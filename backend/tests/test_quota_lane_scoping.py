"""
Commander's recovery-board finding: execution_accounting's 5/day, 25/week,
100/4-week quota counted every ExecutionRecord regardless of which lane its
CanonicalOpportunity belonged to — so a real trade recorded against the
seeded trading-lane candidate (DARKPOOL) would count toward the same quota
meant only for the 149/compliance-recovery campaign. Commander's decision:
"The 5/day, 25/week, 100/four-week target applies only to the 149
campaign." This is a bounded, campaign-scoping fix only — no change to
record_execution()'s receipt/endpoint invariants, no change to what counts
as a valid execution, just which lanes feed the quota count.
"""

from __future__ import annotations

import datetime as dt
from datetime import date

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.services import execution_accounting as acct

_NON_SUNDAY = date(2026, 9, 8)  # Tuesday
_TS = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_candidate(session: Session, cid: str, lane: str) -> CanonicalOpportunity:
    opp = CanonicalOpportunity(
        canonical_opportunity_id=cid,
        lane=lane,
        factual_mechanism="test mechanism",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        disposition=Disposition.screened_only.value,
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def test_trading_lane_execution_does_not_count_toward_campaign_quota():
    session = _make_session()
    _make_candidate(session, "DARKPOOL-CAND", lane="trading")

    acct.record_execution(
        session,
        canonical_opportunity_id="DARKPOOL-CAND",
        source="test",
        action_description="placed a trade",
        actions_taken="bought 1 share",
        external_endpoint="https://broker.example/orders/1",
        receipt_reference="trade-receipt-1",
        timestamp=_TS,
        enforce_sunday_lockout=False,
    )

    assert acct.get_execution_count(session, _NON_SUNDAY) == 0
    assert acct.get_weekly_execution_count(session, _NON_SUNDAY) == 0
    assert acct.get_cycle_execution_count(session, _NON_SUNDAY) == 0


def test_compliance_recovery_lane_execution_still_counts():
    session = _make_session()
    _make_candidate(session, "UCP-CAND", lane="compliance_recovery")

    acct.record_execution(
        session,
        canonical_opportunity_id="UCP-CAND",
        source="test",
        action_description="filed claim",
        actions_taken="submitted the claim form",
        external_endpoint="https://dor.georgia.gov/claim/1",
        receipt_reference="claim-receipt-1",
        timestamp=_TS,
        enforce_sunday_lockout=False,
    )

    assert acct.get_execution_count(session, _NON_SUNDAY) == 1
    status = acct.get_quota_status(session, _NON_SUNDAY)
    assert status["execution_count"] == 1


def test_mixed_lanes_only_campaign_lanes_count_toward_quota_pass():
    session = _make_session()
    for i in range(5):
        _make_candidate(session, f"CAMPAIGN-{i}", lane="compliance_recovery")
        acct.record_execution(
            session,
            canonical_opportunity_id=f"CAMPAIGN-{i}",
            source="test",
            action_description="filed claim",
            actions_taken="submitted",
            external_endpoint=f"https://agency.example/{i}",
            receipt_reference=f"campaign-receipt-{i}",
            timestamp=_TS,
            enforce_sunday_lockout=False,
        )
    _make_candidate(session, "TRADE-1", lane="trading")
    acct.record_execution(
        session,
        canonical_opportunity_id="TRADE-1",
        source="test",
        action_description="placed a trade",
        actions_taken="bought",
        external_endpoint="https://broker.example/orders/2",
        receipt_reference="trade-receipt-2",
        timestamp=_TS,
        enforce_sunday_lockout=False,
    )

    status = acct.get_quota_status(session, _NON_SUNDAY)
    # 5 campaign executions -> PASS; the 6th (trading) must not inflate or
    # otherwise be required for the campaign's own daily verdict.
    assert status["execution_count"] == 5
    assert status["daily_verdict"] == "PASS"


def test_execution_record_with_no_linked_opportunity_still_counts():
    """A receipt whose CanonicalOpportunity row can't be found (e.g. a
    data inconsistency) must fail toward counting real work, never toward
    silently discounting it."""
    session = _make_session()
    acct.record_execution(
        session,
        canonical_opportunity_id="MISSING-OPP",
        source="test",
        action_description="filed claim",
        actions_taken="submitted",
        external_endpoint="https://agency.example/orphan",
        receipt_reference="orphan-receipt-1",
        timestamp=_TS,
        enforce_sunday_lockout=False,
    )
    assert acct.get_execution_count(session, _NON_SUNDAY) == 1
