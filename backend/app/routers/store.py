"""
Store router — Commerce Division API.
"/store" endpoints.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import Session
from app.database.config import get_session
from app.services import store_agent as svc
from app.services import product_creation as prod_svc
from app.models.created_product import CreatedProduct
from app.auth.jwt import get_current_user
from app.auth.models import UserInDB

router = APIRouter(prefix="/store", tags=["store"])


@router.get("/dashboard")
def store_dashboard(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Commerce Division full dashboard — agent status, products, deadlines, urgency."""
    return svc.get_store_dashboard(session)


@router.get("/products")
def list_products(
    status: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """All store products with optional filters."""
    from sqlmodel import select
    q = select(CreatedProduct).order_by(CreatedProduct.created_at.desc())
    if status:
        q = q.where(CreatedProduct.status == status)
    if platform:
        q = q.where(CreatedProduct.platform == platform)
    products = session.exec(q).all()
    return {"count": len(products), "products": [
        {"id": p.id, "name": p.name, "platform": p.platform, "status": p.status,
         "url": p.url, "price": p.price, "margin": p.estimated_margin,
         "manufacturer": p.manufacturer, "design_variant": p.design_variant,
         "next_action": p.next_action, "notes": p.notes,
         "is_marquee": "MARQUEE" in (p.name or "").upper(),
         "created_at": p.created_at.isoformat() if p.created_at else None}
        for p in products
    ]}


@router.post("/products/{product_id}/launch")
def launch_product(
    product_id: int,
    url: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Mark a product as launched with its live store URL."""
    product = prod_svc.mark_launched(session, product_id, url)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"status": "launched", "id": product.id, "name": product.name, "url": product.url}


@router.patch("/products/{product_id}")
def update_product(
    product_id: int,
    data: dict = Body(...),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Update any field on a product record."""
    from datetime import datetime, timezone
    product = session.get(CreatedProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    for field in ("name", "status", "url", "next_action", "price", "estimated_margin", "notes", "platform"):
        if field in data:
            setattr(product, field, data[field])
    product.updated_at = datetime.now(timezone.utc)
    session.add(product)
    session.commit()
    session.refresh(product)
    return {"status": "updated", "id": product.id}


@router.post("/seed")
def seed_all(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Seed all product collections into the Commerce Division."""
    shoes = prod_svc.seed_hunter_leon_products(session)
    shirts = prod_svc.seed_heritage_shirts(session)
    america = prod_svc.seed_america_250(session)
    royal = prod_svc.seed_royal_legacy_250(session)
    colorways = prod_svc.seed_royal_legacy_colorways(session) if hasattr(prod_svc, 'seed_royal_legacy_colorways') else 0
    marquee = prod_svc.seed_marquee_products(session)
    total = shoes + shirts + america + royal + colorways + marquee
    return {"seeded": total, "breakdown": {"shoes": shoes, "heritage_shirts": shirts,
            "america_250": america, "royal_legacy": royal + colorways, "marquee": marquee}}


@router.get("/deadlines")
def get_deadlines(
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Upcoming commerce deadlines and their urgency status."""
    from app.services.store_agent import DEADLINES, _days_until
    return {"deadlines": [
        {**dl, "days_to_event": _days_until(dl["date"]),
         "days_to_list_by": _days_until(dl["list_by"]),
         "event_date": dl["date"].isoformat(),
         "list_by": dl["list_by"].isoformat()}
        for dl in DEADLINES
    ]}


@router.post("/auto-generate")
def auto_generate_product(
    theme: Optional[str] = Query(default=None, description="Theme hint for the product"),
    branded: bool = Query(default=False, description="Include Hunter Leon / Royal Legacy branding"),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Leon autonomously generates a new product using AI. Saves to DB and returns full pack."""
    return svc.auto_generate_product(session, theme=theme, branded=branded)


@router.get("/agent")
def get_agent_identity(
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Leon's agent identity card."""
    return {
        "name": "LEON",
        "full_title": "Leon — Commerce Division Commander",
        "role": "Commerce Division Commander",
        "department": "Commerce Division",
        "status": "OPERATIONAL",
        "focus": "Heritage & America 250 Collection",
        "clearance": "STORE OPS",
        "specialties": ["AOP Polo Collection", "Heritage Brand Strategy", "POD Pipeline Management"],
        "signature": "Leon. Est. Always.",
        "current_mission": "Launch 14 products before July 4, 2026. Juneteenth in 37 days.",
    }


# ── Campaign Briefs (Leon → SAPP handoff) ─────────────────────────────────────────────
from app.services import campaign_agent as cam_svc
from app.models.campaign_brief import CampaignBrief


@router.get("/campaign-briefs")
def get_campaign_briefs(
    status: Optional[str] = Query(default=None, description="pending | accepted | in_progress | launched"),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """All campaign briefs Leon has generated for SAPP."""
    briefs = cam_svc.get_campaign_briefs(session, status=status)
    return {
        "count": len(briefs),
        "briefs": [
            {
                "id": b.id,
                "product_name": b.product_name,
                "campaign_title": b.campaign_title,
                "status": b.status,
                "target_audience": b.target_audience,
                "key_message": b.key_message,
                "urgency_note": b.urgency_note,
                "platforms": b.platforms,
                "hashtags": b.hashtags,
                "video_concept": b.video_concept,
                "social_caption": b.social_caption,
                "price": b.price,
                "product_url": b.product_url,
                "sapp_campaign_id": b.sapp_campaign_id,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in briefs
        ],
    }


@router.post("/campaign-briefs/{brief_id}/accept")
def accept_brief(
    brief_id: int,
    sapp_campaign_id: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """SAPP calls this when it accepts and starts working on a campaign brief."""
    brief = cam_svc.mark_brief_accepted(session, brief_id, sapp_campaign_id)
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")
    return {"status": "accepted", "brief_id": brief.id, "campaign_title": brief.campaign_title}


@router.post("/campaign-briefs/generate")
def generate_brief_for_product(
    product_id: int = Query(...),
    urgency_note: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Leon manually generates a campaign brief for an existing product."""
    product = session.get(prod_svc.CreatedProduct.__class__, product_id)
    from sqlmodel import select as sql_select
    from app.models.created_product import CreatedProduct as CP
    product = session.exec(sql_select(CP).where(CP.id == product_id)).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    brief = cam_svc.generate_campaign_brief(session, product, urgency_note=urgency_note)
    return {"status": "generated", "brief_id": brief.id, "campaign_title": brief.campaign_title, "notified_sapp": bool(brief.id)}


@router.get("/trends")
def get_trend_signals(
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Leon's Google Trends signal feed — what's rising before it peaks."""
    from app.services.leon_trends import get_trend_report
    return get_trend_report()
