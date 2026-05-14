"""
Signals router — Public-Signal Copy Engine API.
"/signals" endpoints.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select
from app.database.config import engine, get_session
from app.models.copy_signal import CopySignal
from app.services import signal_engine as svc
from app.auth.jwt import get_current_user
from app.auth.models import UserInDB

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/summary")
def signal_summary(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """High-level summary: counts by decision, top mirror candidates."""
    return svc.get_signal_summary(session)


@router.post("/scan")
def trigger_scan(
    background: BackgroundTasks,
    days_back: int = Query(default=30, ge=1, le=90),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Trigger a background scan of all configured public disclosure sources."""
    def _run():
        with Session(engine) as s:
            svc.run_signal_scan(s, days_back=days_back)
    background.add_task(_run)
    return {"status": "scan_queued", "days_back": days_back}


@router.get("/feed")
def signal_feed(
    decision: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Paginated signal feed with optional decision/source filter."""
    q = select(CopySignal).order_by(CopySignal.created_at.desc())
    if decision:
        q = q.where(CopySignal.decision == decision)
    if source:
        q = q.where(CopySignal.source == source)
    signals = session.exec(q.limit(limit)).all()
    return {
        "count": len(signals),
        "signals": [
            {
                "id": s.id, "ticker": s.ticker, "source": s.source,
                "filer": s.filer_name, "filer_type": s.filer_type,
                "action": s.action, "decision": s.decision,
                "confidence": s.confidence_score, "risk": s.risk_level,
                "amount": s.amount_midpoint, "latency_hours": s.latency_hours,
                "decision_reason": s.decision_reason, "executed": s.executed,
                "disclosed_at": s.disclosed_at.isoformat() if s.disclosed_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in signals
        ],
    }


class ExecuteRequest(BaseModel):
    notes: Optional[str] = None


@router.post("/{signal_id}/execute")
def mark_executed(
    signal_id: int,
    body: ExecuteRequest = ExecuteRequest(),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Mark a mirror/partial_mirror signal as executed by Commander."""
    signal = session.get(CopySignal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.decision not in ("mirror", "partial_mirror"):
        raise HTTPException(status_code=400, detail="Only mirror/partial_mirror signals can be executed")
    signal.executed = True
    signal.execution_at = datetime.utcnow()
    if body.notes:
        signal.notes = body.notes
    session.add(signal)
    session.commit()
    return {"status": "executed", "signal_id": signal_id}


@router.post("/{signal_id}/override-decision")
def override_decision(
    signal_id: int,
    decision: str,
    reason: Optional[str] = None,
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Commander override: change routing decision for a signal."""
    valid = {"mirror", "partial_mirror", "watchlist", "reject"}
    if decision not in valid:
        raise HTTPException(status_code=400, detail=f"Decision must be one of {valid}")
    signal = session.get(CopySignal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    signal.decision = decision
    signal.decision_reason = reason or f"Commander override at {datetime.utcnow().isoformat()}"
    signal.decision_at = datetime.utcnow()
    session.add(signal)
    session.commit()
    return {"status": "updated", "decision": decision}


@router.get("/vip-watchlist")
def vip_watchlist(
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """VIP politician watchlist and micro-invest settings."""
    from app.services.signal_engine import get_vip_watchlist, VIP_MICRO_INVEST_AMOUNT, VIP_MAX_DAILY_SPEND
    return {
        "vip_count": len(get_vip_watchlist()),
        "micro_invest_per_signal": VIP_MICRO_INVEST_AMOUNT,
        "max_daily_spend": VIP_MAX_DAILY_SPEND,
        "watchlist": get_vip_watchlist(),
    }


@router.get("/crypto-allocation")
def crypto_allocation(
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Current crypto exposure vs the 15% hard wall."""
    from app.services.crypto_engine import get_crypto_allocation_state
    return get_crypto_allocation_state()


@router.post("/crypto-buy")
def crypto_buy(
    symbol: str,
    notional: float = 10.0,
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Place a crypto buy order. Hard wall enforced — rejects if over 15% cap."""
    from app.services.crypto_engine import place_crypto_order
    return place_crypto_order(symbol, "buy", notional)


@router.get("/crypto-feed")
def crypto_feed(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Crypto-only signals from the feed."""
    from sqlmodel import select as sql_select
    signals = session.exec(
        sql_select(CopySignal)
        .where(CopySignal.source == "crypto_coingecko")
        .order_by(CopySignal.created_at.desc())
        .limit(20)
    ).all()
    return {
        "count": len(signals),
        "signals": [
            {"id": s.id, "ticker": s.ticker, "action": s.action,
             "decision": s.decision, "confidence": s.confidence_score,
             "committee": s.committee,  # holds 24h/7d pct change
             "disclosed_at": s.disclosed_at.isoformat() if s.disclosed_at else None}
            for s in signals
        ]
    }
