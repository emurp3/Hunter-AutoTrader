"""
Forge router — Opportunity Forge Engine API.
"/forge" endpoints.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select
from app.database.config import engine, get_session
from app.models.forge import ForgeOpportunity, ForgeCampaign
from app.services import forge_engine as svc
from app.auth.jwt import get_current_user
from app.auth.models import UserInDB

router = APIRouter(prefix="/forge", tags=["forge"])


@router.get("/summary")
def forge_summary(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Summary of active Forge opportunities."""
    return svc.get_forge_summary(session)


@router.post("/scan")
def trigger_forge_scan(
    background: BackgroundTasks,
    look_ahead_days: int = Query(default=60, ge=7, le=365),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Scan upcoming calendar/cultural windows and create Forge opportunities."""
    def _run():
        with Session(engine) as s:
            svc.run_forge_scan(s, look_ahead_days=look_ahead_days)
    background.add_task(_run)
    return {"status": "scan_queued", "look_ahead_days": look_ahead_days}


@router.get("/opportunities")
def list_opportunities(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    q = select(ForgeOpportunity).order_by(ForgeOpportunity.trigger_date)
    if status:
        q = q.where(ForgeOpportunity.status == status)
    opps = session.exec(q.limit(limit)).all()
    return {
        "count": len(opps),
        "opportunities": [
            {
                "id": o.id, "title": o.title, "trigger_name": o.trigger_name,
                "trigger_type": o.trigger_type,
                "trigger_date": o.trigger_date.isoformat() if o.trigger_date else None,
                "opportunity_type": o.opportunity_type,
                "status": o.status, "confidence_score": o.confidence_score,
                "effort_level": o.effort_level, "days_to_launch": o.days_to_launch,
                "days_to_cash": o.days_to_cash, "estimated_revenue": o.estimated_revenue,
                "estimated_margin_pct": o.estimated_margin_pct,
                "fulfillment_model": o.fulfillment_model, "vendor_name": o.vendor_name,
                "product_ideas": o.product_ideas[:3],
                "description": o.description, "target_audience": o.target_audience,
                "landing_page_url": o.landing_page_url, "vendor_order_url": o.vendor_order_url,
                "revenue_realized": o.revenue_realized, "orders_count": o.orders_count,
            }
            for o in opps
        ],
    }


@router.post("/{opp_id}/approve")
def approve(
    opp_id: int,
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    opp = svc.approve_opportunity(session, opp_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return {"status": "approved", "id": opp.id, "title": opp.title}


class LaunchUpdate(BaseModel):
    landing_page_url: Optional[str] = None
    vendor_order_url: Optional[str] = None


@router.post("/{opp_id}/launch")
def launch(
    opp_id: int,
    body: LaunchUpdate = LaunchUpdate(),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    opp = svc.update_launch(session, opp_id, body.landing_page_url, body.vendor_order_url)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return {"status": "live", "id": opp.id, "title": opp.title, "landing_page_url": opp.landing_page_url}


class SalesUpdate(BaseModel):
    orders_count: Optional[int] = None
    revenue_realized: Optional[float] = None


@router.patch("/{opp_id}/sales")
def update_sales(
    opp_id: int,
    body: SalesUpdate,
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    opp = session.get(ForgeOpportunity, opp_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if body.orders_count is not None:
        opp.orders_count = body.orders_count
    if body.revenue_realized is not None:
        opp.revenue_realized = body.revenue_realized
    session.add(opp)
    session.commit()
    return {"status": "updated", "orders_count": opp.orders_count, "revenue_realized": opp.revenue_realized}
