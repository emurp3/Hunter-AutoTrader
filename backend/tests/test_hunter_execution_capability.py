"""
Tests for the minimum execution capability added tonight: dispatching a
search-only worker task for a canonical_opportunity and closing the loop
into the execution ledger on real completion.

These test the ledger-integration logic (tasks.py's _close_ledger_loop)
and the set_disposition(new_checkpoint=...) reopening behavior with a
real in-memory DB — not the live Playwright interaction itself, which
cannot be exercised without a real browser and the real target site.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition, ExecutionRecord
from app.models.task import Task, TaskStatus
from app.services import execution_accounting as acct
from app.services import tasks as task_svc


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.alert  # noqa: F401
    import app.models.income_source  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_candidate(session: Session, cid: str = "GOV-EXEC-CAND", **overrides) -> CanonicalOpportunity:
    fields = dict(
        canonical_opportunity_id=cid,
        lane="compliance_recovery",
        factual_mechanism="test mechanism",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        disposition=Disposition.watchlist.value,
    )
    fields.update(overrides)
    opp = CanonicalOpportunity(**fields)
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def _dispatch_search_task(session: Session, cid: str, **spec_overrides) -> Task:
    spec = {
        "search_url": "https://gaclaims.unclaimedproperty.com/en/Property/SearchIndex",
        "business_name": "Murphy Enterprises LLC",
        "canonical_opportunity_id": cid,
    }
    spec.update(spec_overrides)
    return task_svc.dispatch_task(
        task_type="government_portal_search",
        spec_payload=spec,
        session=session,
        source_type="canonical_opportunity",
        source_id=cid,
        priority=10,
        idempotency_key=f"gov-search:{cid}:test",
        max_attempts=2,
    )


# ── Dispatch ──────────────────────────────────────────────────────────────


def test_dispatch_creates_task_targeting_the_candidate():
    session = _make_session()
    _make_candidate(session)
    task = _dispatch_search_task(session, "GOV-EXEC-CAND")
    assert task.task_type == "government_portal_search"
    assert task.source_type == "canonical_opportunity"
    assert task.source_id == "GOV-EXEC-CAND"
    assert task.status == TaskStatus.dispatched


# ── Close-loop: the safety-critical part ──────────────────────────────────


def test_completed_search_with_real_page_url_records_execution():
    session = _make_session()
    _make_candidate(session)
    task = _dispatch_search_task(session, "GOV-EXEC-CAND")

    completed = task_svc.complete_task(
        task.task_id,
        {"search_performed": True, "business_name_searched": "Murphy Enterprises LLC", "claim_filed": False, "result_excerpt": "No records found."},
        session,
        notes="Hosted HVA searched the portal.",
        screenshot_path="/tmp/hunter-worker-artifacts/x/gov-portal-results.png",
        page_url="https://gaclaims.unclaimedproperty.com/en/Property/SearchResults?q=Murphy",
        engine="playwright",
        worker_id_override="test-worker",
    )
    assert completed.status == TaskStatus.completed

    opp = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "GOV-EXEC-CAND")
    ).first()
    assert opp.disposition == Disposition.executed.value

    records = session.exec(select(ExecutionRecord).where(ExecutionRecord.canonical_opportunity_id == "GOV-EXEC-CAND")).all()
    assert len(records) == 1
    assert records[0].external_endpoint == "https://gaclaims.unclaimedproperty.com/en/Property/SearchResults?q=Murphy"
    assert records[0].receipt_reference == f"task:{task.task_id}"
    # Claim was NOT filed — realized cash must not be populated from this.
    assert records[0].realized_gross_cash is None

    assert acct.get_execution_count(session) == 1


def test_completed_task_without_page_url_never_fabricates_an_execution():
    """A completed task with no real external endpoint (e.g. a
    reconnaissance-only run, or a code path that forgot to pass page_url)
    must NOT be recorded as an execution."""
    session = _make_session()
    _make_candidate(session)
    task = _dispatch_search_task(session, "GOV-EXEC-CAND")

    task_svc.complete_task(
        task.task_id,
        {"search_performed": True, "business_name_searched": "Murphy Enterprises LLC"},
        session,
        page_url=None,  # nothing real reached
    )

    opp = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "GOV-EXEC-CAND")
    ).first()
    assert opp.disposition != Disposition.executed.value
    assert acct.get_execution_count(session) == 0


def test_completed_task_with_search_performed_false_never_fabricates_an_execution():
    """An escalated/failed search must never be recorded as EXECUTED even
    if some page_url happens to be present (e.g. the anti-bot screenshot page)."""
    session = _make_session()
    _make_candidate(session)
    task = _dispatch_search_task(session, "GOV-EXEC-CAND")

    task_svc.complete_task(
        task.task_id,
        {"search_performed": False},
        session,
        page_url="https://gaclaims.unclaimedproperty.com/en/Property/SearchIndex",
    )

    opp = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "GOV-EXEC-CAND")
    ).first()
    assert opp.disposition != Disposition.executed.value
    assert acct.get_execution_count(session) == 0


def test_close_ledger_loop_is_a_safe_noop_for_unknown_candidate():
    session = _make_session()
    task = task_svc.dispatch_task(
        task_type="government_portal_search",
        spec_payload={"search_url": "https://example.gov", "business_name": "Nobody LLC"},
        session=session,
        source_type="canonical_opportunity",
        source_id="DOES-NOT-EXIST",
    )
    # Must not raise even though no matching CanonicalOpportunity exists.
    task_svc.complete_task(
        task.task_id,
        {"search_performed": True},
        session,
        page_url="https://example.gov/results",
    )
    assert acct.get_execution_count(session) == 0


def test_duplicate_task_completion_does_not_double_count():
    """Two different tasks against the same candidate each produce a
    distinct receipt — the accounting layer's own uniqueness invariant
    still holds when driven through the task-completion path."""
    session = _make_session()
    _make_candidate(session)
    task1 = _dispatch_search_task(session, "GOV-EXEC-CAND")
    task_svc.complete_task(
        task1.task_id, {"search_performed": True, "business_name_searched": "Murphy Enterprises LLC"},
        session, page_url="https://gaclaims.unclaimedproperty.com/results/1",
    )
    assert acct.get_execution_count(session) == 1

    task2 = task_svc.dispatch_task(
        task_type="government_portal_search",
        spec_payload={"search_url": "https://gaclaims.unclaimedproperty.com/en/Property/SearchIndex", "business_name": "Murphy Enterprises LLC"},
        session=session,
        source_type="canonical_opportunity",
        source_id="GOV-EXEC-CAND",
        idempotency_key="gov-search:GOV-EXEC-CAND:test-2",
    )
    task_svc.complete_task(
        task2.task_id, {"search_performed": True, "business_name_searched": "Murphy Enterprises LLC"},
        session, page_url="https://gaclaims.unclaimedproperty.com/results/2",
    )
    assert acct.get_execution_count(session) == 2
    records = session.exec(select(ExecutionRecord).where(ExecutionRecord.canonical_opportunity_id == "GOV-EXEC-CAND")).all()
    assert len({r.receipt_reference for r in records}) == 2


# ── set_disposition(new_checkpoint=...) reopening ─────────────────────────


def test_reopen_pending_commander_with_new_checkpoint_clears_old_answer():
    session = _make_session()
    _make_candidate(session, "REOPEN-CAND", commander_response="old answer", disposition=Disposition.watchlist.value)
    acct.record_rescue_attempt(session, "REOPEN-CAND", "alternate_channel", "found route", result="found")

    opp = acct.set_disposition(
        session, "REOPEN-CAND", Disposition.pending_commander,
        new_checkpoint="Need your full name, email, and dates of Incognito use.",
    )
    assert opp.disposition == Disposition.pending_commander.value
    assert opp.required_commander_checkpoints == "Need your full name, email, and dates of Incognito use."
    assert opp.commander_response is None
    assert opp.commander_responded_at is None


def test_reopened_candidate_reappears_in_commander_decisions_feed():
    from app.routers.hunter_ledger import list_commander_decisions

    session = _make_session()
    _make_candidate(session, "REOPEN-CAND-2", commander_response="already answered once", disposition=Disposition.watchlist.value)
    acct.record_rescue_attempt(session, "REOPEN-CAND-2", "alternate_channel", "found route", result="found")

    # Before reopening: not in the feed (already answered).
    assert "REOPEN-CAND-2" not in {d["canonical_opportunity_id"] for d in list_commander_decisions(session=session)}

    acct.set_disposition(
        session, "REOPEN-CAND-2", Disposition.pending_commander,
        new_checkpoint="Need your full name and email.",
    )
    decisions = list_commander_decisions(session=session)
    match = next(d for d in decisions if d["canonical_opportunity_id"] == "REOPEN-CAND-2")
    assert match["checkpoint"] == "Need your full name and email."
    assert match["commander_response"] is None
