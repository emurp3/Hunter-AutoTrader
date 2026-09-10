from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


class SchedulerRunLock(SQLModel, table=True):
    """Cross-process guard against the same scheduled job firing twice
    during a Render blue-green deploy overlap (the old and new instance
    briefly run at once) — APScheduler's max_instances=1 only protects
    against double-firing within a single process, not across two.

    One row per job_id. A run may proceed only if it can atomically claim
    the row (insert it fresh, or UPDATE it while locked_until is already
    in the past) — see scheduler.py's _try_acquire_run_lock()."""

    job_id: str = Field(primary_key=True)
    locked_until: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
