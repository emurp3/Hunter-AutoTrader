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


# ── Autonomous Product Generation ────────────────────────────────────────────────────

BRANDED_THEMES = [
    "Hunter Leon luxury streetwear",
    "Royal Legacy heritage polo",
    "HL Monogram premium collection",
    "Lion Insignia signature series",
]

UNBRANDED_THEMES = [
    "America 250th anniversary 1776-2026",
    "Juneteenth Black heritage celebration",
    "Father's Day premium polo gift",
    "Black excellence empowerment",
    "Pan-African pride collection",
    "Fourth of July patriotic",
    "Melanin culture and pride",
    "Black American history tribute",
]


def auto_generate_product(session, theme: str | None = None, branded: bool = False) -> dict:
    """
    Leon autonomously generates a new product pack using AI.
    Saves to CreatedProduct and returns the full pack.
    """
    import os, json, httpx
    from app.models.created_product import CreatedProduct
    from sqlmodel import select

    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return {"error": "No AI API key configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."}

    # Pick theme
    import random
    if not theme:
        pool = BRANDED_THEMES if branded else UNBRANDED_THEMES
        theme = random.choice(pool)

    brand_instruction = (
        "Include the 'Hunter Leon' or 'Royal Legacy' brand name and lion crest logo in the design."
        if branded else
        "This is a standalone design — no external brand name required."
    )

    prompt = f"""You are Leon, Commerce Division Commander for Hunter AI. Generate a complete AOP polo shirt product pack.

Theme: {theme}
Branding: {brand_instruction}

Return ONLY valid JSON with these exact fields:
{{
  "name": "Product name (max 60 chars)",
  "title": "Etsy listing title (max 140 chars, SEO optimized)",
  "description": "Product description (2-3 sentences, compelling)",
  "price": 72.00,
  "estimated_margin": 0.55,
  "platform": "etsy",
  "manufacturer": "Printful",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "sales_copy": "One punchy sentence",
  "image_prompt": "Detailed prompt to generate the flat print design in ChatGPT/Midjourney",
  "next_action": "First action to take",
  "notes": "Why this product will sell",
  "design_variant": "snake_case_id",
  "is_branded": {str(branded).lower()}
}}"""

    try:
        # Try Anthropic first
        if os.getenv("ANTHROPIC_API_KEY"):
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"},
                json={"model": "claude-3-haiku-20240307", "max_tokens": 1024,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=20,
            )
            content = resp.json()["content"][0]["text"]
        else:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
                timeout=20,
            )
            content = resp.json()["choices"][0]["message"]["content"]

        # Parse JSON from response
        import re
        json_match = re.search(r'\{[\s\S]+\}', content)
        pack = json.loads(json_match.group(0) if json_match else content)

    except Exception as exc:
        logger.error("Leon auto-generate failed: %s", exc)
        return {"error": str(exc)}

    # Save to DB
    try:
        from app.services.product_creation import create_product
        product = create_product(session, {
            "name": pack.get("name", theme),
            "platform": pack.get("platform", "etsy"),
            "manufacturer": pack.get("manufacturer", "Printful"),
            "status": "draft",
            "price": pack.get("price"),
            "estimated_margin": pack.get("estimated_margin"),
            "design_variant": pack.get("design_variant"),
            "next_action": pack.get("next_action"),
            "notes": pack.get("notes"),
            "product_pack": json.dumps(pack),
        })
        pack["product_id"] = product.id
        pack["saved"] = True
    except Exception as exc:
        logger.error("Leon: failed to save product: %s", exc)
        pack["saved"] = False

    pack["theme"] = theme
    pack["branded"] = branded
    return pack
