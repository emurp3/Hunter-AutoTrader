"""
Decision engine endpoints.

GET  /decisions/                        — list decisions (filterable)
GET  /decisions/{source_id}             — single decision lookup
POST /decisions/run                     — batch run engine across all scored sources
POST /decisions/{source_id}/decide      — decide for one source
POST /decisions/{source_id}/approve     — clear approval gate
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.database.config import get_session
from app.services import decision as decision_svc

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("/")
def list_decisions(
    action_state: str | None = Query(default=None),
    execution_path: str | None = Query(default=None),
    execution_ready: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    """List decisions, optionally filtered by state, path, or readiness."""
    decisions = decision_svc.list_decisions(
        session,
        action_state=action_state,
        execution_path=execution_path,
        execution_ready=execution_ready,
        limit=limit,
    )
    return {
        "total": len(decisions),
        "decisions": [d.to_dict() for d in decisions],
    }


@router.post("/run")
def run_decisions(
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    """
    Batch run the decision engine across all scored opportunities.
    Returns counts by action state.
    """
    return decision_svc.run_decisions(session, limit=limit)


@router.get("/{source_id}")
def get_decision(source_id: str, session: Session = Depends(get_session)) -> dict:
    """Get the current decision for a single source."""
    decision = decision_svc.get_decision(source_id, session)
    if not decision:
        raise HTTPException(status_code=404, detail=f"No decision found for {source_id}")
    return decision.to_dict()


@router.post("/{source_id}/decide")
def decide_for_source(source_id: str, session: Session = Depends(get_session)) -> dict:
    """Run the decision engine for a single source."""
    from sqlmodel import select
    from app.models.income_source import IncomeSource

    source = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail=f"No source found: {source_id}")

    decision = decision_svc.decide(source, session)
    return decision.to_dict()


@router.post("/{source_id}/approve")
def approve_decision(
    source_id: str,
    reviewer_note: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Clear the approval gate on a decision, marking it execution_ready."""
    decision = decision_svc.approve_decision(source_id, reviewer_note, session)
    if not decision:
        raise HTTPException(status_code=404, detail=f"No decision found for {source_id}")
    return decision.to_dict()
