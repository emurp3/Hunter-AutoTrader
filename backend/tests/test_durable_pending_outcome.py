"""
Commander, 2026-09-11: "finish durable protection against repeating a
successful or uncertain send across worker restarts."

The worker's in-process _reported_outcome_cache (app/worker/main.py) only
protects a reclaim that lands on the SAME worker process — a restart
(crash, redeploy, OOM kill) loses it entirely. This covers the durable,
server-side half: record_pending_outcome() persists the real outcome to
the Task row itself, and claim_task() must finalize from that record
rather than ever handing such a task back out for re-execution.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.task import Task, TaskStatus
from app.services import tasks as task_svc


def _make_session() -> Session:
    import app.models.alert  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.task  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_record_pending_outcome_does_not_change_status():
    session = _make_session()
    task = task_svc.dispatch_task("service_outreach", {"x": 1}, session)
    task_svc.claim_task("worker-1", session)

    task_svc.record_pending_outcome(
        task.task_id, session, outcome={"email_sent": True}, notes="sent", worker_id="worker-1",
    )

    row = session.exec(select(Task).where(Task.task_id == task.task_id)).first()
    assert row.status == TaskStatus.executing
    assert row.pending_outcome_json is not None


def test_claim_task_finalizes_a_pending_outcome_instead_of_redispatching():
    """The core scenario: execute_task() succeeded (outcome recorded),
    then the worker process is lost before /complete ever lands — the
    task sits executing with an expired lease. A new worker's claim_task
    call must NOT get this task handed back for re-execution; it must be
    silently finalized from the recorded outcome, and the caller gets the
    NEXT real task instead."""
    session = _make_session()
    task = task_svc.dispatch_task("service_outreach", {"x": 1}, session)
    task_svc.claim_task("worker-1", session)
    task_svc.record_pending_outcome(
        task.task_id, session, outcome={"email_sent": True}, notes="sent", worker_id="worker-1",
    )

    # Simulate the lease expiring (worker-1 is gone for good).
    row = session.exec(select(Task).where(Task.task_id == task.task_id)).first()
    row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.add(row)
    session.commit()

    # A second, unrelated task is also waiting.
    other = task_svc.dispatch_task("service_outreach", {"x": 2}, session)

    claimed = task_svc.claim_task("worker-2", session)

    assert claimed is not None
    assert claimed.task_id == other.task_id  # never re-handed the finalized task

    row = session.exec(select(Task).where(Task.task_id == task.task_id)).first()
    assert row.status == TaskStatus.completed
    assert row.pending_outcome_json is None
    assert json.loads(row.outcome)["email_sent"] is True


def test_complete_clears_pending_outcome():
    session = _make_session()
    task = task_svc.dispatch_task("service_outreach", {"x": 1}, session)
    task_svc.claim_task("worker-1", session)
    task_svc.record_pending_outcome(task.task_id, session, outcome={"email_sent": True}, worker_id="worker-1")

    task_svc.complete_task(task.task_id, {"email_sent": True}, session, worker_id_override="worker-1")

    row = session.exec(select(Task).where(Task.task_id == task.task_id)).first()
    assert row.pending_outcome_json is None
    assert row.pending_outcome_recorded_at is None


def test_record_pending_outcome_never_overwrites_a_different_owner():
    """Ownership guard: a stale record_pending_outcome call (e.g. a slow
    retry arriving after the task has already moved on to a different
    worker) must never clobber current state."""
    session = _make_session()
    task = task_svc.dispatch_task("service_outreach", {"x": 1}, session)
    task_svc.claim_task("worker-1", session)

    row = session.exec(select(Task).where(Task.task_id == task.task_id)).first()
    row.worker_id = "worker-2"
    session.add(row)
    session.commit()

    task_svc.record_pending_outcome(
        task.task_id, session, outcome={"email_sent": True}, worker_id="worker-1",
    )
    row = session.exec(select(Task).where(Task.task_id == task.task_id)).first()
    assert row.pending_outcome_json is None
