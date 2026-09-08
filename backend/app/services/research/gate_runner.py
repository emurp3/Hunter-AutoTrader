"""
Formal-gate acceptance-test runner: uses today's two formal gates
(UCP-01, GOOGLE-02) as literal acceptance tests of Hunter's research
capability. Hunter (via research_engine.run_research) gathers the
evidence and this function derives the gate record from what Hunter
actually found — it never assigns PASS/FAIL from anything Claude
determined manually. Claude's role is to run this, inspect the result,
and fix the pipeline if it fails — not to hand-author the verdict.
"""

from __future__ import annotations

from typing import Callable

import httpx
from sqlmodel import Session, select

from app.models.hunter_ledger import CanonicalOpportunity, FormalGate, GateVerdict
from app.services import formal_gates
from app.services.research import engine as research_engine
from app.services.research.base import build_client


def run_gate_acceptance_test(
    session: Session,
    gate_id: str,
    *,
    client_factory: Callable[[], httpx.Client] = build_client,
) -> FormalGate:
    gate = session.exec(select(FormalGate).where(FormalGate.gate_id == gate_id)).first()
    if not gate:
        raise ValueError(f"FormalGate '{gate_id}' not found")
    if not gate.canonical_opportunity_id:
        raise ValueError(f"FormalGate '{gate_id}' has no linked canonical_opportunity_id")

    pre = research_engine.run_research(session, gate.canonical_opportunity_id, client_factory=client_factory)
    opp = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.canonical_opportunity_id == gate.canonical_opportunity_id
        )
    ).first()

    if not pre.network_ok:
        return formal_gates.upsert_gate(
            session, gate_id, gate.title,
            canonical_opportunity_id=gate.canonical_opportunity_id,
            verdict=GateVerdict.pending_evidence,
            evidence_summary=(
                "Hunter's research pipeline ran but could not reach its "
                "target sources from this environment (network egress "
                "blocked at the infrastructure level) — this is an "
                "incomplete-evidence result, not a negative finding. "
                "Verdict withheld pending a rerun from an environment with "
                "normal internet access. Attempted sources: " + pre.notes
            ),
            receipts=None,
            counts_toward_quota=False,
        )

    if pre.passed and opp and opp.eligibility and opp.current_lawful_implementation and opp.required_commander_checkpoints:
        evidence_summary = (
            f"Mechanism: {opp.current_lawful_implementation} | "
            f"Eligibility: {opp.eligibility} | "
            f"Commander checkpoint identified: {opp.required_commander_checkpoints} | "
            f"Raw findings: {pre.notes}"
        )
        return formal_gates.record_gate_verdict(
            session, gate_id, GateVerdict.pass_,
            evidence_summary=evidence_summary,
            receipts=None,
        )

    evidence_summary = (
        "Hunter's research completed but did not clear the gate's bar "
        f"(mechanism/eligibility/checkpoint incomplete, or the primary "
        f"route was not confirmed). Raw findings: {pre.notes}"
    )
    return formal_gates.record_gate_verdict(
        session, gate_id, GateVerdict.fail,
        evidence_summary=evidence_summary,
        receipts=None,
    )
