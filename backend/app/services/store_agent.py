"""
Store Agent — Commerce Division service.

Leon is the Hunter Commerce Division agent. Responsibilities:
  - Track all created products and their real store status
  - Monitor urgent launch deadlines (Juneteenth, July 4, Father's Day)
  - Surface pending actions per product
  - Aggregate revenue + order data from connected platforms
  - Flag listings that are overdue or at risk
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, date
from typing import Optional
from sqlmodel import Session, select
from app.models.created_product import CreatedProduct

logger = logging.getLogger(__name__)

# ── Deadline calendar ─────────────────────────────────────────────────────────
DEADLINES = [
    {"name": "Juneteenth",         "date": date(2026, 6, 19), "list_by": date(2026, 5, 20), "tags": ["juneteenth"]},
    {"name": "Father's Day",       "date": date(2026, 6, 15), "list_by": date(2026, 5, 22), "tags": ["fathers day", "royal roots", "royal legacy"]},
    {"name": "July 4 — America 250","date": date(2026, 7,  4), "list_by": date(2026, 5, 21), "tags": ["250", "america", "independence", "1776"]},
    {"name": "Labor Day",          "date": date(2026, 9,  7), "list_by": date(2026, 8, 10), "tags": []},
]


def _days_until(d: date) -> int:
    return (d - datetime.now(timezone.utc).date()).days


def get_store_dashboard(session: Session) -> dict:
    """Full Commerce Division dashboard payload."""
    products = list(session.exec(
        select(CreatedProduct).order_by(CreatedProduct.created_at.desc())
    ).all())

    # Status breakdown
    by_status: dict[str, int] = {}
    for p in products:
        by_status[p.status] = by_status.get(p.status, 0) + 1

    # Revenue estimate (launched products with price)
    launched = [p for p in products if p.status == "launched"]
    total_price_potential = sum(p.price or 0 for p in products if p.status in ("draft", "created"))
    live_revenue_potential = sum(p.price or 0 for p in launched)

    # Deadline urgency
    today = datetime.now(timezone.utc).date()
    deadlines_status = []
    for dl in DEADLINES:
        days_to_event = _days_until(dl["date"])
        days_to_list = _days_until(dl["list_by"])
        relevant = [p for p in products if any(
            tag.lower() in (p.name or "").lower() or tag.lower() in (p.notes or "").lower()
            for tag in dl["tags"]
        )]
        launched_relevant = [p for p in relevant if p.status == "launched"]
        deadlines_status.append({
            "name": dl["name"],
            "event_date": dl["date"].isoformat(),
            "list_by": dl["list_by"].isoformat(),
            "days_to_event": days_to_event,
            "days_to_list_by": days_to_list,
            "overdue": days_to_list < 0,
            "urgent": 0 <= days_to_list <= 7,
            "relevant_products": len(relevant),
            "launched_products": len(launched_relevant),
            "ready": len(launched_relevant) == len(relevant) and len(relevant) > 0,
        })

    # Urgent actions
    urgent_actions = []
    for p in products:
        if p.status == "draft" and p.next_action:
            urgent_actions.append({
                "product_id": p.id,
                "product_name": p.name,
                "action": p.next_action,
                "platform": p.platform,
                "price": p.price,
                "is_marquee": "MARQUEE" in (p.name or "").upper(),
            })

    # Platform breakdown
    by_platform: dict[str, int] = {}
    for p in products:
        by_platform[p.platform] = by_platform.get(p.platform, 0) + 1

    return {
        "agent": {
            "name": "Leon",
            "role": "Commerce Division Commander",
            "status": "OPERATIONAL",
            "focus": "Heritage + America 250 Polo Collection",
            "clearance": "STORE OPS",
        },
        "summary": {
            "total_products": len(products),
            "by_status": by_status,
            "by_platform": by_platform,
            "launched_count": len(launched),
            "draft_count": by_status.get("draft", 0),
            "live_revenue_potential": round(live_revenue_potential, 2),
            "pipeline_value": round(total_price_potential, 2),
            "urgent_action_count": len(urgent_actions),
        },
        "deadlines": deadlines_status,
        "urgent_actions": urgent_actions[:10],
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "platform": p.platform,
                "manufacturer": p.manufacturer,
                "status": p.status,
                "url": p.url,
                "price": p.price,
                "margin": p.estimated_margin,
                "design_variant": p.design_variant,
                "next_action": p.next_action,
                "is_marquee": "MARQUEE" in (p.name or "").upper(),
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "launched_at": p.launched_at.isoformat() if p.launched_at else None,
            }
            for p in products
        ],
    }
