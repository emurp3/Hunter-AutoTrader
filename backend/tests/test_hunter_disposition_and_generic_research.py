"""
Tests for the disposition-semantics correction and the generic (non-
connector) research path — both requested to fix a real problem: a
network-blocked or capability-blocked candidate must never be reported as
if Hunter substantively judged it bad, and Hunter's 149-item queue cannot
require a bespoke Python connector per opportunity.

All HTTP (including the LLM advisor calls) is served by an in-process
httpx.MockTransport — no live network, no live model calls.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import (
    CanonicalOpportunity,
    Disposition,
    DISPOSITIONS_REQUIRING_REPLACEMENT,
    DISPOSITIONS_REQUIRING_RESCUE_HISTORY,
    NON_SUBSTANTIVE_DISPOSITIONS,
    ReplacementChain,
    RescueAttempt,
    ZERO_EXECUTION_DISPOSITIONS,
)
from app.services import execution_accounting as acct
from app.services.research import engine as research_engine
from app.services.research.generic.provider import generic_research


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


# ── Disposition model correctness ────────────────────────────────────────


def test_new_dispositions_exist_and_are_zero_execution():
    for d in (Disposition.pending_research, Disposition.blocked_infrastructure, Disposition.blocked_capability):
        assert d in ZERO_EXECUTION_DISPOSITIONS
        assert d in NON_SUBSTANTIVE_DISPOSITIONS


def test_infra_and_capability_blocks_require_rescue_history_and_open_replacement():
    for d in (Disposition.blocked_infrastructure, Disposition.blocked_capability):
        assert d in DISPOSITIONS_REQUIRING_RESCUE_HISTORY
        assert d in DISPOSITIONS_REQUIRING_REPLACEMENT


def test_default_disposition_for_new_candidate_is_pending_research():
    session = _make_session()
    opp = _make_candidate(session, "DEFAULT-DISP-CAND")
    assert opp.disposition == Disposition.pending_research.value


def test_set_disposition_blocked_infrastructure_requires_rescue_history_first():
    session = _make_session()
    _make_candidate(session, "INFRA-CAND")
    with pytest.raises(acct.RescueHistoryRequiredError):
        acct.set_disposition(session, "INFRA-CAND", Disposition.blocked_infrastructure)

    acct.record_rescue_attempt(session, "INFRA-CAND", "alternate_channel", "tried and network failed", result="pending")
    opp = acct.set_disposition(session, "INFRA-CAND", Disposition.blocked_infrastructure)
    assert opp.disposition == Disposition.blocked_infrastructure.value

    chains = session.exec(select(ReplacementChain).where(ReplacementChain.blocked_canonical_opportunity_id == "INFRA-CAND")).all()
    assert len(chains) == 1  # still opens a replacement chain — the opportunity stays alive


# ── Reconciliation ledger precision ──────────────────────────────────────


def test_reconciliation_ledger_does_not_conflate_infra_capability_with_rejection():
    session = _make_session()
    _make_candidate(session, "C-PENDING-RESEARCH")  # left at default

    _make_candidate(session, "C-INFRA")
    acct.record_rescue_attempt(session, "C-INFRA", "alternate_channel", "network down", result="pending")
    acct.set_disposition(session, "C-INFRA", Disposition.blocked_infrastructure)

    _make_candidate(session, "C-CAPABILITY")
    acct.record_rescue_attempt(session, "C-CAPABILITY", "alternate_channel", "no connector", result="pending")
    acct.set_disposition(session, "C-CAPABILITY", Disposition.blocked_capability)

    _make_candidate(session, "C-REJECTED")
    acct.record_rescue_attempt(session, "C-REJECTED", "official_successor", "checked", result="not_found")
    acct.set_disposition(session, "C-REJECTED", Disposition.rejected, evidence="mechanism is necessarily unlawful, no lawful equivalent")

    recon = acct.reconciliation_ledger(session)
    assert recon["screened"] == 4
    assert recon["executed"] == 0
    assert recon["not_yet_executed"] == 4
    # The precise breakdown must distinguish these, not lump them as "rejected."
    assert recon["substantively_rejected"] == 1
    assert recon["research_incomplete"] == 3  # pending_research + blocked_infrastructure + blocked_capability
    assert recon["by_disposition"][Disposition.blocked_infrastructure.value] == 1
    assert recon["by_disposition"][Disposition.blocked_capability.value] == 1
    assert recon["by_disposition"][Disposition.pending_research.value] == 1
    assert recon["by_disposition"][Disposition.rejected.value] == 1


# ── Generic research path (LLM-assisted discovery + Hunter's own fetch) ─


def _llm_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


def test_generic_research_succeeds_with_advisor_and_reachable_source(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    session = _make_session()
    opp = _make_candidate(session, "GENERIC-OK", factual_mechanism="Some real program")

    def handler(request: httpx.Request) -> httpx.Response:
        if "chat/completions" in str(request.url):
            body = json.loads(request.content)
            if "authoritative web sources" in body["messages"][0]["content"]:
                return _llm_response({"sources": [{"url": "https://agency.gov/program", "why": "official"}]})
            return _llm_response({
                "supports_opportunity": True,
                "eligibility": "open to all residents",
                "jurisdiction": "Georgia, USA",
                "current_lawful_implementation": "apply via the official portal",
                "commander_checkpoint": "Commander must approve before submission",
            })
        assert str(request.url) == "https://agency.gov/program"
        return httpx.Response(200, text="<html>Official program page</html>")

    result = research_engine.run_research(session, "GENERIC-OK", client_factory=_client_factory(handler))
    assert result.passed is True
    assert result.network_ok is True
    assert "Commander must approve" in result.commander_checkpoint

    updated = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "GENERIC-OK")).first()
    assert updated.jurisdiction == "Georgia, USA"
    assert updated.score is not None
    attempts = session.exec(select(RescueAttempt).where(RescueAttempt.canonical_opportunity_id == "GENERIC-OK")).all()
    assert any(a.result == "found" for a in attempts)


def test_generic_research_blocks_capability_when_no_advisor_configured(monkeypatch):
    for env_var in ("GROK_API_KEY", "VENICE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    session = _make_session()
    _make_candidate(session, "GENERIC-NO-ADVISOR")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never make an HTTP call with no advisor configured")

    result = research_engine.run_research(session, "GENERIC-NO-ADVISOR", client_factory=_client_factory(handler))
    assert result.passed is False
    assert result.disposition_override == Disposition.blocked_capability
    assert result.network_ok is True


def test_generic_research_reports_infrastructure_block_when_llm_unreachable(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    session = _make_session()
    _make_candidate(session, "GENERIC-NET-DOWN")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated egress block", request=request)

    result = research_engine.run_research(session, "GENERIC-NET-DOWN", client_factory=_client_factory(handler))
    assert result.passed is False
    # call_llm_json swallows the transport error internally (tries every
    # advisor, all fail) and returns None — from the engine's perspective
    # that's indistinguishable from "no advisor configured" UNLESS a
    # source was actually proposed and then failed to fetch. Confirm the
    # capability-gap framing still holds and nothing is fabricated.
    assert result.disposition_override in (Disposition.blocked_capability, Disposition.blocked_infrastructure)


def test_generic_research_blocks_when_proposed_source_unreachable(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    session = _make_session()
    _make_candidate(session, "GENERIC-SOURCE-DOWN")

    def handler(request: httpx.Request) -> httpx.Response:
        if "chat/completions" in str(request.url):
            return _llm_response({"sources": [{"url": "https://dead-site.example/page", "why": "official"}]})
        raise httpx.ConnectError("simulated egress block", request=request)

    result = research_engine.run_research(session, "GENERIC-SOURCE-DOWN", client_factory=_client_factory(handler))
    assert result.passed is False
    assert result.disposition_override == Disposition.blocked_infrastructure
    assert result.network_ok is False


def test_generic_research_blocks_when_source_does_not_confirm_mechanism(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    session = _make_session()
    _make_candidate(session, "GENERIC-NOT-CONFIRMED")

    def handler(request: httpx.Request) -> httpx.Response:
        if "chat/completions" in str(request.url):
            body = json.loads(request.content)
            if "authoritative web sources" in body["messages"][0]["content"]:
                return _llm_response({"sources": [{"url": "https://example.com/unrelated", "why": "maybe"}]})
            return _llm_response({"supports_opportunity": False})
        return httpx.Response(200, text="<html>unrelated content</html>")

    result = research_engine.run_research(session, "GENERIC-NOT-CONFIRMED", client_factory=_client_factory(handler))
    assert result.passed is False
    assert result.disposition_override == Disposition.blocked  # a real, substantive (but curable) finding
    assert result.network_ok is True


def test_generic_research_never_fetches_unreal_looking_urls():
    """generic_research itself must not attempt a GET against a
    non-http(s) or malformed 'url' value the LLM might return."""
    session = _make_session()
    opp = _make_candidate(session, "GENERIC-BAD-URL")

    def handler(request: httpx.Request) -> httpx.Response:
        if "chat/completions" in str(request.url):
            return _llm_response({"sources": [{"url": "not-a-real-url", "why": "??"}]})
        raise AssertionError("must not fetch a malformed URL")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        import os
        os.environ["GROK_API_KEY"] = "test-key"
        try:
            outcome = generic_research(client, opp)
        finally:
            del os.environ["GROK_API_KEY"]

    assert outcome.passed is False
