"""
Seeds today's named candidates and formal gates directly from the text of
the Claude Hunter Implementation Addendum (2026-09-08). Every field here
traces to that document — nothing here is invented data, and none of the
five candidates, two replacements, or two gates are pre-scored, pre-
verified, or pre-decided. They land as SCREENED_ONLY (or WATCHLIST for
the trademark case, which the addendum requires testing rather than
outright rejecting) with `next_action` set to the real verification work
still required.

The full 149-post baseline manifest is a separate, larger import (see
baseline_manifest.py) that requires the actual
`Hunter_Opportunity_Queue_2026-09-08.md` source content, which is not
reproduced in the addendum text — this seed does not fabricate it.
"""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition, GateVerdict, RescueAttempt, RescueResult, RescueType
from app.services import execution_accounting as acct
from app.services import formal_gates

ADDENDUM_SOURCE = "Claude Hunter Implementation Addendum 2026-09-08 — Today's candidate queue"
ADDENDUM_DATE = date(2026, 9, 8)

_TRADEMARK_ID = "HUNTER-CAND-2026-09-08-07-TRADEMARK"

_CANDIDATES = [
    dict(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-01-UCP",
        lane="compliance_recovery",
        factual_mechanism=(
            "Georgia corporate unclaimed-property recovery: state-held funds "
            "owed to a business can be reclaimed through the official Georgia "
            "unclaimed-property process, in some cases via a licensed/bonded "
            "finder acting under a fee-capped recovery agreement."
        ),
        eligibility="Requires verified GA registration, disclosure, and authorization/POA — not yet confirmed.",
        jurisdiction="Georgia, USA",
        required_commander_checkpoints=(
            "Contract/authorization signature, POA, fee-cap compliance, and "
            "claimant payment routing. Hunter must not impersonate an entity "
            "or officer, create a look-alike entity, submit a claim without "
            "authorization, receive the owner's property, or charge above "
            "the lawful fee cap."
        ),
        next_action="Validate GA registration/contract/disclosure/fee-cap/official-data-access requirements; persist a real verifiable business-property match if one exists.",
    ),
    dict(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
        lane="legal_claims",
        factual_mechanism=(
            "Google Incognito individual-damages route: distinct from the "
            "prior Brown settlement (must not be represented as an open cash "
            "claim). Requires identifying a genuinely open law-firm or "
            "official intake for individual damages."
        ),
        eligibility="Requires Georgia applicability or an explicit attorney-review issue — not yet confirmed.",
        jurisdiction="Georgia, USA (pending attorney-review confirmation)",
        required_commander_checkpoints=(
            "No retainer, filing, personal-data submission, arbitration "
            "acceptance, or signature without Commander approval. Statutory "
            "maximums must not be treated as expected recovery."
        ),
        next_action="Identify a genuinely open route with current case evidence, eligibility checklist, and real fee/cost terms.",
    ),
    dict(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-03-PUBLICRECORDS",
        lane="data_service",
        factual_mechanism="Public Records Glitch / Compliance-Data Service — flagged from the systemcracker_1 source feed; economic mechanism not yet verified.",
        eligibility="Not yet verified.",
        jurisdiction="US — not yet verified.",
        next_action="Validate the current lawful implementation of this mechanism before scoring.",
    ),
    dict(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-04-DARKPOOL",
        lane="trading",
        factual_mechanism="Dark Pool Glitch / FINRA Market-Data Strategy — flagged from the systemcracker_1 source feed; economic mechanism not yet verified.",
        eligibility="Not yet verified.",
        jurisdiction="US — not yet verified.",
        next_action="Validate the current lawful implementation and data-source legality before scoring.",
    ),
    dict(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-05-QRRE",
        lane="service",
        factual_mechanism="QR Code Real Estate Glitch — flagged from the systemcracker_1 source feed; economic mechanism not yet verified.",
        eligibility="Not yet verified.",
        jurisdiction="US — not yet verified.",
        next_action="Validate the current lawful implementation before scoring.",
    ),
    dict(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-06-GHOSTJOB",
        lane="service",
        factual_mechanism="Ghost Job Agency Glitch — next visible replacement candidate; economic mechanism not yet verified.",
        eligibility="Not yet verified.",
        jurisdiction="US — not yet verified.",
        next_action="Validate the current lawful implementation before scoring.",
    ),
    dict(
        canonical_opportunity_id=_TRADEMARK_ID,
        lane="service",
        factual_mechanism=(
            "Trademark Hijack Glitch — the hijack tactic itself is prohibited "
            "and is rejected outright. Per addendum instruction, this does "
            "not automatically reject the opportunity: lawful adjacent "
            "models (trademark monitoring, brand-protection, referral, "
            "registration-assistance) are tested before any permanent "
            "rejection of the underlying opportunity."
        ),
        eligibility="Not yet verified.",
        jurisdiction="US — not yet verified.",
        compliance_risk="Red/Yellow — 'Trademark Hijack' phrase triggers automatic legal screening.",
        legal_risk="Prohibited hijack tactic rejected; lawful adjacent service models under test.",
        next_action="Pursue lawful adjacent models: monitoring, brand-protection, referral, registration-assistance.",
    ),
]

_GATES = [
    dict(
        gate_id="HUNTER-OPP-2026-09-08-UCP-01",
        title="Georgia corporate unclaimed-property recovery",
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-01-UCP",
    ),
    dict(
        gate_id="HUNTER-OPP-2026-09-08-GOOGLE-02",
        title="Google Incognito individual-damages route",
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-02-GOOGLE",
    ),
]

_GATE_PENDING_NOTE = (
    "Gate stood up from the addendum's formal-gate text. Verdict withheld — "
    "PASS/FAIL requires real registration/contract/fee-cap research (UCP-01) "
    "or law-firm-intake/case-evidence research (GOOGLE-02) that has not yet "
    "been performed in this session."
)


def seed_addendum_candidates(session: Session) -> list[CanonicalOpportunity]:
    """Idempotently create today's named candidates and gates. Safe to
    call repeatedly — existing rows are left as-is (their disposition may
    have moved on since seeding)."""
    created: list[CanonicalOpportunity] = []

    for spec in _CANDIDATES:
        cid = spec["canonical_opportunity_id"]
        existing = session.exec(
            select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == cid)
        ).first()
        if existing:
            created.append(existing)
            continue

        opp = CanonicalOpportunity(
            canonical_opportunity_id=cid,
            lane=spec["lane"],
            factual_mechanism=spec["factual_mechanism"],
            source_provenance=ADDENDUM_SOURCE,
            freshness_date=ADDENDUM_DATE,
            eligibility=spec.get("eligibility"),
            jurisdiction=spec.get("jurisdiction"),
            compliance_risk=spec.get("compliance_risk"),
            legal_risk=spec.get("legal_risk"),
            required_commander_checkpoints=spec.get("required_commander_checkpoints"),
            next_action=spec.get("next_action"),
            disposition=Disposition.screened_only.value,
        )
        session.add(opp)
        session.commit()
        session.refresh(opp)
        created.append(opp)

    _seed_trademark_rescue_attempt(session)

    for gate_spec in _GATES:
        formal_gates.upsert_gate(
            session,
            gate_spec["gate_id"],
            gate_spec["title"],
            canonical_opportunity_id=gate_spec["canonical_opportunity_id"],
            verdict=GateVerdict.pending_evidence,
            evidence_summary=_GATE_PENDING_NOTE,
            counts_toward_quota=False,
        )

    return created


def _seed_trademark_rescue_attempt(session: Session) -> None:
    has_attempt = session.exec(
        select(RescueAttempt).where(RescueAttempt.canonical_opportunity_id == _TRADEMARK_ID)
    ).first()
    if not has_attempt:
        acct.record_rescue_attempt(
            session,
            _TRADEMARK_ID,
            RescueType.lawful_current_version.value,
            description=(
                "Rejected the prohibited trademark-hijack tactic itself. "
                "Testing lawful adjacent models: trademark monitoring, "
                "brand-protection, referral, and registration-assistance "
                "services before rejecting the opportunity."
            ),
            result=RescueResult.pending,
        )

    opp = session.exec(
        select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == _TRADEMARK_ID)
    ).first()
    if opp and opp.disposition == Disposition.screened_only.value:
        opp.disposition = Disposition.watchlist.value
        session.add(opp)
        session.commit()
