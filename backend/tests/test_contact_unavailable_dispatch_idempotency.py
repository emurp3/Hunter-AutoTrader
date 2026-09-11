"""
Commander, 2026-09-11: "Missing contact information should move the
task to an appropriate research/waiting/disposition state — not repeat
execution against unchanged inputs." A task escalated with
EscalationType.contact_unavailable represents a real investigation that
already found no usable contact route for the CURRENT input. A fresh
dispatch_task() call with the identical spec_payload must return that
same escalated task rather than creating (and re-running) a new one;
once the input genuinely changes (e.g. contact enrichment finds
something), a fresh dispatch must be allowed through.
"""

from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.task import EscalationType, Task, TaskStatus
from app.services import tasks as task_svc


def _make_session() -> Session:
    import app.models.alert  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.task  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _escalate_as_contact_unavailable(session: Session, task: Task) -> Task:
    return task_svc.escalate_task(
        task.task_id,
        EscalationType.contact_unavailable,
        "No contact route available for service outreach",
        session,
    )


def test_redispatch_with_unchanged_spec_returns_the_same_escalated_task():
    session = _make_session()
    spec = {"service_outreach": {"contact_email": None, "contact_url": None, "business_type": "dentist"}}
    task = task_svc.dispatch_task(
        task_type="service_outreach",
        spec_payload=spec,
        session=session,
        source_id="local:osm:dentist:1",
        idempotency_key="source:local:osm:dentist:1:service_outreach",
    )
    _escalate_as_contact_unavailable(session, task)

    redispatched = task_svc.dispatch_task(
        task_type="service_outreach",
        spec_payload=spec,
        session=session,
        source_id="local:osm:dentist:1",
        idempotency_key="source:local:osm:dentist:1:service_outreach",
    )

    assert redispatched.task_id == task.task_id
    all_tasks = session.exec(select(Task).where(Task.source_id == "local:osm:dentist:1")).all()
    assert len(all_tasks) == 1  # no duplicate task created


def test_redispatch_with_changed_contact_info_creates_a_fresh_task():
    session = _make_session()
    spec_missing = {"service_outreach": {"contact_email": None, "contact_url": None, "business_type": "dentist"}}
    task = task_svc.dispatch_task(
        task_type="service_outreach",
        spec_payload=spec_missing,
        session=session,
        source_id="local:osm:dentist:2",
        idempotency_key="source:local:osm:dentist:2:service_outreach",
    )
    _escalate_as_contact_unavailable(session, task)

    spec_found = {
        "service_outreach": {
            "contact_email": "front-desk@example.com",
            "contact_url": None,
            "business_type": "dentist",
        }
    }
    redispatched = task_svc.dispatch_task(
        task_type="service_outreach",
        spec_payload=spec_found,
        session=session,
        source_id="local:osm:dentist:2",
        idempotency_key="source:local:osm:dentist:2:service_outreach",
    )

    assert redispatched.task_id != task.task_id
    all_tasks = session.exec(select(Task).where(Task.source_id == "local:osm:dentist:2")).all()
    assert len(all_tasks) == 2


def test_other_escalation_types_are_unaffected_and_still_block_redispatch_normally():
    """Only contact_unavailable gets the unchanged-input check — every
    other escalation_type keeps its prior behavior (escalated tasks
    don't block a fresh dispatch attempt at all, e.g. after Commander
    fixes credentials)."""
    session = _make_session()
    spec = {"service_outreach": {"contact_email": "x@example.com"}}
    task = task_svc.dispatch_task(
        task_type="service_outreach",
        spec_payload=spec,
        session=session,
        source_id="local:osm:dentist:3",
        idempotency_key="source:local:osm:dentist:3:service_outreach",
    )
    task_svc.escalate_task(
        task.task_id, EscalationType.credentials_required, "SMTP not configured", session,
    )

    redispatched = task_svc.dispatch_task(
        task_type="service_outreach",
        spec_payload=spec,
        session=session,
        source_id="local:osm:dentist:3",
        idempotency_key="source:local:osm:dentist:3:service_outreach",
    )

    assert redispatched.task_id != task.task_id
