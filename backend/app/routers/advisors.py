from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.advisor import AdvisorInput, AdvisorName
from app.models.income_source import IncomeSource
from app.services import advisors as advisor_svc
from app.integration.advisor_bridge import call_advisor, call_all_advisors
from app.services import daily_opportunity as daily_opp_svc

router = APIRouter(prefix="/advisors", tags=["advisors"])


# ── Daily opportunity routes (literals before parameterized) ──────────────────

@router.get("/daily-opportunity/today")
def get_today(session: Session = Depends(get_session)):
    """Return today's advisor-generated opportunity. 404 if not yet generated."""
    opp = daily_opp_svc.get_today_opportunity(session)
    if not opp:
        return {
            "exists": False,
            "assigned_advisor": daily_opp_svc.get_day_owner(),
            "message": "No opportunity generated yet today. POST /advisors/daily-opportunity/generate to create one.",
        }
    return {
        "exists": True,
        "source_id": daily_opp_svc.get_source_id_for_opportunity(opp),
        "opportunity": opp,
    }


@router.post("/daily-opportunity/generate", status_code=201)
def generate_today(session: Session = Depends(get_session)):
    """
    Generate today's daily opportunity using the assigned advisor (with fallback).
    Idempotent — returns existing opportunity if already generated today.
    """
    try:
        opp = daily_opp_svc.generate_today_opportunity_and_sync(session)
        return {
            "opportunity": opp,
            "source_id": daily_opp_svc.get_source_id_for_opportunity(opp),
            "assigned_advisor": opp.assigned_advisor,
            "actual_advisor": opp.actual_advisor,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Opportunity generation failed: {exc}")


@router.get("/daily-opportunity/history")
def get_history(limit: int = 30, session: Session = Depends(get_session)):
    """Return recent daily opportunities, newest first."""
    history = daily_opp_svc.get_opportunity_history(session, limit=limit)
    return {"opportunities": history, "count": len(history)}


class OutcomeRequest(BaseModel):
    status: str  # succeeded | failed | dispatched | skipped
    actual_profit: Optional[float] = None
    notes: Optional[str] = None


@router.post("/daily-opportunity/{opp_id}/outcome")
def record_outcome(opp_id: int, body: OutcomeRequest, session: Session = Depends(get_session)):
    """Record the real outcome for a daily opportunity (updates weekly score)."""
    valid_statuses = {"succeeded", "failed", "dispatched", "skipped"}
    if body.status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"status must be one of: {valid_statuses}")
    try:
        opp = daily_opp_svc.mark_outcome(
            opp_id,
            body.status,
            session,
            actual_profit=body.actual_profit,
            notes=body.notes,
        )
        return {"opportunity": opp}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/scoreboard")
def get_scoreboard(session: Session = Depends(get_session)):
    """Return the current week's advisor performance scoreboard."""
    scores = daily_opp_svc.get_weekly_scoreboard(session)
    assigned_today = daily_opp_svc.get_day_owner()
    return {
        "week": daily_opp_svc._current_week_start().isoformat(),
        "assigned_today": assigned_today,
        "scores": scores,
        "winner": next((s for s in scores if s["is_winner"]), None),
    }


@router.get("/disagreements/list")
def get_disagreements(session: Session = Depends(get_session)):
    return {"disagreements": advisor_svc.get_disagreements(session)}


# ── Per-source advisor consultation ──────────────────────────────────────────

@router.get("/{source_id}")
def get_advisor_inputs(source_id: str, session: Session = Depends(get_session)):
    inputs = advisor_svc.get_inputs(source_id, session)
    consensus = advisor_svc.get_consensus(source_id, session)
    return {
        "source_id": source_id,
        "inputs": inputs,
        "consensus": consensus,
        "summary": advisor_svc.format_summary(source_id, session),
    }


@router.post("/{source_id}/consult")
def consult_advisors(
    source_id: str,
    advisor: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Call one or all advisors for this opportunity."""
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")

    if advisor:
        valid = {n.value for n in AdvisorName if n != AdvisorName.other}
        if advisor not in valid:
            raise HTTPException(status_code=422, detail=f"Unknown advisor. Must be one of: {valid}")
        try:
            result = call_advisor(advisor, source, session)
            return {"advisor": advisor, "result": result or "not_configured"}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Advisor call failed: {exc}")

    results = call_all_advisors(source, session)
    return {
        "source_id": source_id,
        "results": results,
        "consensus": advisor_svc.get_consensus(source_id, session),
    }
