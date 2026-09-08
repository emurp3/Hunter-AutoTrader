"""
Hunter's general-purpose research path — used whenever no specialized
connector is registered for a candidate (see research/providers/ for the
handful of stable, recurring sources that warrant a dedicated connector).
The 149-item baseline queue cannot require a bespoke Python module per
opportunity, so this is the reusable fallback every other candidate goes
through.

Two-step, both grounded in real fetches — never in an LLM's unverified
claim:
  1. Ask a configured advisor (Venice/DeepSeek/Grok — Hunter's own
     existing LLM integrations) to propose up to 3 candidate authoritative
     source URLs for the opportunity's claimed mechanism.
  2. Hunter's own httpx client fetches each proposed URL directly. Only
     text Hunter actually retrieved is ever passed back to an LLM, and
     only for EXTRACTION (summarizing what the fetched page says), never
     for asserting a fact standing alone.

Every branch that can't proceed — no advisor configured, advisor
proposed nothing, nothing reachable — returns an honest capability-gap
result. Nothing here invents a source or a finding to avoid that outcome.
"""

from __future__ import annotations

import httpx

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.services.research.base import EvidenceFinding, ResearchOutcome
from app.services.research.generic.llm_client import call_llm_json

_DISCOVERY_SYSTEM = (
    "You identify real, currently-existing, authoritative web sources "
    "(official government pages, primary legal sources, or a clearly "
    "identifiable named organization's own site) that would let someone "
    "verify whether a claimed opportunity mechanism is real, current, and "
    "lawful. Respond in JSON only: "
    '{"sources": [{"url": "https://...", "why": "..."}]}, at most 3 '
    "entries. If you are not confident a URL is real and correctly "
    "spelled, omit it rather than guess — an empty list is a valid answer."
)

_EXTRACTION_SYSTEM = (
    "Using ONLY the fetched page text provided below (not prior "
    "knowledge), extract what it actually establishes about this "
    "opportunity. Respond in JSON only: "
    '{"supports_opportunity": true|false, "eligibility": "...", '
    '"jurisdiction": "...", "current_lawful_implementation": "...", '
    '"commander_checkpoint": "..."}. If the page does not clearly discuss '
    "the opportunity, set supports_opportunity to false and leave the "
    "other fields null. Never state a fact the text does not support."
)

_MAX_SOURCES = 3
_MAX_FETCH_CHARS = 6000


def generic_research(client: httpx.Client, opp: CanonicalOpportunity) -> ResearchOutcome:
    discovery_prompt = (
        f"Opportunity lane: {opp.lane}\n"
        f"Claimed mechanism: {opp.factual_mechanism}\n"
        f"Known jurisdiction hint: {opp.jurisdiction or 'unspecified'}\n"
        "Identify authoritative sources to verify this."
    )
    discovery = call_llm_json(_DISCOVERY_SYSTEM, discovery_prompt, client=client)
    proposed = (discovery or {}).get("sources") or []

    if not proposed:
        return ResearchOutcome(
            passed=False,
            findings=[],
            disposition_override=Disposition.blocked_capability,
            rescue_type="alternate_channel",
            rescue_description=(
                "Generic research path found no configured advisor, or the "
                "advisor proposed no candidate sources, for this opportunity."
            ),
            rescue_result="pending",
        )

    findings: list[EvidenceFinding] = []
    fetched: list[tuple[str, str]] = []
    for entry in proposed[:_MAX_SOURCES]:
        url = str((entry or {}).get("url", "")).strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            continue
        try:
            resp = client.get(url)
            ok = resp.status_code == 200
            findings.append(EvidenceFinding(
                ok=ok, source_url=url,
                summary="reachable" if ok else f"unexpected HTTP status {resp.status_code}",
                http_status=resp.status_code,
            ))
            if ok:
                fetched.append((url, resp.text[:_MAX_FETCH_CHARS]))
        except httpx.HTTPError as exc:
            findings.append(EvidenceFinding(
                ok=False, source_url=url, summary="fetch failed at the transport layer",
                error=repr(exc), network_reachable=False,
            ))

    if not fetched:
        return ResearchOutcome(
            passed=False,
            findings=findings,
            rescue_type="alternate_channel",
            rescue_description="Advisor proposed sources but none were reachable on this attempt.",
            rescue_result="pending",
        )

    url, text = fetched[0]
    extraction = call_llm_json(
        _EXTRACTION_SYSTEM,
        f"Source URL: {url}\n\nFetched page text:\n{text}",
        client=client,
    )

    if not extraction or not extraction.get("supports_opportunity"):
        return ResearchOutcome(
            passed=False,
            findings=findings,
            disposition_override=Disposition.blocked,
            rescue_type="alternate_channel",
            rescue_description="Fetched a proposed source but it did not confirm the opportunity's mechanism.",
            rescue_result="not_found",
        )

    return ResearchOutcome(
        passed=True,
        findings=findings,
        eligibility=extraction.get("eligibility"),
        jurisdiction=extraction.get("jurisdiction"),
        current_lawful_implementation=extraction.get("current_lawful_implementation"),
        execution_route=url,
        commander_checkpoint=extraction.get("commander_checkpoint") or (
            "No explicit Commander checkpoint extracted by the generic "
            "path — treat any credentials, filings, payments, or "
            "signatures as requiring Commander approval by default."
        ),
        rescue_type="alternate_channel",
        rescue_description=f"Generic research path confirmed a source ({url}) supporting this opportunity.",
        rescue_result="found",
    )
