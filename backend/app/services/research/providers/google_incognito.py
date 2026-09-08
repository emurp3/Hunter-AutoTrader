"""
Research provider for HUNTER-CAND-2026-09-08-02-GOOGLE (Google Incognito
individual-damages route).

Encoded legal-status fact (verified 2026-09-08, re-verify periodically —
appellate rulings and settlements change): Brown v. Google (the Incognito
privacy class action) settled for data-deletion/policy changes only — there
is NO class-wide cash fund and NO claim form. The Ninth Circuit affirmed in
April 2026 that no class-wide damages were ever certified. The live
mechanism for an individual is a SEPARATE state-court suit; 2,200+ such
suits (370,000+ plaintiffs) are pending in California as of mid-2026. This
provider's live check is whether a specific individual-damages intake
route is still reachable and showing an active call to action — that part
DOES need to be re-verified on every run, since law-firm intake status
changes.

Primary source checked (confirmed offering free case reviews for this
exact issue as of 2026-09-08):
  - https://potterhandy.com/google-privacy-violations-lawsuit/
"""

from __future__ import annotations

import httpx

from app.models.hunter_ledger import Disposition
from app.services.research.base import EvidenceFinding, ResearchOutcome

INTAKE_URL = "https://potterhandy.com/google-privacy-violations-lawsuit/"

_OPEN_INTAKE_SIGNALS = ("free case review", "case evaluation", "contact us", "get started")

_LEGAL_STATUS_NOTE = (
    "Brown v. Google (Incognito) settlement provides NO class-wide cash "
    "fund — relief was data deletion/policy changes only, affirmed by the "
    "9th Circuit (Apr 2026); do not represent it as an open cash claim. "
    "Individual damages require a separate state-court suit — 2,200+ "
    "pending in California as of mid-2026."
)


def google_incognito_research(client: httpx.Client) -> ResearchOutcome:
    try:
        resp = client.get(INTAKE_URL)
        ok = resp.status_code == 200
        body = resp.text.lower() if ok else ""
        signals_open = ok and any(kw in body for kw in _OPEN_INTAKE_SIGNALS)
        finding = EvidenceFinding(
            ok=signals_open,
            source_url=INTAKE_URL,
            summary=(
                "Law firm intake page reachable with an active case-review call to action."
                if signals_open
                else "Reachable but no open-intake signal found on the page." if ok
                else f"unexpected HTTP status {resp.status_code}"
            ),
            http_status=resp.status_code,
        )
    except httpx.HTTPError as exc:
        signals_open = False
        finding = EvidenceFinding(
            ok=False,
            source_url=INTAKE_URL,
            summary="fetch failed at the transport layer",
            error=repr(exc),
            network_reachable=False,
        )

    findings = [finding]

    if signals_open:
        return ResearchOutcome(
            passed=True,
            findings=findings,
            eligibility=(
                "Requires Commander to confirm Chrome Incognito use during "
                "the alleged collection window and that the claim can be "
                "brought under Georgia venue/choice-of-law or another "
                "applicable state — needs attorney-review confirmation."
            ),
            jurisdiction="Individual state-court suit — Georgia applicability requires attorney confirmation.",
            current_lawful_implementation=_LEGAL_STATUS_NOTE,
            execution_route=INTAKE_URL,
            commander_checkpoint=(
                "No retainer, filing, personal-data submission, arbitration "
                "acceptance, or signature without Commander's explicit "
                "approval. Fee/cost terms (typically contingency-based) are "
                "disclosed by the firm after intake — request them explicitly "
                "rather than assuming a rate."
            ),
            rescue_type="alternate_channel",
            rescue_description=(
                "Identified a currently reachable individual-damages intake "
                "distinct from the closed, no-cash-fund Brown settlement."
            ),
            rescue_result="found",
        )

    return ResearchOutcome(
        passed=False,
        findings=findings,
        disposition_override=Disposition.blocked,
        rescue_type="alternate_channel",
        rescue_description="Primary law-firm intake route not confirmed reachable/open on this attempt.",
        rescue_result="pending",
    )
