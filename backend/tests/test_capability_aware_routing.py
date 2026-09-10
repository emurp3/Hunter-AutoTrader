"""
Recovery-board finding: several task_types are assignable by
_ORIGIN_TO_TASK_TYPE/_CATEGORY_TO_TASK_TYPE (gig_application,
github_bounty, rfp_response, affiliate_signup, social_outreach,
local_outreach) but app.worker.executors.execute_task has no branch for
any of them — a dispatched task of one of these types is guaranteed to be
claimed, fail immediately, and escalate as "Unsupported task_type",
wasting a worker attempt and raising a pointless Commander alert.
resolve_task_type() now routes these to None (skip dispatch) instead,
exactly like the existing execution_path=="trading" skip — a capability
gap, not a fresh policy decision, and it doesn't need to burn a worker
cycle to be visible (it's logged instead).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.income_source import IncomeSource, SourceStatus
from app.models.task import Task
from app.services import tasks as task_svc


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


def _seed_source(session: Session, *, category: str, sid: str = "at:gig-001") -> IncomeSource:
    source = IncomeSource(
        source_id=sid,
        description="test opportunity",
        estimated_profit=10.0,
        currency="USD",
        status=SourceStatus.budgeted,
        date_found=date(2026, 9, 8),
        category=category,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def test_unsupported_category_resolves_to_no_task_type():
    session = _make_session()
    source = _seed_source(session, category="gig")
    assert task_svc.resolve_task_type(source) is None


def test_unsupported_origin_module_resolves_to_no_task_type():
    session = _make_session()
    source = _seed_source(session, category="")
    source.origin_module = "github_scanner"
    assert task_svc.resolve_task_type(source) is None


def test_supported_category_still_resolves_normally():
    session = _make_session()
    source = _seed_source(session, category="marketplace")
    assert task_svc.resolve_task_type(source) == "marketplace_listing"


def test_every_mapped_task_type_is_accounted_for_as_supported_or_not():
    """Guards against silent drift: every task_type reachable via
    IncomeSource origin/category mapping either has a wired executor
    (SUPPORTED_TASK_TYPES) or is a deliberately-tracked known gap. Fails
    loudly if a new mapping is added without a decision either way.
    (government_portal_search/intake_form_submission/generic_execution
    are real, wired, supported task_types too — they're just dispatched
    via the ledger router / trading-spec short-circuit, not these two
    IncomeSource maps, so they're intentionally absent from all_mapped.)"""
    all_mapped = set(task_svc._ORIGIN_TO_TASK_TYPE.values()) | set(task_svc._CATEGORY_TO_TASK_TYPE.values())
    known_gaps = {
        "gig_application", "github_bounty", "rfp_response",
        "affiliate_signup", "social_outreach", "local_outreach",
    }
    reachable_supported = task_svc.SUPPORTED_TASK_TYPES & all_mapped
    assert all_mapped == reachable_supported | known_gaps
    assert reachable_supported == {"marketplace_listing", "service_outreach", "digital_product_launch"}


def test_unsupported_task_type_never_reaches_dispatch():
    session = _make_session()
    source = _seed_source(session, category="rfp")

    task = task_svc.auto_dispatch_for_source(source.source_id, session)

    assert task is None
    assert session.exec(select(Task)).all() == []


def test_supported_task_type_still_dispatches():
    session = _make_session()
    source = _seed_source(session, category="marketplace")
    source.notes = "listing_price: 40 | listing_title: Widget"

    task = task_svc.auto_dispatch_for_source(source.source_id, session)

    assert task is not None
    assert task.task_type == "marketplace_listing"
