"""
Tests for Hunter's own research engine (app/services/research/). Every
HTTP call is served by an in-process httpx.MockTransport — no live
network — so these prove the engine's logic (evidence persistence,
scoring, rescue history, disposition routing, gate wiring) is correct
regardless of what a live run finds. Live-network behavior is exercised
separately (see the manual acceptance run recorded in the addendum
follow-up), never faked here as if it were a real finding.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition, FormalGate, GateVerdict, RescueAttempt
from app.services import execution_accounting as acct, formal_gates
from app.services.quota_loop import run_quota_protection_loop
from app.services.research import engine as research_engine
from app.services.research.gate_runner import run_gate_acceptance_test
from app.services.research.providers.ga_unclaimed_property import (
    PORTAL_URL,
    PROGRAM_URL,
    ga_unclaimed_property_research,
)
from app.services.research.providers.google_incognito import INTAKE_URL, google_incognito_research


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_candidate(session: Session, cid: str, **overrides) -> CanonicalOpportunity:
    fields = dict(
        canonical_opportunity_id=cid,
        lane="test",
        factual_mechanism="test mechanism",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 8),
        disposition=Disposition.screened_only.value,
    )
    fields.update(overrides)
    opp = CanonicalOpportunity(**fields)
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def _client_factory(handler):
    def _factory():
        return httpx.Client(transport=httpx.MockTransport(handler))
    return _factory


UCP_ID = "HUNTER-CAND-2026-09-08-01-UCP"
GOOGLE_ID = "HUNTER-CAND-2026-09-08-02-GOOGLE"


# ── GA unclaimed property provider ───────────────────────────────────────


def test_ga_provider_passes_when_both_official_sources_reachable():
    session = _make_session()
    _make_candidate(session, UCP_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) in (PROGRAM_URL, PORTAL_URL)
        return httpx.Response(200, text="ok")

    result = research_engine.run_research(session, UCP_ID, client_factory=_client_factory(handler))
    assert result.passed is True
    assert result.network_ok is True
    assert result.commander_checkpoint and "FEIN" in result.commander_checkpoint

    opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == UCP_ID)).first()
    assert opp.jurisdiction == "Georgia, USA"
    assert opp.eligibility and "FEIN" in opp.eligibility
    assert opp.evidence_log and PROGRAM_URL in opp.evidence_log
    assert opp.score is not None

    attempts = session.exec(select(RescueAttempt).where(RescueAttempt.canonical_opportunity_id == UCP_ID)).all()
    assert any(a.result == "found" for a in attempts)


def test_ga_provider_blocks_on_unreachable_source_without_permanent_rejection():
    session = _make_session()
    _make_candidate(session, UCP_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PROGRAM_URL:
            return httpx.Response(200, text="ok")
        return httpx.Response(503, text="down")

    result = research_engine.run_research(session, UCP_ID, client_factory=_client_factory(handler))
    assert result.passed is False
    assert result.disposition_override == Disposition.blocked
    # One source succeeded, so this is NOT a total network outage.
    assert result.network_ok is True


def test_ga_provider_reports_network_outage_distinctly():
    session = _make_session()
    _make_candidate(session, UCP_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated egress block", request=request)

    result = research_engine.run_research(session, UCP_ID, client_factory=_client_factory(handler))
    assert result.passed is False
    assert result.network_ok is False  # every source failed at the transport layer


# ── Google Incognito provider ────────────────────────────────────────────


def test_google_provider_passes_on_open_intake_signal():
    session = _make_session()
    _make_candidate(session, GOOGLE_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == INTAKE_URL
        return httpx.Response(200, text="<html>Free Case Review — Contact Us</html>")

    result = research_engine.run_research(session, GOOGLE_ID, client_factory=_client_factory(handler))
    assert result.passed is True
    assert "retainer" in (result.commander_checkpoint or "").lower()

    opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == GOOGLE_ID)).first()
    assert "no class-wide cash fund" in (opp.current_lawful_implementation or "").lower()


def test_google_provider_blocks_when_no_open_intake_signal():
    session = _make_session()
    _make_candidate(session, GOOGLE_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Page moved</html>")

    result = research_engine.run_research(session, GOOGLE_ID, client_factory=_client_factory(handler))
    assert result.passed is False
    assert result.disposition_override == Disposition.blocked
    assert result.network_ok is True


# ── Engine-level behavior ────────────────────────────────────────────────


def test_no_registered_provider_falls_back_to_generic_path_and_blocks_on_capability_gap(monkeypatch):
    """No dedicated connector must NOT dead-end a candidate — it should
    fall through to the generic research path, which here has no advisor
    API key configured (as in this test env), so it fails as an honest
    capability gap rather than a fabricated pass or a bare 'no provider'
    stub."""
    for env_var in ("GROK_API_KEY", "VENICE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)

    session = _make_session()
    _make_candidate(session, "NO-PROVIDER-CAND")
    result = research_engine.run_research(session, "NO-PROVIDER-CAND")
    assert result.passed is False
    assert result.disposition_override == Disposition.blocked_capability
    assert result.network_ok is True  # this is a capability gap, not a network outage
    attempts = session.exec(select(RescueAttempt).where(RescueAttempt.canonical_opportunity_id == "NO-PROVIDER-CAND")).all()
    assert len(attempts) == 1
    assert "no configured advisor" in attempts[0].description.lower()


def test_provider_exception_is_caught_and_logged_not_fabricated():
    session = _make_session()
    _make_candidate(session, "BUGGY-CAND")

    def buggy_provider(client: httpx.Client):
        raise RuntimeError("provider bug")

    research_engine.register_provider("BUGGY-CAND", buggy_provider)
    try:
        result = research_engine.run_research(session, "BUGGY-CAND")
    finally:
        del research_engine.PROVIDER_REGISTRY["BUGGY-CAND"]

    assert result.passed is False
    assert result.disposition_override == Disposition.blocked_capability
    opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "BUGGY-CAND")).first()
    assert "provider bug" in (opp.evidence_log or "")


def test_run_research_missing_candidate_raises():
    session = _make_session()
    with pytest.raises(ValueError):
        research_engine.run_research(session, "DOES-NOT-EXIST")


# ── Loop integration: Hunter's own preflight, not Claude's ──────────────


def test_loop_uses_research_engine_as_its_own_preflight():
    session = _make_session()
    _make_candidate(session, UCP_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    result = run_quota_protection_loop(
        session,
        day=date(2026, 9, 8),
        preflight_fn=research_engine.make_research_preflight(session, client_factory=_client_factory(handler)),
    )
    # GA candidate resolves a real Commander checkpoint (FEIN needed) — so
    # the loop correctly stops there rather than fabricating an execution.
    assert result.execution_count == 0
    opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == UCP_ID)).first()
    assert opp.disposition == Disposition.pending_commander.value
    assert opp.eligibility  # evidence was actually persisted by the loop's own preflight call


# ── Gate acceptance tests (UCP-01 / GOOGLE-02 as literal tests of Hunter) ─


def test_gate_acceptance_passes_when_hunter_confirms_full_picture():
    session = _make_session()
    _make_candidate(session, UCP_ID)
    formal_gates.upsert_gate(session, "GATE-UCP", "Georgia corporate unclaimed-property recovery", canonical_opportunity_id=UCP_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    gate = run_gate_acceptance_test(session, "GATE-UCP", client_factory=_client_factory(handler))
    assert gate.verdict == GateVerdict.pass_.value
    assert gate.counts_toward_quota is False  # PASS never grants execution credit
    assert "FEIN" in gate.evidence_summary


def test_gate_acceptance_withholds_verdict_on_network_outage_rather_than_faking_fail():
    session = _make_session()
    _make_candidate(session, GOOGLE_ID)
    formal_gates.upsert_gate(session, "GATE-GOOGLE", "Google Incognito individual-damages route", canonical_opportunity_id=GOOGLE_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated egress block", request=request)

    gate = run_gate_acceptance_test(session, "GATE-GOOGLE", client_factory=_client_factory(handler))
    assert gate.verdict == GateVerdict.pending_evidence.value
    assert "could not reach" in gate.evidence_summary.lower()


def test_gate_acceptance_fails_when_route_confirmed_closed():
    session = _make_session()
    _make_candidate(session, GOOGLE_ID)
    formal_gates.upsert_gate(session, "GATE-GOOGLE-2", "Google Incognito individual-damages route", canonical_opportunity_id=GOOGLE_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>404 not found</html>")

    gate = run_gate_acceptance_test(session, "GATE-GOOGLE-2", client_factory=_client_factory(handler))
    assert gate.verdict == GateVerdict.fail.value
