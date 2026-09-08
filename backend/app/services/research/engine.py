"""
Hunter's own research engine.

This is the ONLY sanctioned way a CanonicalOpportunity gets evidence,
eligibility notes, a current-lawful-implementation summary, a Commander
checkpoint, or a score attached to it. It is Hunter's machinery — invoked
by the quota-protection loop as its preflight step (see
make_research_preflight) — not something Claude fills in by hand from
manual browsing. Claude's job here is limited to: building/repairing this
pipeline, writing the provider connectors against real named sources, and
independently spot-checking a finding as QA (see the acceptance-test
runner in gate_runner.py).

Providers are plain callables `(httpx.Client) -> ResearchOutcome`
registered per canonical_opportunity_id — reserved for stable, recurring
sources that earn a dedicated connector. Every other candidate (the bulk
of a 149-item queue) falls through to the generic research path
(research/generic/provider.py), which uses Hunter's own already-
configured LLM advisors to propose sources and Hunter's own httpx client
to fetch and ground them. A candidate never fails preflight simply for
lacking a bespoke connector — only for a genuine, logged capability gap
(no advisor configured, nothing reachable) or a real network outage, and
those two are classified and reported distinctly (see
Disposition.blocked_infrastructure / .blocked_capability) rather than
being reported as a substantive rejection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from sqlmodel import Session, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.services import execution_accounting as acct
from app.services.hunter_ledger_scoring import score_candidate
from app.services.quota_loop import PreflightFn, PreflightResult
from app.services.research.base import ResearchOutcome, build_client
from app.services.research.generic.provider import generic_research
from app.services.research.providers.ga_unclaimed_property import ga_unclaimed_property_research
from app.services.research.providers.google_incognito import google_incognito_research

ProviderFn = Callable[[httpx.Client], ResearchOutcome]

PROVIDER_REGISTRY: dict[str, ProviderFn] = {
    "HUNTER-CAND-2026-09-08-01-UCP": ga_unclaimed_property_research,
    "HUNTER-CAND-2026-09-08-02-GOOGLE": google_incognito_research,
}


def register_provider(canonical_opportunity_id: str, provider: ProviderFn) -> None:
    """Wire a specialized connector for a candidate that warrants one
    (a stable, recurring, high-value source). Claude's job when repairing
    or extending Hunter's research capability — never a substitute for
    calling it. Most candidates should NOT need this — they use the
    generic research path automatically."""
    PROVIDER_REGISTRY[canonical_opportunity_id] = provider


def run_research(
    session: Session,
    canonical_opportunity_id: str,
    *,
    client_factory: Callable[[], httpx.Client] = build_client,
) -> PreflightResult:
    opp = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.canonical_opportunity_id == canonical_opportunity_id
        )
    ).first()
    if not opp:
        raise ValueError(f"CanonicalOpportunity '{canonical_opportunity_id}' not found")

    provider = PROVIDER_REGISTRY.get(canonical_opportunity_id)

    try:
        with client_factory() as client:
            outcome = provider(client) if provider is not None else generic_research(client, opp)
    except Exception as exc:  # a bug in the provider itself — a capability gap Claude must fix,
                              # never a judgment about the opportunity
        acct.record_rescue_attempt(
            session, canonical_opportunity_id, "alternate_channel",
            description=f"Research provider raised an unexpected error: {exc!r}",
            result="pending",
        )
        _append_evidence(opp, f"[research_engine error] provider raised {exc!r}")
        session.add(opp)
        session.commit()
        return PreflightResult(
            lawful=False, current=False, applicable=False,
            notes=f"Research provider error: {exc!r}",
            disposition_override=Disposition.blocked_capability,
            network_ok=True,
        )

    _persist_outcome(session, opp, outcome)

    if outcome.rescue_type:
        acct.record_rescue_attempt(
            session, canonical_opportunity_id, outcome.rescue_type,
            description=outcome.rescue_description or "research engine finding",
            result=outcome.rescue_result,
            evidence_reference=outcome.evidence_reference(),
        )

    opp.score = score_candidate(opp)
    opp.updated_at = datetime.now(timezone.utc)
    session.add(opp)
    session.commit()

    effective_override = _classify_failure(outcome) if not outcome.passed else None

    return PreflightResult(
        lawful=outcome.passed,
        current=outcome.passed,
        applicable=outcome.passed,
        commander_checkpoint=outcome.commander_checkpoint if outcome.passed else None,
        notes=outcome.evidence_reference(),
        disposition_override=effective_override,
        network_ok=outcome.network_reachable,
    )


def _classify_failure(outcome: ResearchOutcome) -> Disposition:
    """Distinguish WHY a research attempt didn't pass, so the accounting
    layer never conflates 'the network was down' or 'nothing is wired
    here yet' with an actual finding about the opportunity."""
    if not outcome.network_reachable:
        return Disposition.blocked_infrastructure
    if outcome.disposition_override is not None:
        return outcome.disposition_override
    if not outcome.findings:
        # No provider even attempted a fetch (e.g. generic path found no
        # sources to try) — that's a capability gap, not a real finding.
        return Disposition.blocked_capability
    # Reachable, evaluated, and genuinely didn't confirm the opportunity —
    # a real, substantive (but still curable/temporary) finding.
    return Disposition.blocked


def make_research_preflight(session: Session, *, client_factory: Callable[[], httpx.Client] = build_client) -> PreflightFn:
    """Build a PreflightFn (single-arg CanonicalOpportunity -> PreflightResult)
    bound to this session, for the quota loop to call as ITS preflight step.
    This is what makes the loop Hunter's own research run, not Claude's."""

    def _preflight(opp: CanonicalOpportunity) -> PreflightResult:
        return run_research(session, opp.canonical_opportunity_id, client_factory=client_factory)

    return _preflight


def _persist_outcome(session: Session, opp: CanonicalOpportunity, outcome: ResearchOutcome) -> None:
    if outcome.eligibility:
        opp.eligibility = outcome.eligibility
    if outcome.jurisdiction:
        opp.jurisdiction = outcome.jurisdiction
    if outcome.current_lawful_implementation:
        opp.current_lawful_implementation = outcome.current_lawful_implementation
    if outcome.execution_route:
        opp.next_action = f"Execution route confirmed by Hunter's research: {outcome.execution_route}"
    if outcome.commander_checkpoint:
        opp.required_commander_checkpoints = outcome.commander_checkpoint
    _append_evidence(opp, outcome.evidence_reference())
    session.add(opp)


def _append_evidence(opp: CanonicalOpportunity, note: str) -> None:
    stamped = f"[{datetime.now(timezone.utc).isoformat()}] {note}"
    opp.evidence_log = stamped if not opp.evidence_log else f"{opp.evidence_log}\n{stamped}"
