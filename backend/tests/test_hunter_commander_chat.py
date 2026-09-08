"""
Tests for the Commander-answer path that backs the Hunter chat widget's
approve/decline/answer actions. Commander's reply is only ever written by
record_commander_answer() — never inferred from freeform chat text by an
LLM — and decline still has to satisfy the same rescue-history invariant
as any other REJECTED transition.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.services import execution_accounting as acct


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_pending_commander_candidate(session: Session, cid: str = "CHAT-CAND-1") -> CanonicalOpportunity:
    opp = CanonicalOpportunity(
        canonical_opportunity_id=cid,
        lane="test",
        factual_mechanism="test mechanism",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        required_commander_checkpoints="need your FEIN",
        disposition=Disposition.pending_commander.value,
    )
    session.add(opp)
    session.commit()
    # PENDING_COMMANDER is only ever reached after research logs a rescue
    # attempt in the real flow — reproduce that here.
    acct.record_rescue_attempt(session, cid, "alternate_channel", "confirmed official channel", result="found")
    return opp


def test_commander_answer_requires_nonempty_text():
    session = _make_session()
    _make_pending_commander_candidate(session)
    with pytest.raises(ValueError):
        acct.record_commander_answer(session, "CHAT-CAND-1", "   ")


def test_commander_supplies_info_moves_to_watchlist():
    session = _make_session()
    _make_pending_commander_candidate(session)

    opp = acct.record_commander_answer(
        session, "CHAT-CAND-1", "Company: Acme Holdings LLC, FEIN 12-3456789"
    )
    assert opp.disposition == Disposition.watchlist.value
    assert opp.commander_response == "Company: Acme Holdings LLC, FEIN 12-3456789"
    assert opp.commander_responded_at is not None


def test_commander_approve_moves_to_watchlist():
    session = _make_session()
    _make_pending_commander_candidate(session)

    opp = acct.record_commander_answer(session, "CHAT-CAND-1", "go ahead", decision="approve")
    assert opp.disposition == Disposition.watchlist.value


def test_commander_decline_moves_to_rejected_with_evidence():
    session = _make_session()
    _make_pending_commander_candidate(session)

    opp = acct.record_commander_answer(session, "CHAT-CAND-1", "not interested", decision="decline")
    assert opp.disposition == Disposition.rejected.value
    assert opp.commander_response == "not interested"
    assert "Commander declined" in (opp.next_action or "")


def test_commander_decline_without_rescue_history_still_enforces_invariant():
    session = _make_session()
    opp = CanonicalOpportunity(
        canonical_opportunity_id="CHAT-NO-RESCUE",
        lane="test",
        factual_mechanism="test",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        disposition=Disposition.pending_commander.value,
    )
    session.add(opp)
    session.commit()

    with pytest.raises(acct.RescueHistoryRequiredError):
        acct.record_commander_answer(session, "CHAT-NO-RESCUE", "no", decision="decline")

    # The answer text is still recorded even though the disposition
    # transition failed — Commander's reply is never silently dropped.
    refreshed = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "CHAT-NO-RESCUE")
    ).first()
    assert refreshed.commander_response == "no"


def test_commander_decisions_endpoint_drops_answered_candidates():
    from app.routers.hunter_ledger import list_commander_decisions

    session = _make_session()
    _make_pending_commander_candidate(session, "CHAT-CAND-2")
    _make_pending_commander_candidate(session, "CHAT-CAND-3")
    acct.record_commander_answer(session, "CHAT-CAND-2", "handled", decision="approve")

    decisions = list_commander_decisions(session=session)
    ids = {d["canonical_opportunity_id"] for d in decisions}
    # CHAT-CAND-2 answered (and moved off PENDING_COMMANDER) — gone from the feed.
    # CHAT-CAND-3 still open.
    assert "CHAT-CAND-2" not in ids
    assert "CHAT-CAND-3" in ids
