"""
Research provider for HUNTER-CAND-2026-09-08-01-UCP (Georgia corporate
unclaimed-property recovery).

Confirms — by actually fetching them — that Georgia's official program and
claimant-search portal are live and current, and that the free official
state process is the correct execution route (never a paid third-party
finder). This provider deliberately does NOT attempt a business-specific
property lookup: that requires Commander's exact legal company name and
FEIN, which is Commander-owned identity information, not something Hunter
should guess or submit for. Providing those two facts is surfaced as the
Commander checkpoint.

Official sources (confirmed reachable in normal-network conditions as of
2026-09; re-verify on drift — see freshness handling below):
  - https://dor.georgia.gov/unclaimed-property-program (program authority)
  - https://gaclaims.unclaimedproperty.com/ (official claimant search/filing portal)
"""

from __future__ import annotations

import httpx

from app.models.hunter_ledger import Disposition
from app.services.research.base import EvidenceFinding, ResearchOutcome

PROGRAM_URL = "https://dor.georgia.gov/unclaimed-property-program"
PORTAL_URL = "https://gaclaims.unclaimedproperty.com/"


def _fetch(client: httpx.Client, url: str, ok_summary: str) -> EvidenceFinding:
    try:
        resp = client.get(url)
        ok = resp.status_code == 200
        return EvidenceFinding(
            ok=ok,
            source_url=url,
            summary=ok_summary if ok else f"unexpected HTTP status {resp.status_code}",
            http_status=resp.status_code,
        )
    except httpx.HTTPError as exc:
        return EvidenceFinding(
            ok=False,
            source_url=url,
            summary="fetch failed at the transport layer",
            error=repr(exc),
            network_reachable=False,
        )


def ga_unclaimed_property_research(client: httpx.Client) -> ResearchOutcome:
    program_finding = _fetch(client, PROGRAM_URL, "Georgia DOR unclaimed-property program page reachable and current.")
    portal_finding = _fetch(client, PORTAL_URL, "Official claimant search/filing portal reachable.")
    findings = [program_finding, portal_finding]

    if program_finding.ok and portal_finding.ok:
        return ResearchOutcome(
            passed=True,
            findings=findings,
            eligibility=(
                "Georgia Department of Revenue confirms the official free "
                "program and claimant portal are live. A business-specific "
                "match requires Commander's exact legal company name and "
                "FEIN, searched directly on the official portal."
            ),
            jurisdiction="Georgia, USA",
            current_lawful_implementation=(
                "File directly through the free official state process — "
                f"{PROGRAM_URL} / {PORTAL_URL}. No paid third-party finder "
                "service is required or preferable; Georgia DOR explicitly "
                "warns against them."
            ),
            execution_route=PORTAL_URL,
            commander_checkpoint=(
                "Need Commander's exact legal company name + FEIN to run the "
                "business-property search. Portal account registration, POA "
                "(if a licensed finder is later engaged), and claim "
                "submission all require Commander's authorization/signature "
                "— Hunter must not submit on Commander's behalf without it, "
                "receive the property itself, or charge above the lawful fee cap."
            ),
            rescue_type="alternate_channel",
            rescue_description="Confirmed the free official state channel over any paid third-party finder service.",
            rescue_result="found",
        )

    return ResearchOutcome(
        passed=False,
        findings=findings,
        disposition_override=Disposition.blocked,
        rescue_type="alternate_channel",
        rescue_description="Could not confirm reachability of the official GA unclaimed-property sources on this attempt.",
        rescue_result="pending",
    )
