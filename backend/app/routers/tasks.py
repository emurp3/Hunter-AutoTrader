"""
Task dispatch API.

POST /tasks/dispatch            — create and queue a task
GET  /tasks/pending             — list pending tasks (for worker polling)
POST /tasks/claim               — atomically claim the next task
GET  /tasks/{task_id}           — inspect a task
GET  /tasks/{task_id}/attempts  — attempt history for a task
POST /tasks/{task_id}/heartbeat — extend worker lease
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
from app.models.task import EscalationType, ExecutionEngine, Task, TaskAttempt, TaskStatus
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
