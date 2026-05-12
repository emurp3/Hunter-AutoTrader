"""
Forge Engine — Opportunity Forge Engine core service.

Detects calendar/cultural/trend windows, generates scored opportunities,
supports order-first outsourced fulfillment model.
"""
from __future__ import annotations
import logging
from datetime import datetime
from sqlmodel import Session, select
from app.models.forge import ForgeOpportunity
from app.services.sources.trend_calendar import TrendCalendarAdapter

logger = logging.getLogger(__name__)

FULFILLMENT_VENDORS = {
    "printful":  {"url": "https://www.printful.com",  "model": "print_on_demand", "avg_margin": 0.55},
    "printify":  {"url": "https://printify.com",      "model": "print_on_demand", "avg_margin": 0.60},
    "gelato":    {"url": "https://gelato.com",         "model": "print_on_demand", "avg_margin": 0.58},
    "gumroad":   {"url": "https://gumroad.com",        "model": "digital",         "avg_margin": 0.93},
    "autods":    {"url": "https://autods.com",          "model": "dropship",        "avg_margin": 0.25},
}


def run_forge_scan(session: Session, look_ahead_days: int = 60) -> dict:
    adapter = TrendCalendarAdapter()
    windows = adapter.get_upcoming(look_ahead_days=look_ahead_days)
    created = 0
    skipped = 0

    for w in windows:
        trigger_name = w["trigger_name"]
        trigger_date = w.get("trigger_date")

        existing = session.exec(
            select(ForgeOpportunity)
            .where(ForgeOpportunity.trigger_name == trigger_name)
            .where(ForgeOpportunity.trigger_date == trigger_date)
        ).first()
        if existing:
            skipped += 1
            continue

        opp = ForgeOpportunity(
            trigger_type=w["trigger_type"],
            trigger_name=trigger_name,
            trigger_date=trigger_date,
            window_open=w.get("window_open"),
            window_close=w.get("window_close"),
            opportunity_type=w.get("opportunity_type", "merchandise"),
            title=w["title"],
            description=w.get("description"),
            target_audience=w.get("target_audience"),
            product_ideas_json=w.get("product_ideas_json"),
            fulfillment_model=w.get("fulfillment_model", "print_on_demand"),
            vendor_name=w.get("vendor_name"),
            price_point=w.get("price_point"),
            cogs_estimate=w.get("cogs_estimate"),
            estimated_margin_pct=w.get("estimated_margin_pct"),
            estimated_units=w.get("estimated_units"),
            estimated_revenue=w.get("estimated_revenue"),
            confidence_score=w.get("confidence_score", 0.5),
            effort_level=w.get("effort_level", "medium"),
            days_to_launch=w.get("days_to_launch"),
            days_to_cash=w.get("days_to_cash"),
            status="detected",
        )
        session.add(opp)
        created += 1

    session.commit()
    return {"created": created, "skipped": skipped, "windows_checked": len(windows)}


def get_forge_summary(session: Session) -> dict:
    all_opps = session.exec(
        select(ForgeOpportunity)
        .where(ForgeOpportunity.status.in_(["detected", "scored", "approved", "launching", "live"]))
        .order_by(ForgeOpportunity.trigger_date)
    ).all()

    by_status: dict = {}
    for o in all_opps:
        by_status[o.status] = by_status.get(o.status, 0) + 1

    return {
        "active_count": len(all_opps),
        "by_status": by_status,
        "total_estimated_revenue": round(sum(o.estimated_revenue or 0 for o in all_opps), 2),
        "opportunities": [
            {
                "id": o.id,
                "title": o.title,
                "trigger_name": o.trigger_name,
                "trigger_type": o.trigger_type,
                "trigger_date": o.trigger_date.isoformat() if o.trigger_date else None,
                "days_to_launch": o.days_to_launch,
                "days_to_cash": o.days_to_cash,
                "confidence_score": o.confidence_score,
                "effort_level": o.effort_level,
                "estimated_revenue": o.estimated_revenue,
                "estimated_margin_pct": o.estimated_margin_pct,
                "fulfillment_model": o.fulfillment_model,
                "vendor_name": o.vendor_name,
                "status": o.status,
                "product_ideas": o.product_ideas[:3],
            }
            for o in all_opps
        ],
        "vendors": FULFILLMENT_VENDORS,
    }


def approve_opportunity(session: Session, opp_id: int, approved_by: str = "commander"):
    opp = session.get(ForgeOpportunity, opp_id)
    if not opp:
        return None
    opp.status = "approved"
    opp.approved_by = approved_by
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def update_launch(session: Session, opp_id: int, landing_page_url=None, vendor_order_url=None):
    opp = session.get(ForgeOpportunity, opp_id)
    if not opp:
        return None
    opp.status = "live"
    opp.launched_at = datetime.utcnow()
    if landing_page_url:
        opp.landing_page_url = landing_page_url
    if vendor_order_url:
        opp.vendor_order_url = vendor_order_url
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp
