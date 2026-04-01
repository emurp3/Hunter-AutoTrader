from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.strategy import Strategy, StrategyCreate, StrategyUpdate
from app.services import strategies as strategy_svc

router = APIRouter(prefix="/strategies", tags=["strategies"])


# Literal routes BEFORE parameterized routes
@router.get("/")
def list_strategies(session: Session = Depends(get_session)):
    return list(session.exec(select(Strategy)).all())


@router.get("/active")
def list_active_strategies(session: Session = Depends(get_session)):
    """Return only active employed strategies (counts toward weekly quota)."""
    return strategy_svc.get_active_strategies(session)


@router.get("/quota")
def quota_status(session: Session = Depends(get_session)):
    return strategy_svc.check_quota(session)


@router.get("/weekly")
def weekly_status(session: Session = Depends(get_session)):
    """Full weekly strategy ledger: active count, activated/retired this week, quota met, replacements required."""
    return strategy_svc.get_weekly_status(session)


@router.post("/")
def create_strategy(data: StrategyCreate, session: Session = Depends(get_session)):
    return strategy_svc.create_strategy(data, session)


@router.post("/auto-promote")
def auto_promote(session: Session = Depends(get_session)):
    promoted = strategy_svc.auto_promote_candidates(session)
    return {"promoted": len(promoted), "strategies": promoted}


@router.get("/discovery-quota")
def discovery_quota(session: Session = Depends(get_session)):
    """Weekly source discovery quota status — how many new sources found vs required."""
    return strategy_svc.check_source_discovery_quota(session)


@router.post("/activate/by-source/{source_id}")
def activate_by_source(source_id: str, session: Session = Depends(get_session)):
    """Activate the strategy linked to a given income source_id."""
    strategy = strategy_svc.activate_strategy_for_source(source_id, session)
    if not strategy:
        raise HTTPException(
            status_code=404,
            detail=f"No strategy found linked to source_id '{source_id}'. Create one first via POST /strategies/from-opportunity/{source_id}."
        )
    return strategy


@router.post("/from-opportunity/{source_id}")
def create_from_opportunity(source_id: str, session: Session = Depends(get_session)):
    """Create a candidate strategy pre-linked to an income source, using its data as defaults."""
    try:
        return strategy_svc.create_strategy_from_opportunity(source_id, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# Parameterized routes AFTER all literals
@router.get("/{strategy_id}")
def get_strategy(strategy_id: str, session: Session = Depends(get_session)):
    strategy = session.exec(select(Strategy).where(Strategy.strategy_id == strategy_id)).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@router.post("/{strategy_id}/activate")
def activate_strategy(strategy_id: str, session: Session = Depends(get_session)):
    strategy = strategy_svc.activate_strategy(strategy_id, session)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@router.patch("/{strategy_id}")
def update_strategy(strategy_id: str, data: StrategyUpdate, session: Session = Depends(get_session)):
    strategy = session.exec(select(Strategy).where(Strategy.strategy_id == strategy_id)).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    update_data = data.model_dump(exclude_unset=True)
    was_stale = strategy.status == "underperforming" and (
        strategy.reason_for_continuation_or_termination or ""
    ).startswith("Auto-flagged:")

    for key, value in update_data.items():
        setattr(strategy, key, value)

    # Auto-recover from auto-flagged staleness when evidence is supplied
    if (
        was_stale
        and "evidence_of_activity" in update_data
        and update_data["evidence_of_activity"]
        and "status" not in update_data  # commander hasn't explicitly set a different status
    ):
        strategy.status = "active"
        strategy.reason_for_continuation_or_termination = None

    strategy.updated_at = datetime.now(timezone.utc)
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy
