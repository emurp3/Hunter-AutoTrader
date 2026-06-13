from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.income_source import IncomeSource, IncomeSourceCreate, IncomeSourceUpdate, PriorityBand, SourceStatus
from app.services.scoring import score_opportunity

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


# ---------------------------------------------------------------------------
# Execution-honesty classifier
#
# Commander's standing rule: no fake numbers, no "budgeted" language when
# nothing can actually execute. An opportunity's dollar estimate is only
# truthful if Hunter has a REAL, wired platform that can act on it.
#
#   trading  -> Alpaca (connected, live, proven fills)        => executable
#   merch    -> Printful + Etsy store (wired via /store)       => executable
#   anything else (resale/consulting/referral/flip/digital/    => NOT executable
#     local_pitch/outreach/affiliate_content/...)                 manual projection only
# ---------------------------------------------------------------------------

_TRADING_SIGNALS = ("trading", "autotrader", "alpaca", "daily_opportunity", "crypto")
_MERCH_SIGNALS = ("merch", "printful", "store", "leon", "product")


def classify_executability(source: IncomeSource) -> dict:
    """Return an honesty annotation for an opportunity.

    executable          True only when a real platform can act on it autonomously.
    execution_platform  'alpaca' | 'printful' | None
    revenue_status      'live_executable' | 'manual_projection'
    revenue_note        plain-language truth for the UI.
    """
    haystack = " ".join(
        str(v or "").lower()
        for v in (source.category, source.origin_module, source.notes)
    )
    if any(sig in haystack for sig in _TRADING_SIGNALS):
        return {
            "executable": True,
            "execution_platform": "alpaca",
            "revenue_status": "live_executable",
            "revenue_note": "Trading lane — Hunter can execute this on Alpaca.",
        }
    if any(sig in haystack for sig in _MERCH_SIGNALS):
        return {
            "executable": True,
            "execution_platform": "printful",
            "revenue_status": "live_executable",
            "revenue_note": "Merch lane — listable via Printful + Etsy store.",
        }
    return {
        "executable": False,
        "execution_platform": None,
        "revenue_status": "manual_projection",
        "revenue_note": (
            "Idea only. Estimate is a manual projection — no wired platform. "
            "Hunter cannot execute this; a human must. Not committed revenue."
        ),
    }


def _annotate(source: IncomeSource) -> dict:
    payload = source.model_dump()
    payload.update(classify_executability(source))
    return payload


# Literal routes BEFORE parameterized routes
@router.get("/ranked")
def list_opportunities_ranked(session: Session = Depends(get_session)):
    """All income sources by score desc, each annotated with execution honesty."""
    rows = session.exec(select(IncomeSource).order_by(IncomeSource.score.desc())).all()
    return [_annotate(r) for r in rows]


@router.get("/by-origin/{origin_module}")
def list_by_origin(origin_module: str, session: Session = Depends(get_session)):
    rows = session.exec(
        select(IncomeSource)
        .where(IncomeSource.origin_module == origin_module)
        .order_by(IncomeSource.score.desc())
    ).all()
    return [_annotate(r) for r in rows]


@router.get("/")
def list_opportunities(session: Session = Depends(get_session)):
    return [_annotate(r) for r in session.exec(select(IncomeSource)).all()]


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

    if sr.priority_band in (PriorityBand.elite, PriorityBand.high):
        try:
            from app.services.orchestrator import process_new_opportunity
            process_new_opportunity(record, session)
        except Exception:
            pass

    return record


@router.get("/{source_id}")
def get_opportunity(source_id: str, session: Session = Depends(get_session)):
    record = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return _annotate(record)


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

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    sr = score_opportunity(record, session)
    record.score = sr.score
    record.priority_band = sr.priority_band
    record.score_rationale = sr.rationale

    session.add(record)
    session.commit()
    session.refresh(record)

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
