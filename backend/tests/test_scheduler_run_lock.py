"""
Recovery-board finding (Commander, 2026-09-10): APScheduler's
max_instances=1 only prevents the same job from double-firing WITHIN one
process — it does nothing during a Render blue-green deploy overlap,
where the old and new instance briefly both run their own in-memory
schedulers, and next_run_time=now (the immediate-catch-up fix) makes it
more likely both fire at once. _try_acquire_run_lock() is a real,
DB-backed cross-process mutex: exactly one concurrent caller may ever
win it for a given job_id within the hold window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.scheduler_lock import SchedulerRunLock
from app.services.scheduler import _try_acquire_run_lock


def _make_engine():
    import app.models.scheduler_lock  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def test_first_caller_wins_the_lock():
    engine = _make_engine()
    with Session(engine) as session:
        assert _try_acquire_run_lock(session, "job-a", hold_seconds=90) is True


def test_second_immediate_caller_is_blocked():
    """Simulates two overlapping process instances (or two calls within
    the same process) both trying to run the same job at once."""
    engine = _make_engine()
    with Session(engine) as first_session, Session(engine) as second_session:
        assert _try_acquire_run_lock(first_session, "job-a", hold_seconds=90) is True
        assert _try_acquire_run_lock(second_session, "job-a", hold_seconds=90) is False


def test_lock_is_released_after_hold_window_expires():
    engine = _make_engine()
    with Session(engine) as session:
        assert _try_acquire_run_lock(session, "job-a", hold_seconds=90) is True

        # Simulate the hold window having already passed.
        row = session.get(SchedulerRunLock, "job-a")
        row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(row)
        session.commit()

        assert _try_acquire_run_lock(session, "job-a", hold_seconds=90) is True


def test_different_job_ids_do_not_contend():
    engine = _make_engine()
    with Session(engine) as session:
        assert _try_acquire_run_lock(session, "job-a", hold_seconds=90) is True
        assert _try_acquire_run_lock(session, "job-b", hold_seconds=90) is True


def test_still_locked_caller_is_blocked_mid_window():
    engine = _make_engine()
    with Session(engine) as session:
        assert _try_acquire_run_lock(session, "job-a", hold_seconds=90) is True
        # Same job, well within the hold window — must still be blocked.
        assert _try_acquire_run_lock(session, "job-a", hold_seconds=90) is False
