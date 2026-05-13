"""
Quick-Cash Board router.
"/quickcash" endpoints — opportunity ranking + created product/store tracking.
"""
from __future__ import annotations
import json
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlmodel import Session
from app.database.config import get_session
from app.services.quickcash import get_quick_cash_board
from app.services import product_creation as prod_svc
from app.models.created_product import CreatedProduct
from app.auth.jwt import get_current_user
from app.auth.models import UserInDB

router = APIRouter(prefix="/quickcash", tags=["quickcash"])


@router.get("/board")
def quick_cash_board(
    limit: int = Query(default=50, ge=1, le=100),
    lane: str | None = Query(default=None, description="Filter by lane: trading, signal_copy, forge"),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """
    Ranked cross-lane opportunity board.
    Sorted by: (1/days_to_cash) * expected_revenue * confidence / effort.
    """
    board = get_quick_cash_board(session, limit=limit * 3)
    if lane:
        board["board"] = [x for x in board["board"] if x["lane"] == lane]
    board["board"] = board["board"][:limit]
    board["total"] = len(board["board"])
    return board


# ── Created Products / Stores ──────────────────────────────────────────────────────

@router.get("/created-products")
def list_created_products(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """All created products/stores/listings."""
    products = prod_svc.get_created_products(session)
    return {
        "count": len(products),
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "source_opportunity": p.source_opportunity,
                "platform": p.platform,
                "status": p.status,
                "url": p.url,
                "next_action": p.next_action,
                "price": p.price,
                "estimated_margin": p.estimated_margin,
                "design_variant": p.design_variant,
                "manufacturer": p.manufacturer,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "launched_at": p.launched_at.isoformat() if p.launched_at else None,
            }
            for p in products
        ],
    }


@router.post("/created-products")
def create_product(
    data: dict = Body(...),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Create a new product/store record."""
    product = prod_svc.create_product(session, data)
    return {"status": "created", "id": product.id, "name": product.name}


@router.post("/created-products/{product_id}/mark-launched")
def mark_product_launched(
    product_id: int,
    url: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Mark a product as launched and optionally set its live URL."""
    product = prod_svc.mark_launched(session, product_id, url)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"status": "launched", "id": product.id, "url": product.url}


@router.post("/seed-hunter-leon")
def seed_hunter_leon(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Seed Hunter Leon shoe product packs into the created-products board."""
    seeded = prod_svc.seed_hunter_leon_products(session)
    return {"seeded": seeded, "message": f"Seeded {seeded} Hunter Leon product(s)"}

@router.post("/seed-heritage-shirts")
def seed_heritage_shirts(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Seed Heritage Polo shirt products into the created-products board."""
    seeded = prod_svc.seed_heritage_shirts(session)
    return {"seeded": seeded, "message": f"Seeded {seeded} heritage shirt product(s)"}


@router.post("/seed-america-250")
def seed_america_250(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Seed America 250th anniversary polo products."""
    seeded = prod_svc.seed_america_250(session)
    return {"seeded": seeded, "message": f"Seeded {seeded} America 250 product(s)"}


@router.post("/seed-all-products")
def seed_all_products(
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Seed all Hunter Leon shoes + Heritage shirts in one call."""
    shoes = prod_svc.seed_hunter_leon_products(session)
    shirts = prod_svc.seed_heritage_shirts(session)
    return {"seeded_shoes": shoes, "seeded_shirts": shirts, "total": shoes + shirts}



@router.post("/generate-product-pack")
def generate_product_pack(
    opportunity_title: str = Query(...),
    opportunity_type: str = Query(default="digital"),
    save: bool = Query(default=False),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """Generate a complete product pack from a Quick-Cash/Forge opportunity."""
    pack = prod_svc.generate_product_pack(opportunity_title, opportunity_type)
    result = {"status": "generated", "pack": pack}
    if save:
        p = prod_svc.create_product(session, {
            "name": opportunity_title,
            "platform": pack["platform"],
            "status": "draft",
            "product_pack": pack,
        })
        result["product_id"] = p.id
    return result
