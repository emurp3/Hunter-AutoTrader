from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import Optional

from app.database.config import get_session
from app.models.advisor import AdvisorInput, AdvisorName
from app.models.income_source import IncomeSource
from app.services import advisors as advisor_svc
from app.integration.advisor_bridge import call_advisor, call_all_advisors

router = APIRouter(prefix="/advisors", tags=["advisors"])


# Literal routes BEFORE parameterized routes
@router.get("/disagreements/list")
def get_disagreements(session: Session = Depends(get_session)):
    return {"disagreements": advisor_svc.get_disagreements(session)}


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
def consult_advisors(source_id: str, advisor: Optional[str] = None, session: Session = Depends(get_session)):
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
