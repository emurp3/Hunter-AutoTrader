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
from app.models.event import EventType
from app.services import alerts as alert_svc
from app.services import events as event_svc

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

    if source_id:
        try:
            event_svc.log_event(
                source_id, EventType.state_change, session,
                summary=f"Task dispatched: {task_type} (task_id={task.task_id})",
                metadata={"task_id": task.task_id, "task_type": task_type},
            )
        except Exception:
            pass

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
    screenshot_path: Optional[str] = None,
    page_url: Optional[str] = None,
    error_text: Optional[str] = None,
    trace_reference: Optional[str] = None,
    engine: Optional[str] = None,
    worker_id_override: Optional[str] = None,
) -> Task:
    task = _get_task_or_raise(task_id, session)
    now = datetime.now(timezone.utc)

    # Record attempt with artifacts
    _record_terminal_attempt(
        task=task,
        session=session,
        status="completed",
        now=now,
        engine=engine,
        worker_id_override=worker_id_override,
        outcome=outcome,
        screenshot_path=screenshot_path,
        page_url=page_url,
        error_text=error_text,
        trace_reference=trace_reference,
        summary_reason=notes,
    )

    task.status = TaskStatus.completed
    task.outcome = json.dumps(outcome)
    task.outcome_notes = notes
    task.completed_at = now
    task.worker_id = None
    task.lease_expires_at = None
    session.add(task)
    session.commit()
    session.refresh(task)

    if task.source_id:
        event_svc.log_event(
            task.source_id, EventType.state_change, session,
            summary=f"Task completed: {task.task_type} (task_id={task_id})",
            metadata={"task_id": task_id, "outcome": outcome},
        )

    _close_loop(task, outcome, session)
    return task


# ── Escalate ──────────────────────────────────────────────────────────────────

def escalate_task(
    task_id: str,
    escalation_type: EscalationType,
    reason: str,
    session: Session,
    *,
    screenshot_path: Optional[str] = None,
    page_url: Optional[str] = None,
    error_text: Optional[str] = None,
    trace_reference: Optional[str] = None,
    engine: Optional[str] = None,
    worker_id_override: Optional[str] = None,
) -> Task:
    """Hard-stop escalation — raises Commander alert immediately."""
    task = _get_task_or_raise(task_id, session)
    now = datetime.now(timezone.utc)

    _record_terminal_attempt(
        task=task,
        session=session,
        status="escalated",
        now=now,
        engine=engine,
        worker_id_override=worker_id_override,
        screenshot_path=screenshot_path,
        page_url=page_url,
        error_text=error_text,
        trace_reference=trace_reference,
        summary_reason=reason,
        is_escalation=True,
        escalation_type=escalation_type,
    )

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

    if task.source_id:
        event_svc.log_event(
            task.source_id, EventType.alert_raised, session,
            summary=f"Task escalated [{escalation_type}]: {reason[:100]} (task_id={task_id})",
            metadata={"task_id": task_id, "escalation_type": str(escalation_type)},
        )

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

def fail_task(
    task_id: str,
    reason: str,
    session: Session,
    *,
    screenshot_path: Optional[str] = None,
    page_url: Optional[str] = None,
    error_text: Optional[str] = None,
    trace_reference: Optional[str] = None,
    engine: Optional[str] = None,
    worker_id_override: Optional[str] = None,
) -> Task:
    """Max attempts exhausted and no escalation condition applies."""
    task = _get_task_or_raise(task_id, session)
    now = datetime.now(timezone.utc)

    _record_terminal_attempt(
        task=task,
        session=session,
        status="failed",
        now=now,
        engine=engine,
        worker_id_override=worker_id_override,
        screenshot_path=screenshot_path,
        page_url=page_url,
        error_text=error_text or reason,
        trace_reference=trace_reference,
        summary_reason=reason,
    )

    task.status = TaskStatus.failed
    task.outcome_notes = reason
    task.failed_at = now
    task.worker_id = None
    task.lease_expires_at = None
    session.add(task)
    session.commit()
    session.refresh(task)

    if task.source_id:
        event_svc.log_event(
            task.source_id, EventType.alert_raised, session,
            summary=f"Task failed after {task.attempts} attempts: {reason[:100]} (task_id={task_id})",
            metadata={"task_id": task_id, "attempts": task.attempts},
        )

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
    "service": "service_outreach",
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
        import re as _re

        def _note_field(notes: str | None, key: str) -> str | None:
            if not notes:
                return None
            m = _re.search(rf'\b{_re.escape(key)}:\s*([^|]+)', notes)
            return m.group(1).strip() if m else None

        _notes = source.notes or ""
        _listing_price_str = _note_field(_notes, "listing_price")
        _listing_title = _note_field(_notes, "listing_title") or source.description[:80]
        _cost_basis_str = _note_field(_notes, "cost_basis")
        _fb_category = _note_field(_notes, "fb_category") or source.category or "General"

        try:
            _listing_price = float(_listing_price_str)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # Fallback: infer listing price assuming ~30% margin
            _listing_price = round((source.estimated_profit or 0) / 0.30, 0)

        try:
            _cost_basis = float(_cost_basis_str) if _cost_basis_str else None  # type: ignore[arg-type]
        except (TypeError, ValueError):
            _cost_basis = None

        spec["platform"] = "facebook_marketplace"
        spec["listing"] = {
            "title": _listing_title,
            "listing_price": _listing_price,   # actual sale price — used by MarketplaceListingSkill
            "price": _listing_price,            # alias — fill_details reads this
            "cost_basis": _cost_basis,          # acquisition cost (informational)
            "estimated_profit": source.estimated_profit,  # expected margin
            "category": _fb_category,
            "description": source.notes or source.description,
        }

    if task_type == "service_outreach":
        import re as _sre
        import os as _sos

        def _sf(notes: str | None, key: str) -> str | None:
            if not notes:
                return None
            m = _sre.search(rf'\b{_sre.escape(key)}:\s*([^|]+)', notes)
            return m.group(1).strip() if m else None

        _notes = source.notes or ""
        _next = source.next_action or ""
        # Extract quoted search term from next_action (e.g. "church near me")
        _sq_match = _sre.search(r'["\']([^"\']{5,60})["\']', _next)
        _search_query = _sq_match.group(1) if _sq_match else (source.description or "")[:50]

        spec["service_outreach"] = {
            "business_type": _sf(_notes, "target_buyer") or source.category,
            "execution_path": _sf(_notes, "execution_path") or "local_pitch",
            "contact_url": _sf(_notes, "contact_url"),
            "contact_email": _sf(_notes, "contact_email"),
            "search_query": _search_query,
            "location": _sos.getenv("HUNTER_SERVICE_LOCATION", ""),
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


def _record_terminal_attempt(
    *,
    task: Task,
    session: Session,
    status: str,
    now: datetime,
    engine: Optional[str] = None,
    worker_id_override: Optional[str] = None,
    outcome: Optional[dict[str, Any]] = None,
    screenshot_path: Optional[str] = None,
    page_url: Optional[str] = None,
    error_text: Optional[str] = None,
    trace_reference: Optional[str] = None,
    summary_reason: Optional[str] = None,
    is_escalation: bool = False,
    escalation_type: Optional[EscalationType] = None,
) -> TaskAttempt:
    """Create a closed TaskAttempt record for a terminal transition."""
    try:
        eng = ExecutionEngine(engine) if engine else ExecutionEngine.playwright
    except ValueError:
        eng = ExecutionEngine.playwright

    attempt = TaskAttempt(
        task_id=task.task_id,
        attempt_number=task.attempts,
        engine=eng,
        worker_id=worker_id_override or task.worker_id or "",
        status=status,
        started_at=getattr(task, "executing_at", None) or now,
        completed_at=now,
        outcome=json.dumps(outcome) if outcome else None,
        screenshot_path=screenshot_path,
        page_url=page_url,
        error_text=error_text,
        summary_reason=summary_reason,
        trace_reference=trace_reference,
        is_escalation=is_escalation,
        escalation_type=escalation_type,
    )
    session.add(attempt)
    session.flush()  # persist without outer commit — outer caller commits
    return attempt


def _close_loop(task: Task, outcome: dict[str, Any], session: Session) -> None:
    """Advance source status, advance linked packet to active, and raise execution_completed alert."""
    now = datetime.now(timezone.utc)
    # Advance source to active when task succeeds and source is in a pre-active state
    if task.source_id:
        try:
            from app.models.income_source import IncomeSource, SourceStatus
            source = session.exec(
                select(IncomeSource).where(IncomeSource.source_id == task.source_id)
            ).first()
            if source and source.status in (
                SourceStatus.review_ready,
                SourceStatus.budgeted,
                SourceStatus.scored,
                SourceStatus.prioritized,
            ):
                old_status = source.status
                source.status = SourceStatus.active
                session.add(source)
                session.commit()
                event_svc.log_event(
                    task.source_id, EventType.state_change, session,
                    old_state=old_status,
                    new_state=SourceStatus.active,
                    summary=f"Source activated via task completion: {task.task_type}",
                )
        except Exception:
            pass  # Source advancement must not block alert

    # Advance the source's most-recent planned packet to active so the dashboard
    # reflects real in-flight work. Does not touch realized profit.
    if task.source_id:
        try:
            from app.models.action_packet import ActionPacket, ExecutionState
            packet = session.exec(
                select(ActionPacket)
                .where(
                    ActionPacket.source_id == task.source_id,
                    ActionPacket.execution_state == ExecutionState.planned,
                )
                .order_by(ActionPacket.created_at.desc())
            ).first()
            if packet:
                packet.execution_state = ExecutionState.active
                packet.execution_started_at = now
                session.add(packet)
                session.commit()
        except Exception:
            pass  # Packet advancement must not block alert

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


# ── Monitor ───────────────────────────────────────────────────────────────────

def get_monitor_data(session: Session) -> dict:
    """
    Queue depth, task counts by status, attempts by engine (24h),
    recent failures and escalations (24h). Used by GET /tasks/monitor.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    all_tasks = session.exec(select(Task)).all()
    by_status: dict[str, int] = {}
    for t in all_tasks:
        key = t.status.value if hasattr(t.status, "value") else str(t.status)
        by_status[key] = by_status.get(key, 0) + 1

    queue_depth = by_status.get("dispatched", 0) + by_status.get("retrying", 0)

    recent_attempts = session.exec(select(TaskAttempt)).all()
    by_engine: dict[str, int] = {}
    for a in recent_attempts:
        if a.started_at and a.started_at >= cutoff:
            key = a.engine.value if hasattr(a.engine, "value") else str(a.engine)
            by_engine[key] = by_engine.get(key, 0) + 1

    recent_failures = [
        t for t in all_tasks
        if t.status == TaskStatus.failed
        and t.failed_at
        and t.failed_at >= cutoff
    ]
    recent_failures.sort(key=lambda t: t.failed_at, reverse=True)

    recent_escalations = [
        t for t in all_tasks
        if t.status == TaskStatus.escalated
        and t.escalated_at
        and t.escalated_at >= cutoff
    ]
    recent_escalations.sort(key=lambda t: t.escalated_at, reverse=True)

    return {
        "queue_depth": queue_depth,
        "by_status": by_status,
        "total_tasks": len(all_tasks),
        "attempts_by_engine_24h": by_engine,
        "recent_failures": [
            {
                "task_id": t.task_id,
                "task_type": t.task_type,
                "source_id": t.source_id,
                "failed_at": t.failed_at.isoformat() if t.failed_at else None,
                "outcome_notes": t.outcome_notes,
                "attempts": t.attempts,
            }
            for t in recent_failures[:10]
        ],
        "recent_escalations": [
            {
                "task_id": t.task_id,
                "task_type": t.task_type,
                "source_id": t.source_id,
                "escalated_at": t.escalated_at.isoformat() if t.escalated_at else None,
                "escalation_type": str(t.escalation_type) if t.escalation_type else None,
                "escalation_reason": t.escalation_reason,
            }
            for t in recent_escalations[:10]
        ],
    }
