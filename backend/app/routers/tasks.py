"""
Task dispatch API.

POST /tasks/dispatch            — create and queue a task
GET  /tasks/pending             — list pending tasks (for worker polling)
POST /tasks/claim               — atomically claim the next task
GET  /tasks/{task_id}           — inspect a task
GET  /tasks/{task_id}/attempts  — attempt history for a task
POST /tasks/{task_id}/heartbeat — extend worker lease
POST /tasks/{task_id}/record-outcome — durably record a real outcome before the terminal report (restart-safe)
POST /tasks/{task_id}/complete  — record success outcome
POST /tasks/{task_id}/escalate  — hard-stop escalation
POST /tasks/{task_id}/fail      — mark as failed (exhausted)
POST /tasks/{task_id}/retry     — re-queue a failed task
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database.config import get_session
from app.auth.jwt import require_worker
from app.models.task import EscalationType, ExecutionEngine, Task, TaskAttempt, TaskStatus, VerificationReceipt
from app.services import tasks as task_svc

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── Request schemas ───────────────────────────────────────────────────────────

class DispatchRequest(BaseModel):
    task_type: str
    spec_payload: dict[str, Any] = {}
    source_type: str = "income_source"
    source_id: Optional[str] = None
    packet_id: Optional[int] = None
    strategy_id: Optional[str] = None
    priority: int = 5
    preferred_engine: ExecutionEngine = ExecutionEngine.playwright
    allowed_engines: list[str] = ["playwright", "claude_cu"]
    success_criteria: dict[str, Any] = {}
    escalate_rules: dict[str, Any] = {}
    idempotency_key: str = ""
    max_attempts: int = 3


class ClaimRequest(BaseModel):
    worker_id: str


class HeartbeatRequest(BaseModel):
    worker_id: str


class CompleteRequest(BaseModel):
    worker_id: str
    outcome: dict[str, Any] = {}
    notes: str = ""
    screenshot_path: Optional[str] = None
    page_url: Optional[str] = None
    trace_reference: Optional[str] = None
    engine: Optional[str] = None


class RecordOutcomeRequest(BaseModel):
    worker_id: str
    outcome: dict[str, Any] = {}
    notes: str = ""
    screenshot_path: Optional[str] = None
    page_url: Optional[str] = None
    trace_reference: Optional[str] = None
    engine: Optional[str] = None


class BeginOutreachRequest(BaseModel):
    worker_id: str
    attempt_number: int
    intent: dict[str, Any]


class EscalateRequest(BaseModel):
    worker_id: str
    escalation_type: EscalationType
    reason: str
    screenshot_path: Optional[str] = None
    page_url: Optional[str] = None
    error_text: Optional[str] = None
    trace_reference: Optional[str] = None
    engine: Optional[str] = None


class FailRequest(BaseModel):
    worker_id: str
    reason: str
    screenshot_path: Optional[str] = None
    page_url: Optional[str] = None
    error_text: Optional[str] = None
    trace_reference: Optional[str] = None
    engine: Optional[str] = None

class VerificationRequest(BaseModel):
    worker_id: str
    url: str
    challenge_type: str
    attempted_recovery_paths: list[str] = []
    required_human_action: str
    resume_checkpoint: dict[str, Any] = {}
    real: bool = True
    solver_provider: Optional[str] = None
    solver_enabled: bool = False
    solver_configured: bool = False
    challenge_family: Optional[str] = None
    solver_request_id: Optional[str] = None
    solver_attempt_count: int = 0
    solver_result: Optional[str] = None
    verification_accepted: bool = False
    fallback_reason: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/dispatch", status_code=201)
def dispatch(body: DispatchRequest, session: Session = Depends(get_session)):
    """Create and queue a task for execution."""
    task = task_svc.dispatch_task(
        task_type=body.task_type,
        spec_payload=body.spec_payload,
        session=session,
        source_type=body.source_type,
        source_id=body.source_id,
        packet_id=body.packet_id,
        strategy_id=body.strategy_id,
        priority=body.priority,
        preferred_engine=body.preferred_engine,
        allowed_engines=body.allowed_engines,
        success_criteria=body.success_criteria,
        escalate_rules=body.escalate_rules,
        idempotency_key=body.idempotency_key,
        max_attempts=body.max_attempts,
    )
    return task


@router.get("/pending")
def list_pending(limit: int = 50, session: Session = Depends(get_session)):
    """List pending tasks (dispatched or retrying), highest priority first."""
    stmt = (
        select(Task)
        .where(Task.status.in_([TaskStatus.dispatched, TaskStatus.retrying]))
        .order_by(Task.priority.desc(), Task.created_at.asc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


@router.post("/claim")
def claim(body: ClaimRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Atomically claim the next available task. Returns null if queue is empty."""
    task = task_svc.claim_task(body.worker_id, session)
    if not task:
        return {"task": None, "message": "No tasks available"}
    return {"task": task}


@router.get("/monitor")
def monitor(session: Session = Depends(get_session)):
    """
    Queue depth, tasks by status, attempts by engine (24h),
    recent failures and escalations (24h).
    """
    return task_svc.get_monitor_data(session)


@router.get("/{task_id}")
def get_task(task_id: str, session: Session = Depends(get_session)):
    """Inspect a task by task_id."""
    task = session.exec(select(Task).where(Task.task_id == task_id)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


@router.get("/{task_id}/attempts")
def get_attempts(task_id: str, session: Session = Depends(get_session)):
    """Return all execution attempts for a task, newest first."""
    stmt = (
        select(TaskAttempt)
        .where(TaskAttempt.task_id == task_id)
        .order_by(TaskAttempt.started_at.desc())
    )
    return list(session.exec(stmt).all())


@router.post("/{task_id}/heartbeat")
def heartbeat(task_id: str, body: HeartbeatRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Extend the worker lease. Call every 60s while executing."""
    ok = task_svc.heartbeat(task_id, body.worker_id, session)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Heartbeat rejected — task no longer owned by this worker",
        )
    return {"status": "ok", "task_id": task_id}


@router.post("/{task_id}/record-outcome")
def record_outcome(task_id: str, body: RecordOutcomeRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Durably record a real outcome before the terminal /complete report
    is attempted (Commander, 2026-09-11: duplicate-send protection that
    survives a worker restart, not just the in-process case). Does not
    close the task — /complete (or /fail, /escalate) still must follow;
    this only ensures a lost worker process can't cause the SAME work to
    be re-executed on reclaim."""
    try:
        task = task_svc.record_pending_outcome(
            task_id,
            session,
            outcome=body.outcome,
            notes=body.notes,
            screenshot_path=body.screenshot_path,
            page_url=body.page_url,
            trace_reference=body.trace_reference,
            engine=body.engine,
            worker_id=body.worker_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task


@router.post("/{task_id}/begin-outreach")
def begin_outreach(task_id: str, body: BeginOutreachRequest,
                   session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    try:
        task = task_svc.begin_outreach(task_id, body.worker_id, body.attempt_number, body.intent, session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"permitted": True, "task_id": task.task_id}


@router.post("/{task_id}/complete")
def complete(task_id: str, body: CompleteRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Record a successful outcome and close the task."""
    try:
        task = task_svc.complete_task(
            task_id,
            body.outcome,
            session,
            notes=body.notes,
            screenshot_path=body.screenshot_path,
            page_url=body.page_url,
            trace_reference=body.trace_reference,
            engine=body.engine,
            worker_id_override=body.worker_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task


@router.post("/{task_id}/escalate")
def escalate(task_id: str, body: EscalateRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Hard-stop escalation. Raises a Commander alert immediately."""
    try:
        task = task_svc.escalate_task(
            task_id,
            body.escalation_type,
            body.reason,
            session,
            screenshot_path=body.screenshot_path,
            page_url=body.page_url,
            error_text=body.error_text,
            trace_reference=body.trace_reference,
            engine=body.engine,
            worker_id_override=body.worker_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task

@router.post("/{task_id}/verification-checkpoint")
def verification_checkpoint(task_id: str, body: VerificationRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Route real access friction without terminating the objective."""
    from app.services.verification import await_human
    try:
        return await_human(session, task_id, url=body.url, challenge_type=body.challenge_type,
            attempted_paths=body.attempted_recovery_paths,
            required_human_action=body.required_human_action, checkpoint=body.resume_checkpoint)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

@router.get("/{task_id}/verification-receipts")
def verification_receipts(task_id: str, session: Session = Depends(get_session)):
    return session.exec(select(VerificationReceipt).where(VerificationReceipt.task_id == task_id).order_by(VerificationReceipt.timestamp)).all()

@router.post("/{task_id}/verification-event")
def verification_event(task_id: str, body: VerificationRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    from app.services.verification import ACTIVE, record_receipt
    task = session.exec(select(Task).where(Task.task_id == task_id)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    record_receipt(session, task, url=body.url, challenge_type=body.challenge_type,
        real=body.real, attempted_paths=body.attempted_recovery_paths,
        state=ACTIVE, checkpoint=body.resume_checkpoint,
        solver=body.model_dump(include={"solver_provider","solver_enabled","solver_configured","challenge_family","solver_request_id","solver_attempt_count","solver_result","verification_accepted","fallback_reason"}))
    session.commit()
    return {"recorded": True, "state": ACTIVE}

@router.get("/captcha/provider-health")
def captcha_provider_health(live: bool = False, session: Session = Depends(get_session)):
    from app.worker.captcha_provider import TwoCaptchaProvider
    status = TwoCaptchaProvider().provider_health(live=live)
    last = session.exec(select(VerificationReceipt).where(
        VerificationReceipt.verification_accepted == True
    ).order_by(VerificationReceipt.timestamp.desc())).first()
    status["last_end_to_end_success"] = last.timestamp.isoformat() if last else None
    status["operational"] = bool(status["enabled"] and status["credentials"] == "configured"
                                 and status["api_reachable"] is True and last)
    if status["operational"]:
        status["status"] = "OPERATIONAL"
    return status


@router.post("/{task_id}/fail")
def fail(task_id: str, body: FailRequest, session: Session = Depends(get_session), _w: dict = Depends(require_worker)):
    """Mark task as permanently failed (attempts exhausted, no escalation condition)."""
    try:
        task = task_svc.fail_task(
            task_id,
            body.reason,
            session,
            screenshot_path=body.screenshot_path,
            page_url=body.page_url,
            error_text=body.error_text,
            trace_reference=body.trace_reference,
            engine=body.engine,
            worker_id_override=body.worker_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return task


@router.post("/{task_id}/retry")
def retry(task_id: str, session: Session = Depends(get_session)):
    """Re-queue a failed task for another attempt."""
    try:
        task = task_svc.retry_task(task_id, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return task
