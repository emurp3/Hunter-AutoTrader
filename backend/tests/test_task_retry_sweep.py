"""
Recovery-board finding: fail_task() is the terminal state the worker
reports for a RetryableExecutionError when attempts remain (distinct from
escalate_task()'s "attempts exhausted, needs Commander" state — see
worker/main.py's _process_task()). Nothing ever called the existing
retry_task() automatically, so a task with budget left simply sat as
"failed" forever. sweep_and_retry_failed_tasks() is the missing link.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.task import Task, TaskStatus
from app.services import tasks as task_svc


def _make_engine():
    import app.models.task  # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.alert  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def _dispatch(session: Session, *, max_attempts: int = 3) -> Task:
    return task_svc.dispatch_task(
        task_type="marketplace_listing",
        spec_payload={},
        session=session,
        source_type="income_source",
        source_id="at:sweep-test",
        max_attempts=max_attempts,
    )


def _claim_and_fail(session: Session, task: Task, *, failed_at: datetime, reason: str = "transient error") -> Task:
    task_svc.claim_task("worker-1", session)
    task_svc.fail_task(task.task_id, reason, session)
    # Backdate failed_at directly — fail_task() always stamps "now", and
    # the sweep's age floor needs a controllable clock in tests.
    row = session.get(Task, task.id)
    row.failed_at = failed_at
    session.add(row)
    session.commit()
    return row


def test_old_enough_failed_task_with_budget_is_retried():
    engine = _make_engine()
    with Session(engine) as session:
        task = _dispatch(session, max_attempts=3)
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        _claim_and_fail(session, task, failed_at=old)

        retried = task_svc.sweep_and_retry_failed_tasks(session, min_age_seconds=300)

        assert len(retried) == 1
        assert retried[0].task_id == task.task_id
        row = session.get(Task, task.id)
        assert row.status == TaskStatus.retrying


def test_freshly_failed_task_is_not_retried_yet():
    engine = _make_engine()
    with Session(engine) as session:
        task = _dispatch(session, max_attempts=3)
        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        _claim_and_fail(session, task, failed_at=recent)

        retried = task_svc.sweep_and_retry_failed_tasks(session, min_age_seconds=300)

        assert retried == []
        row = session.get(Task, task.id)
        assert row.status == TaskStatus.failed


def test_task_with_exhausted_attempts_is_not_retried():
    engine = _make_engine()
    with Session(engine) as session:
        task = _dispatch(session, max_attempts=1)
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        _claim_and_fail(session, task, failed_at=old)
        row = session.get(Task, task.id)
        assert row.attempts >= row.max_attempts

        retried = task_svc.sweep_and_retry_failed_tasks(session, min_age_seconds=300)

        assert retried == []
        row = session.get(Task, task.id)
        assert row.status == TaskStatus.failed


def test_escalated_and_completed_tasks_are_never_touched():
    engine = _make_engine()
    with Session(engine) as session:
        from app.models.task import EscalationType

        escalated = _dispatch(session, max_attempts=3)
        task_svc.claim_task("worker-1", session)
        task_svc.escalate_task(escalated.task_id, EscalationType.unrecoverable_failure, "give up", session)

        completed = _dispatch(session, max_attempts=3)
        task_svc.claim_task("worker-1", session)
        task_svc.complete_task(completed.task_id, {}, session)

        retried = task_svc.sweep_and_retry_failed_tasks(session, min_age_seconds=0)

        assert retried == []
        assert session.get(Task, escalated.id).status == TaskStatus.escalated
        assert session.get(Task, completed.id).status == TaskStatus.completed


def test_sweep_is_idempotent_once_a_task_is_retrying():
    engine = _make_engine()
    with Session(engine) as session:
        task = _dispatch(session, max_attempts=3)
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        _claim_and_fail(session, task, failed_at=old)

        first = task_svc.sweep_and_retry_failed_tasks(session, min_age_seconds=300)
        second = task_svc.sweep_and_retry_failed_tasks(session, min_age_seconds=300)

        assert len(first) == 1
        assert second == []
