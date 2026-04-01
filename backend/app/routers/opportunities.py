from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.income_source import IncomeSource, IncomeSourceCreate, IncomeSourceUpdate, PriorityBand, SourceStatus
from app.services.scoring import score_opportunity

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


# Literal routes BEFORE parameterized routes
@router.get("/ranked", response_model=List[IncomeSource])
def list_opportunities_ranked(session: Session = Depends(get_session)):
    """Return all income sources ordered by score descending."""
    return session.exec(
        select(IncomeSource).order_by(IncomeSource.score.desc())
    ).all()


@router.get("/by-origin/{origin_module}", response_model=List[IncomeSource])
def list_by_origin(origin_module: str, session: Session = Depends(get_session)):
    """Return all income sources from a specific origin module (e.g. 'autotrader', 'manual')."""
    return session.exec(
        select(IncomeSource)
        .where(IncomeSource.origin_module == origin_module)
        .order_by(IncomeSource.score.desc())
    ).all()


@router.get("/", response_model=List[IncomeSource])
def list_opportunities(session: Session = Depends(get_session)):
    return session.exec(select(IncomeSource)).all()


@router.post("/", response_model=IncomeSource, status_code=201)
def create_opportunity(payload: IncomeSourceCreate, session: Session = Depends(get_session)):
    existing = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == payload.source_id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"source_id '{payload.source_id}' already exists")
    record = IncomeSource.model_validate(payload)
    sr = score_opportunity(record, session)
    record.score = sr.score
    record.priority_band = sr.priority_band
    record.score_rationale = sr.rationale
    session.add(record)
    session.commit()
    session.refresh(record)

    # Run orchestrator for elite/high manual entries
    if sr.priority_band in (PriorityBand.elite, PriorityBand.high):
        try:
            from app.services.orchestrator import process_new_opportunity
            process_new_opportunity(record, session)
        except Exception:
            pass  # Orchestrator failure must not block the create response

    return record


@router.get("/{source_id}", response_model=IncomeSource)
def get_opportunity(source_id: str, session: Session = Depends(get_session)):
    record = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return record


@router.patch("/{source_id}", response_model=IncomeSource)
def update_opportunity(
    source_id: str,
    payload: IncomeSourceUpdate,
    session: Session = Depends(get_session),
):
    record = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    old_band = record.priority_band
    old_status = record.status

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    # Full rescore — updates priority_band and score_rationale
    sr = score_opportunity(record, session)
    record.score = sr.score
    record.priority_band = sr.priority_band
    record.score_rationale = sr.rationale

    session.add(record)
    session.commit()
    session.refresh(record)

    # If band improved to elite/high and source hasn't been through orchestration yet,
    # run orchestrator to generate alerts and action packet
    newly_elite_or_high = (
        sr.priority_band in (PriorityBand.elite, PriorityBand.high)
        and old_band not in (PriorityBand.elite, PriorityBand.high)
    )
    if newly_elite_or_high and record.status not in (
        SourceStatus.review_ready, SourceStatus.budgeted, SourceStatus.active,
        SourceStatus.complete, SourceStatus.archived,
    ):
        try:
            from app.services.orchestrator import process_new_opportunity
            process_new_opportunity(record, session)
        except Exception:
            pass

    return record


@router.delete("/{source_id}", status_code=204)
def delete_opportunity(source_id: str, session: Session = Depends(get_session)):
    record = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    session.delete(record)
    session.commit()
