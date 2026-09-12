from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services import diagnostics as diag_svc
from app.services import tasks as task_svc
from app.models.task import Task, TaskAttempt, TaskStatus, ExecutionEngine


def test_diagnostics_metadata_is_json_safe_for_uuid() -> None:
    payload = diag_svc.record_error(
        "diag.test",
        "boom",
        metadata={
            "packet_uuid": uuid.uuid4(),
            "nested": {"attempt_uuid": uuid.uuid4()},
        },
    )

    assert isinstance(payload["metadata"]["packet_uuid"], str)
    assert isinstance(payload["metadata"]["nested"]["attempt_uuid"], str)


def test_monitor_and_execution_metrics_accept_sqlite_naive_datetimes() -> None:
    import app.models.action_packet  # noqa: F401
    import app.models.budget  # noqa: F401
    import app.models.task  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        task = Task(task_type="government_portal_search", status=TaskStatus.failed)
        task.created_at = datetime(2026, 9, 12, 12, 0)
        task.failed_at = datetime(2026, 9, 12, 12, 1)
        session.add(task)
        session.commit()
        session.add(TaskAttempt(
            task_id=task.task_id,
            attempt_number=1,
            engine=ExecutionEngine.playwright,
            status="failed",
            started_at=datetime(2026, 9, 12, 12, 0),
            completed_at=datetime(2026, 9, 12, 12, 1),
        ))
        session.commit()

        assert task_svc.get_monitor_data(session)["total_tasks"] == 1
        assert diag_svc.get_execution_metrics(session)["status"] == "ok"
