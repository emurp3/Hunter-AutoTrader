"""
Commander's recovery-board finding: execution_accounting's 5/day, 25/week,
100/4-week quota counted every ExecutionRecord regardless of which lane its
CanonicalOpportunity belonged to — so a real trade recorded against the
seeded trading-lane candidate (DARKPOOL) would count toward the same quota
meant only for the 149/compliance-recovery campaign. Commander's decision:
"The 5/day, 25/week, 100/four-week target applies only to the 149
campaign."

First pass used a blocklist (exclude lane="trading"). Commander's follow-up
correction: that's insufficient unless every OTHER counted record is
proven to belong to the campaign — a blocklist silently counts anything
not explicitly named. This is now a positive allowlist
(CAMPAIGN_LANES) — only proven campaign lanes count, and anything
unresolvable or out of scope (including an unexpected/future lane, not
just "trading") fails closed. No change to record_execution()'s
receipt/endpoint invariants or to what counts as a valid execution —
just which lanes feed the quota count.
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


def test_execution_record_with_no_linked_opportunity_does_not_count():
    """Positive membership: a receipt whose CanonicalOpportunity row can't
    be found (e.g. a data inconsistency — shouldn't happen given
    record_execution() always requires a real canonical_opportunity_id,
    but data can drift) fails closed. It cannot be proven to belong to
    the campaign, so it must not inflate the campaign's own quota —
    "other activity does not count merely because it shares a table"."""
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
    assert acct.get_execution_count(session, _NON_SUNDAY) == 0


def test_unexpected_lane_not_named_trading_also_does_not_count():
    """The whole point of a positive allowlist over a blocklist: a lane
    that isn't "trading" (e.g. a future crypto lane, or a mistagged row)
    must not be counted just because it's not on a exclusion list."""
    session = _make_session()
    _make_candidate(session, "CRYPTO-CAND", lane="crypto")

    acct.record_execution(
        session,
        canonical_opportunity_id="CRYPTO-CAND",
        source="test",
        action_description="placed a crypto order",
        actions_taken="bought BTC",
        external_endpoint="https://broker.example/crypto/1",
        receipt_reference="crypto-receipt-1",
        timestamp=_TS,
        enforce_sunday_lockout=False,
    )

    assert acct.get_execution_count(session, _NON_SUNDAY) == 0


def test_every_currently_seeded_addendum_lane_is_in_campaign_lanes():
    """Guards against drift: every lane app.services.hunter_addendum_seed
    actually seeds today (compliance_recovery, legal_claims, data_service,
    service — DARKPOOL's "trading" is the deliberate exception) must be
    covered by CAMPAIGN_LANES, or real campaign candidates would silently
    stop counting toward their own quota."""
    from app.services.hunter_addendum_seed import _CANDIDATES

    seeded_lanes = {c["lane"] for c in _CANDIDATES}
    non_campaign = seeded_lanes - acct.CAMPAIGN_LANES
    assert non_campaign == {"trading"}
