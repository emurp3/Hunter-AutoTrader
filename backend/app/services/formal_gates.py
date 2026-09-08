"""
Formal gate records (Claude Hunter Implementation Addendum, "Today's
formal gates"). A gate is evaluated independently of daily execution
credit — `counts_toward_quota` stays False unless a separately
authorized real-world execution with a receipt actually occurs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.hunter_ledger import FormalGate, GateVerdict


def upsert_gate(
    session: Session,
    gate_id: str,
    title: str,
    *,
    canonical_opportunity_id: Optional[str] = None,
    verdict: GateVerdict | str = GateVerdict.pending_evidence,
    evidence_summary: Optional[str] = None,
    receipts: Optional[str] = None,
    counts_toward_quota: bool = False,
) -> FormalGate:
    existing = session.exec(select(FormalGate).where(FormalGate.gate_id == gate_id)).first()
    verdict_value = GateVerdict(verdict).value
    if existing:
        existing.title = title
        existing.canonical_opportunity_id = canonical_opportunity_id or existing.canonical_opportunity_id
        existing.verdict = verdict_value
        existing.evidence_summary = evidence_summary
        existing.receipts = receipts
        existing.counts_toward_quota = counts_toward_quota
        existing.updated_at = datetime.now(timezone.utc)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    gate = FormalGate(
        gate_id=gate_id,
        title=title,
        canonical_opportunity_id=canonical_opportunity_id,
        verdict=verdict_value,
        evidence_summary=evidence_summary,
        receipts=receipts,
        counts_toward_quota=counts_toward_quota,
    )
    session.add(gate)
    session.commit()
    session.refresh(gate)
    return gate


def record_gate_verdict(
    session: Session,
    gate_id: str,
    verdict: GateVerdict | str,
    *,
    evidence_summary: str,
    receipts: Optional[str] = None,
) -> FormalGate:
    """A gate may only conclude PASS/FAIL with an evidence_summary behind
    it — no evidence-free verdicts."""
    verdict = GateVerdict(verdict)
    if verdict in (GateVerdict.pass_, GateVerdict.fail) and not (evidence_summary and evidence_summary.strip()):
        raise ValueError("A PASS/FAIL gate verdict requires a non-empty evidence_summary.")

    gate = session.exec(select(FormalGate).where(FormalGate.gate_id == gate_id)).first()
    if not gate:
        raise ValueError(f"FormalGate '{gate_id}' not found")

    gate.verdict = verdict.value
    gate.evidence_summary = evidence_summary
    if receipts:
        gate.receipts = receipts
    gate.updated_at = datetime.now(timezone.utc)
    session.add(gate)
    session.commit()
    session.refresh(gate)
    return gate
