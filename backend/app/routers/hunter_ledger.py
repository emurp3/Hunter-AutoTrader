"""
API surface for the Hunter Implementation Addendum execution ledger.
Routers stay thin — all invariant enforcement lives in
app.services.execution_accounting / quota_loop / baseline_manifest /
formal_gates; this module only translates HTTP <-> those functions.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.hunter_ledger import CanonicalOpportunity, Disposition, FormalGate, GateVerdict
from app.services import baseline_manifest, execution_accounting as acct, formal_gates, hunter_eod_report
from app.services.hunter_addendum_seed import seed_addendum_candidates
from app.services.quota_loop import run_quota_protection_loop
from app.services.research import engine as research_engine
from app.services.research.gate_runner import run_gate_acceptance_test

router = APIRouter(prefix="/hunter-ops", tags=["hunter-ops"])


# ── Candidates ──────────────────────────────────────────────────────────


@router.get("/candidates")
def list_candidates(disposition: Optional[str] = None, session: Session = Depends(get_session)):
    stmt = select(CanonicalOpportunity)
    if disposition:
        stmt = stmt.where(CanonicalOpportunity.disposition == disposition)
    rows = session.exec(stmt.order_by(CanonicalOpportunity.score.desc())).all()
    return rows


@router.get("/commander-decisions")
def list_commander_decisions(session: Session = Depends(get_session)):
    """Every candidate currently waiting on a Commander checkpoint —
    the feed the chat widget uses to have Hunter ask first."""
    rows = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.disposition == Disposition.pending_commander.value
        )
    ).all()
    return [
        {
            "canonical_opportunity_id": o.canonical_opportunity_id,
            "lane": o.lane,
            "factual_mechanism": o.factual_mechanism,
            "checkpoint": o.required_commander_checkpoints,
            "commander_response": o.commander_response,
            "commander_responded_at": o.commander_responded_at,
        }
        for o in rows
        if o.commander_response is None  # already-answered ones drop off the feed
    ]


@router.post("/candidates/{canonical_opportunity_id}/commander-answer")
def answer_commander_checkpoint(
    canonical_opportunity_id: str,
    answer: str,
    decision: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Commander's reply to a checkpoint, from the chat widget or the API
    directly. `decision` is 'approve', 'decline', or omitted (just
    supplying requested info, e.g. a company name/FEIN)."""
    try:
        return acct.record_commander_answer(session, canonical_opportunity_id, answer, decision=decision)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/candidates/{canonical_opportunity_id}")
def get_candidate(canonical_opportunity_id: str, session: Session = Depends(get_session)):
    opp = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.canonical_opportunity_id == canonical_opportunity_id
        )
    ).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Canonical opportunity not found")
    return opp


@router.post("/candidates", response_model=CanonicalOpportunity, status_code=201)
def create_candidate(payload: CanonicalOpportunity, session: Session = Depends(get_session)):
    existing = session.exec(
        select(CanonicalOpportunity).where(
            CanonicalOpportunity.canonical_opportunity_id == payload.canonical_opportunity_id
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="canonical_opportunity_id already exists")
    payload.id = None
    session.add(payload)
    session.commit()
    session.refresh(payload)
    return payload


@router.post("/candidates/{canonical_opportunity_id}/rescue-attempts")
def add_rescue_attempt(
    canonical_opportunity_id: str,
    rescue_type: str,
    description: str,
    result: str = "pending",
    evidence_reference: Optional[str] = None,
    session: Session = Depends(get_session),
):
    return acct.record_rescue_attempt(
        session, canonical_opportunity_id, rescue_type, description,
        result=result, evidence_reference=evidence_reference,
    )


@router.post("/candidates/{canonical_opportunity_id}/research")
def run_candidate_research(canonical_opportunity_id: str, session: Session = Depends(get_session)):
    """Trigger Hunter's own research engine for one candidate — the same
    step the quota loop runs as its preflight. Not a Claude-authored
    result; this calls the actual provider connector."""
    try:
        result = research_engine.run_research(session, canonical_opportunity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "canonical_opportunity_id": canonical_opportunity_id,
        "passed": result.passed,
        "network_ok": result.network_ok,
        "commander_checkpoint": result.commander_checkpoint,
        "disposition_override": result.disposition_override.value if result.disposition_override else None,
        "notes": result.notes,
    }


@router.post("/candidates/{canonical_opportunity_id}/disposition")
def update_disposition(
    canonical_opportunity_id: str,
    disposition: str,
    evidence: Optional[str] = None,
    duplicate_of: Optional[str] = None,
    session: Session = Depends(get_session),
):
    try:
        return acct.set_disposition(
            session, canonical_opportunity_id, disposition,
            evidence=evidence, duplicate_of=duplicate_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ── Executions ──────────────────────────────────────────────────────────


@router.post("/executions", status_code=201)
def create_execution(
    canonical_opportunity_id: str,
    source: str,
    action_description: str,
    actions_taken: str,
    external_endpoint: str,
    receipt_reference: str,
    money_spent_committed: float = 0.0,
    expected_lawful_return: float = 0.0,
    expected_time_to_cash: Optional[str] = None,
    follow_up: Optional[str] = None,
    owner: str = "Hunter",
    due_date: Optional[date] = None,
    session: Session = Depends(get_session),
):
    try:
        return acct.record_execution(
            session,
            canonical_opportunity_id=canonical_opportunity_id,
            source=source,
            action_description=action_description,
            actions_taken=actions_taken,
            external_endpoint=external_endpoint,
            receipt_reference=receipt_reference,
            money_spent_committed=money_spent_committed,
            expected_lawful_return=expected_lawful_return,
            expected_time_to_cash=expected_time_to_cash,
            follow_up=follow_up,
            owner=owner,
            due_date=due_date,
        )
    except acct.SundayLockout as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except acct.MissingReceiptError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/executions/{execution_record_id}/settle")
def settle_execution(
    execution_record_id: int,
    realized_gross_cash: float,
    evidence_reference: str,
    settled_expenses: float = 0.0,
    session: Session = Depends(get_session),
):
    try:
        return acct.settle_cash(
            session, execution_record_id,
            realized_gross_cash=realized_gross_cash,
            evidence_reference=evidence_reference,
            settled_expenses=settled_expenses,
        )
    except acct.MissingReceiptError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── Quota / reporting ──────────────────────────────────────────────────


@router.get("/quota")
def get_quota(on: Optional[date] = None, session: Session = Depends(get_session)):
    return acct.get_quota_status(session, on)


@router.get("/eod-report")
def get_eod_report(on: Optional[date] = None, session: Session = Depends(get_session)):
    return hunter_eod_report.generate_eod_report(session, on)


@router.post("/loop/run")
def run_loop(on: Optional[date] = None, session: Session = Depends(get_session)):
    """Run the quota-protection loop with Hunter's own research engine as
    its preflight step (not a trivial always-pass stub) — this is Hunter
    running his own preflight, per the addendum's role boundary."""
    try:
        result = run_quota_protection_loop(
            session, day=on, preflight_fn=research_engine.make_research_preflight(session)
        )
    except acct.SundayLockout as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {
        "day": result.day.isoformat(),
        "verdict": result.verdict,
        "execution_count": result.execution_count,
        "receipts": result.receipts,
        "steps": [
            {"canonical_opportunity_id": s.canonical_opportunity_id, "outcome": s.outcome, "detail": s.detail}
            for s in result.steps
        ],
    }


# ── Formal gates ────────────────────────────────────────────────────────


@router.get("/gates")
def list_gates(session: Session = Depends(get_session)):
    return session.exec(select(FormalGate)).all()


@router.post("/gates/{gate_id}/run-acceptance-test")
def run_gate_research(gate_id: str, session: Session = Depends(get_session)):
    """Runs Hunter's research engine against this gate's linked candidate
    and derives the gate verdict from what Hunter actually found — the
    literal acceptance test of Hunter's research capability."""
    try:
        return run_gate_acceptance_test(session, gate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/gates/{gate_id}/verdict")
def set_gate_verdict(
    gate_id: str,
    verdict: str,
    evidence_summary: str,
    receipts: Optional[str] = None,
    session: Session = Depends(get_session),
):
    try:
        return formal_gates.record_gate_verdict(
            session, gate_id, verdict, evidence_summary=evidence_summary, receipts=receipts
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ── Baseline manifest / seed ───────────────────────────────────────────


@router.get("/baseline/summary")
def baseline_summary(session: Session = Depends(get_session)):
    return baseline_manifest.get_manifest_summary(session)


@router.post("/baseline/import")
def baseline_import(entries: list[dict], session: Session = Depends(get_session)):
    try:
        return baseline_manifest.freeze_baseline_manifest(session, entries)
    except baseline_manifest.ManifestIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/seed-addendum-candidates")
def seed_candidates(session: Session = Depends(get_session)):
    return seed_addendum_candidates(session)
