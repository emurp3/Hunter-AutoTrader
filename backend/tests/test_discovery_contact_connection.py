"""
Track B (Commander, 2026-09-10): "determine whether the discovery/
research pipeline obtains and passes legitimate contact info, repairing
that connection if missing."

Root cause traced: local_business_prospector.normalize() read
email/website straight from OpenStreetMap tags but never stored them
anywhere retrievable; source_acquisition._persist_results() built
IncomeSource.notes from only source_url/lane/signal_type, ignoring
item.metadata entirely; tasks.py's service_outreach spec builder reads
contact_email/contact_url back out of notes via a "key: value" regex.
Net effect: every service_outreach task's contact_email/contact_url
was always None, so _execute_service_outreach always raised "No
contact route available" — the SMTP send path (verified separately in
test_service_outreach_send.py) was correctly built but unreachable.

These tests cover the actual discovery -> notes -> task spec chain,
which was untested before (existing send tests hand-built the spec
directly, skipping this connection).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.income_source import IncomeSource, SourceStatus
from app.services import tasks as task_svc
from app.services.sources.base import SourceOpportunity
from app.services.sources.local_business_prospector import LocalBusinessProspectorAdapter


def _make_session() -> Session:
    import app.models.action_packet  # noqa: F401
    import app.models.alert  # noqa: F401
    import app.models.decision  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.task  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_local_business_prospector_passes_through_real_published_contact_info():
    adapter = LocalBusinessProspectorAdapter.__new__(LocalBusinessProspectorAdapter)
    raw = {
        "id": 12345,
        "tags": {
            "amenity": "dentist",
            "name": "Example Dental",
            "email": "front-desk@exampledental.example",
            # no website — this lead still qualifies (missing website is
            # itself a valid gap), so email is the only channel.
            "addr:city": "Macon",
        },
    }

    opp = adapter.normalize(raw)

    assert opp is not None
    assert opp.metadata["contact_email"] == "front-desk@exampledental.example"
    assert opp.metadata["contact_url"] is None  # never guessed
    assert opp.metadata["target_buyer"] == "Example Dental"


def test_local_business_prospector_never_fabricates_missing_contact_info():
    adapter = LocalBusinessProspectorAdapter.__new__(LocalBusinessProspectorAdapter)
    raw = {
        "id": 67890,
        "tags": {
            "amenity": "church",
            "name": "Example Church",
            # no email, no website, no phone at all
        },
    }

    opp = adapter.normalize(raw)

    assert opp is not None
    assert opp.metadata["contact_email"] is None
    assert opp.metadata["contact_url"] is None


def test_persist_results_carries_contact_metadata_into_notes():
    from app.services.source_acquisition import _persist_results

    session = _make_session()
    item = SourceOpportunity(
        source_id="local:osm:dentist:12345",
        title="Example Dental",
        description="Example Dental shows public listing gaps: missing website.",
        estimated_profit=850.0,
        currency="USD",
        confidence=0.66,
        next_action="Prepare a short outreach offer.",
        origin_module="local_business_prospector",
        category="healthcare-implementation",
        lane="local_business_prospecting",
        source_url="https://www.openstreetmap.org/node/12345",
        timestamp="2026-09-10T12:00:00+00:00",
        metadata={
            "contact_email": "front-desk@exampledental.example",
            "contact_url": None,
            "target_buyer": "Example Dental",
        },
    )

    _persist_results(session, [item])

    record = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == "local:osm:dentist:12345")
    ).first()
    assert record is not None
    assert "contact_email: front-desk@exampledental.example" in record.notes
    assert "target_buyer: Example Dental" in record.notes
    assert "contact_url:" not in record.notes  # absent value never appended


def test_persist_results_omits_contact_fields_entirely_when_source_has_none():
    """A source provider that never populates metadata (every other
    provider today) must keep working exactly as before — no
    "contact_email: None" noise in notes."""
    from app.services.source_acquisition import _persist_results

    session = _make_session()
    item = SourceOpportunity(
        source_id="congress:trade:001",
        title="Congressional trade signal",
        description="desc",
        estimated_profit=100.0,
        currency="USD",
        confidence=0.7,
        next_action="review",
        origin_module="autotrader",
        category="trading",
        lane="trading",
        source_url="https://example.com/x",
        timestamp="2026-09-10T12:00:00+00:00",
    )

    _persist_results(session, [item])

    record = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == "congress:trade:001")
    ).first()
    assert record is not None
    assert "contact_email" not in record.notes
    assert "contact_url" not in record.notes


def test_backfill_bootstrap_dispatches_existing_undispatched_leads(monkeypatch):
    """EOD priority (Commander, 2026-09-10): "ensure the contact/routing
    corrections apply to existing eligible leads, not only future
    inserts." A local_business_prospector source already in the database
    from before today's routing fix — never dispatched, since the old
    routing was capability-gapped — gets dispatched by the backfill
    bootstrap without waiting for a fresh discovery scan."""
    import app.database.config as db_config

    session = _make_session()
    source = IncomeSource(
        source_id="local:osm:dentist:11111",
        description="Example Dental shows public listing gaps: missing website.",
        estimated_profit=850.0,
        currency="USD",
        status=SourceStatus.budgeted,
        date_found=date(2026, 9, 8),
        next_action="Prepare a short outreach offer.",
        notes=(
            "Source URL: https://www.openstreetmap.org/node/11111 | "
            "Lane: local_business_prospecting | "
            "contact_email: front-desk@exampledental.example | "
            "target_buyer: Example Dental"
        ),
        origin_module="local_business_prospector",
        category="healthcare-implementation",
        confidence=0.66,
        score=70.0,
    )
    session.add(source)
    session.commit()
    session.close()

    monkeypatch.setattr(db_config, "engine", session.get_bind())
    from app.main import _bootstrap_backfill_local_business_dispatch_after_startup

    _bootstrap_backfill_local_business_dispatch_after_startup()

    verify_session = Session(session.get_bind())
    from app.models.task import Task

    tasks = verify_session.exec(
        select(Task).where(Task.source_id == "local:osm:dentist:11111")
    ).all()
    assert len(tasks) == 1
    assert tasks[0].task_type == "service_outreach"


def test_backfill_bootstrap_is_a_safe_noop_on_repeated_runs(monkeypatch):
    import app.database.config as db_config

    session = _make_session()
    source = IncomeSource(
        source_id="local:osm:dentist:22222",
        description="Example Clinic shows public listing gaps: missing website.",
        estimated_profit=850.0,
        currency="USD",
        status=SourceStatus.budgeted,
        date_found=date(2026, 9, 8),
        next_action="Prepare a short outreach offer.",
        notes="Source URL: https://www.openstreetmap.org/node/22222 | Lane: local_business_prospecting",
        origin_module="local_business_prospector",
        category="healthcare-implementation",
        confidence=0.66,
        score=70.0,
    )
    session.add(source)
    session.commit()
    session.close()

    monkeypatch.setattr(db_config, "engine", session.get_bind())
    from app.main import _bootstrap_backfill_local_business_dispatch_after_startup

    _bootstrap_backfill_local_business_dispatch_after_startup()
    _bootstrap_backfill_local_business_dispatch_after_startup()

    verify_session = Session(session.get_bind())
    from app.models.task import Task

    tasks = verify_session.exec(
        select(Task).where(Task.source_id == "local:osm:dentist:22222")
    ).all()
    assert len(tasks) == 1  # no duplicate task from the second run


def test_local_business_prospector_origin_resolves_to_a_real_executor():
    """Deeper gap than the missing contact info: local_business_prospector's
    origin_module used to map to "local_outreach", a task_type with no
    executor branch — resolve_task_type correctly refused to dispatch it
    at all (capability-aware routing), so no local_business_prospector
    lead could ever become a task, regardless of contact info. It now
    maps to service_outreach, the executor that already handles this
    exact data shape."""
    from types import SimpleNamespace

    source = SimpleNamespace(
        source_id="local:osm:dentist:1",
        origin_module="local_business_prospector",
        category="healthcare-implementation",
    )
    assert task_svc.resolve_task_type(source) == "service_outreach"


def test_end_to_end_real_contact_email_reaches_the_dispatched_task_spec():
    """The full chain this Track B fix restores: a source whose notes
    carry a real contact_email (as source_acquisition now writes it)
    produces a service_outreach task whose spec actually contains it —
    the exact input _execute_service_outreach needs to send a real
    email instead of failing with "No contact route available"."""
    import json

    session = _make_session()
    source = IncomeSource(
        source_id="local:osm:dentist:99999",
        description="Example Dental shows public listing gaps: missing website.",
        estimated_profit=850.0,
        currency="USD",
        status=SourceStatus.budgeted,
        date_found=date(2026, 9, 8),
        next_action="Prepare a short outreach offer.",
        notes=(
            "Source URL: https://www.openstreetmap.org/node/99999 | "
            "Lane: local_business_prospecting | "
            "contact_email: front-desk@exampledental.example | "
            "target_buyer: Example Dental"
        ),
        origin_module="local_business_prospector",
        category="healthcare-implementation",
        confidence=0.66,
        score=70.0,
    )
    session.add(source)
    session.commit()

    task = task_svc.auto_dispatch_for_source(source.source_id, session)

    assert task is not None
    assert task.task_type == "service_outreach"
    spec = json.loads(task.spec_payload)
    assert spec["service_outreach"]["contact_email"] == "front-desk@exampledental.example"
    assert spec["service_outreach"]["business_type"] == "Example Dental"
