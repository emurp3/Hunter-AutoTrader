"""
Task service — dispatch, claim, heartbeat, complete, escalate, fail, retry.

Hunter dispatches Tasks for interface-bound work. The assistant worker
polls /tasks/pending, atomically claims tasks with a worker lease,
executes via Playwright (primary) or Claude CU (fallback), and reports
outcomes back. Hunter closes the loop into ledger/strategy/budget.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlmodel import Session, select

from app.models.task import (
    EscalationType,
    ExecutionEngine,
    Task,
    TaskAttempt,
    TaskStatus,
)
from app.models.alert import AlertPriority, AlertType
from app.services import alerts as alert_svc

# Worker must heartbeat within this window or the task is re-claimable.
_LEASE_SECONDS = 120


# ── Dispatch ──────────────────────────────────────────────────────────────────

def dispatch_task(
    task_type: str,
    spec_payload: dict[str, Any],
    session: Session,
    *,
    source_type: str = "income_source",
    source_id: Optional[str] = None,
    packet_id: Optional[int] = None,
    strategy_id: Optional[str] = None,
    priority: int = 5,
    preferred_engine: ExecutionEngine = ExecutionEngine.playwright,
    allowed_engines: list[str] | None = None,
    success_criteria: dict[str, Any] | None = None,
    escalate_rules: dict[str, Any] | None = None,
    idempotency_key: str = "",
    max_attempts: int = 3,
) -> Task:
    """
    Create and dispatch a task.

    Idempotent: if idempotency_key is given and a non-terminal task already
    exists with that key, the existing task is returned unchanged.
    """
    if idempotency_key:
        existing = session.exec(
            select(Task).where(
                Task.idempotency_key == idempotency_key,
                Task.status.not_in([TaskStatus.failed, TaskStatus.escalated]),
            )
        ).first()
        if existing:
            return existing

    task = Task(
        task_type=task_type,
        source_type=source_type,
        source_id=source_id,
        packet_id=packet_id,
        strategy_id=strategy_id,
        priority=priority,
        preferred_engine=preferred_engine,
        allowed_engines=json.dumps(allowed_engines or ["playwright", "claude_cu"]),
        spec_payload=json.dumps(spec_payload),
        success_criteria=json.dumps(success_criteria or {}),
        escalate_rules=json.dumps(escalate_rules or {}),
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        status=TaskStatus.dispatched,
        dispatched_at=datetime.now(timezone.utc),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


# ── Claim ─────────────────────────────────────────────────────────────────────

def claim_task(worker_id: str, session: Session) -> Optional[Task]:
    """
    Atomically claim the highest-priority available task for this worker.

    Eligible tasks:
    - status=dispatched or status=retrying (not yet claimed)
    - status=executing with an expired lease (abandoned by prior worker)

    Returns None when no tasks are available.
    """
    now = datetime.now(timezone.utc)
    lease_expiry = now + timedelta(seconds=_LEASE_SECONDS)

    stmt = (
        select(Task)
        .where(
            (Task.status.in_([TaskStatus.dispatched, TaskStatus.retrying]))
            | (
                (Task.status == TaskStatus.executing)
                & (Task.lease_expires_at < now)
            )
        )
        .order_by(Task.priority.desc(), Task.created_at.asc())
        .limit(1)
    )
    task = session.exec(stmt).first()
    if not task:
        return None

    task.status = TaskStatus.executing
    task.worker_id = worker_id
    task.lease_expires_at = lease_expiry
    task.last_heartbeat_at = now
    task.executing_at = now
    task.attempts += 1
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def heartbeat(task_id: str, worker_id: str, session: Session) -> bool:
    """Extend the lease. Returns False if this worker no longer owns the task."""
    task = session.exec(select(Task).where(Task.task_id == task_id)).first()
    if not task or task.worker_id != worker_id:
        return False
    now = datetime.now(timezone.utc)
    task.last_heartbeat_at = now
    task.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
    session.add(task)
    session.commit()
    return True


# ── Complete ──────────────────────────────────────────────────────────────────

def complete_task(
    task_id: str,
    outcome: dict[str, Any],
    session: Session,
    *,
    notes: str = "",
) -> Task:
    task = _get_task_or_raise(task_id, session)
    now = datetime.now(timezone.utc)
    task.status = TaskStatus.completed
    task.outcome = json.dumps(outcome)
    task.outcome_notes = notes
    task.completed_at = now
    task.worker_id = None
    task.lease_expires_at = None
    session.add(task)
    session.commit()
    session.refresh(task)
    _close_loop(task, outcome, session)
    return task


# ── Escalate ──────────────────────────────────────────────────────────────────

def escalate_task(
    task_id: str,
    escalation_type: EscalationType,
    reason: str,
    session: Session,
) -> Task:
    """Hard-stop escalation — raises Commander alert immediately."""
    task = _get_task_or_raise(task_id, session)
    now = datetime.now(timezone.utc)
    task.status = TaskStatus.escalated
    task.must_escalate = True
    task.escalation_type = escalation_type
    task.escalation_reason = reason
    task.escalated_at = now
    task.worker_id = None
    task.lease_expires_at = None
    session.add(task)
    session.commit()
    session.refresh(task)

    alert_svc.raise_alert(
        alert_type=AlertType.review_required,
        title=f"Task Escalated — {task.task_type} [{escalation_type}]",
        body=f"task_id={task_id} | source_id={task.source_id} | reason={reason}",
        session=session,
        priority=AlertPriority.high,
        source_id=task.source_id,
    )
    return task


# ── Fail ──────────────────────────────────────────────────────────────────────

def fail_task(task_id: str, reason: str, session: Session) -> Task:
    """Max attempts exhausted and no escalation condition applies."""
    task = _get_task_or_raise(task_id, session)
    now = datetime.now(timezone.utc)
    task.status = TaskStatus.failed
    task.outcome_notes = reason
    task.failed_at = now
    task.worker_id = None
    task.lease_expires_at = None
    session.add(task)
    session.commit()
    session.refresh(task)

    alert_svc.raise_alert(
        alert_type=AlertType.execution_failed,
        title=f"Task Failed — {task.task_type} (exhausted {task.attempts} attempts)",
        body=f"task_id={task_id} | source_id={task.source_id} | reason={reason}",
        session=session,
        priority=AlertPriority.medium,
        source_id=task.source_id,
    )
    return task


# ── Retry ─────────────────────────────────────────────────────────────────────

def retry_task(task_id: str, session: Session) -> Task:
    """Re-queue a failed/executing task. Raises if max_attempts already exhausted."""
    task = _get_task_or_raise(task_id, session)
    if task.attempts >= task.max_attempts:
        raise ValueError(
            f"Task {task_id} has used {task.attempts}/{task.max_attempts} attempts. "
            "Escalate instead of retrying."
        )
    task.status = TaskStatus.retrying
    task.worker_id = None
    task.lease_expires_at = None
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


# ── Attempt logging ───────────────────────────────────────────────────────────

def record_attempt(
    task_id: str,
    engine: ExecutionEngine,
    worker_id: str,
    session: Session,
) -> TaskAttempt:
    task = _get_task_or_raise(task_id, session)
    attempt = TaskAttempt(
        task_id=task_id,
        attempt_number=task.attempts,
        engine=engine,
        worker_id=worker_id,
        status="started",
        started_at=datetime.now(timezone.utc),
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def close_attempt(
    attempt_id: int,
    status: str,
    session: Session,
    *,
    outcome: dict[str, Any] | None = None,
    screenshot_path: str | None = None,
    page_url: str | None = None,
    error_text: str | None = None,
    error_message: str | None = None,
    summary_reason: str | None = None,
    trace_reference: str | None = None,
    is_escalation: bool = False,
    escalation_type: EscalationType | None = None,
) -> TaskAttempt:
    attempt = session.get(TaskAttempt, attempt_id)
    if not attempt:
        raise ValueError(f"TaskAttempt {attempt_id} not found")
    attempt.status = status
    attempt.completed_at = datetime.now(timezone.utc)
    if outcome is not None:
        attempt.outcome = json.dumps(outcome)
    attempt.screenshot_path = screenshot_path
    attempt.page_url = page_url
    attempt.error_text = error_text
    attempt.error_message = error_message
    attempt.summary_reason = summary_reason
    attempt.trace_reference = trace_reference
    attempt.is_escalation = is_escalation
    attempt.escalation_type = escalation_type
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


# ── Task type resolution ──────────────────────────────────────────────────────

_ORIGIN_TO_TASK_TYPE: dict[str, str] = {
    "marketplace_scanner": "marketplace_listing",
    "gig_scanner": "gig_application",
    "github_scanner": "github_bounty",
    "rfp_scanner": "rfp_response",
    "affiliate_scanner": "affiliate_signup",
    "social_listener": "social_outreach",
    "local_business_prospector": "local_outreach",
    "digital_product_scanner": "digital_product_launch",
}

_CATEGORY_TO_TASK_TYPE: dict[str, str] = {
    "marketplace": "marketplace_listing",
    "gig": "gig_application",
    "github": "github_bounty",
    "rfp": "rfp_response",
    "affiliate": "affiliate_signup",
    "social": "social_outreach",
    "local": "local_outreach",
    "digital": "digital_product_launch",
}


def resolve_task_type(source) -> str:
    """Map an IncomeSource to the appropriate task_type string."""
    if source.origin_module:
        t = _ORIGIN_TO_TASK_TYPE.get(source.origin_module)
        if t:
            return t
    if source.category:
        t = _CATEGORY_TO_TASK_TYPE.get(source.category.lower())
        if t:
            return t
    return "generic_execution"


# ── Auto-dispatch (called from orchestrator) ──────────────────────────────────

def auto_dispatch_for_source(source_id: str, session: Session) -> Optional[Task]:
    """
    Build and dispatch the appropriate task for an income source.
    Called from the orchestrator after packet + decision complete.
    Returns None if source is missing or dispatch is skipped (idempotent).
    """
    from app.models.income_source import IncomeSource

    source = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not source:
        return None

    task_type = resolve_task_type(source)
    idem_key = f"source:{source_id}:{task_type}"

    spec: dict[str, Any] = {
        "source_id": source.source_id,
        "description": source.description,
        "estimated_profit": source.estimated_profit,
        "currency": source.currency,
        "next_action": source.next_action,
        "notes": source.notes,
        "origin_module": source.origin_module,
        "category": source.category,
        "confidence": source.confidence,
        "task_type": task_type,
    }

    if task_type == "marketplace_listing":
        spec["platform"] = "facebook_marketplace"
        spec["listing"] = {
            "title": source.description,
            "price": source.estimated_profit,
            "category": source.category or "General",
            "description": source.notes or source.description,
        }

    band_priority = {"elite": 15, "high": 10, "medium": 5, "low": 3}
    priority = band_priority.get(source.priority_band or "medium", 5)

    return dispatch_task(
        task_type=task_type,
        spec_payload=spec,
        session=session,
        source_type="income_source",
        source_id=source_id,
        priority=priority,
        preferred_engine=ExecutionEngine.playwright,
        allowed_engines=["playwright", "claude_cu"],
        idempotency_key=idem_key,
        max_attempts=3,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_task_or_raise(task_id: str, session: Session) -> Task:
    task = session.exec(select(Task).where(Task.task_id == task_id)).first()
    if not task:
        raise ValueError(f"Task not found: {task_id}")
    return task


def _close_loop(task: Task, outcome: dict[str, Any], session: Session) -> None:
    """Raises execution_completed alert and links back into the audit trail."""
    alert_svc.raise_alert(
        alert_type=AlertType.execution_completed,
        title=f"Task Completed — {task.task_type}",
        body=(
            f"task_id={task.task_id} | source_id={task.source_id} | "
            f"outcome={json.dumps(outcome)[:200]}"
        ),
        session=session,
        priority=AlertPriority.medium,
        source_id=task.source_id,
    )
