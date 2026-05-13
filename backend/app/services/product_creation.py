"""Product creation service — generates real product packs from Quick-Cash / Forge opportunities."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any
from sqlmodel import Session, select
from app.models.created_product import CreatedProduct

logger = logging.getLogger(__name__)


# —— Product pack templates ——————————————————————————————————————————————

HUNTER_LEON_PRODUCTS = [
    {
        "name": "Hunter Leon Oxford Brogue — Lion Insignia",
        "design_variant": "lion_insignia",
        "platform": "etsy",
        "manufacturer": "JetPrint",
        "price": 219.00,
        "estimated_margin": 0.52,
        "title": "Hunter Leon Oxford Brogue | Lion Insignia | Luxury Dress Shoe",
        "description": (
            "Handcrafted leather Oxford brogue featuring the iconic Hunter Leon Lion Insignia — "
            "a gold-embossed crowned lion emblem on premium black-to-burgundy gradient leather. "
            "Air-cushioned sole for all-day comfort without sacrificing formality. "
            "Available in US sizes 7–13. Each pair ships in a branded Hunter Leon box. "
            "Perfect for boardrooms, formal occasions, and anyone who refuses to blend in."
        ),
        "price_display": "$219.00",
        "tags": ["oxford shoes", "luxury dress shoes", "mens formal shoes", "brogue shoes",
                 "custom logo shoes", "lion emblem", "hunter leon", "black burgundy shoes",
                 "air sole dress shoes", "premium leather oxford"],
        "image_prompt": (
            "Professional product photography of a black-to-deep-burgundy leather Oxford brogue shoe "
            "on a dark slate surface. Gold embossed lion with crown logo on the vamp. "
            "Air-cushioned transparent sole visible from side angle. Soft rim lighting. "
            "Luxury fashion editorial style."
        ),
        "mockup_prompt": (
            "3D mockup of a leather Oxford brogue dress shoe, black-to-burgundy fade, "
            "with a gold lion crest logo on the side panel. Show both shoes at 3/4 angle "
            "on a dark wood surface with gold accent lighting."
        ),
        "sales_copy": (
            "You already know the difference. Hunter Leon Lion Insignia Oxford — "
            "premium gradient leather, gold lion crest, air sole. Boardroom to black tie. "
            "Only {N} pairs this run."
        ),
        "checkout_recommendation": "Etsy (no monthly fees, built-in audience for luxury goods)",
        "manufacturer_url": "https://www.jetprintapp.com/custom-branding",
        "manufacturer_notes": (
            "JetPrint: upload lion insignia logo as SVG/PNG, select Oxford Leather Dress Shoe SKU, "
            "set placement to vamp panel. Enable branded shoebox (+$1.50). Min order: 1 unit POD."
        ),
        "launch_checklist": [
            "Upload lion_insignia PNG to JetPrint 3D configurator",
            "Generate mockup images (front, side, heel, box)",
            "Create Etsy seller account if not done",
            "List product at $219 with free shipping",
            "Add 8 photos minimum per Etsy SEO requirements",
            "Enable Etsy Ads at $1/day for first 30 days",
        ],
        "source_opportunity": "Hunter Leon Brand — Luxury Footwear",
        "status": "draft",
        "next_action": "Upload logo to JetPrint configurator at jetprintapp.com",
    },
    {
        "name": "Hunter Leon Oxford Brogue — Luxury Monogram",
        "design_variant": "luxury_monogram",
        "platform": "etsy",
        "manufacturer": "JetPrint",
        "price": 199.00,
        "estimated_margin": 0.54,
        "title": "Hunter Leon Oxford Brogue | HL Monogram | Premium Leather Dress Shoe",
        "description": (
            "The signature HL Monogram variant of the Hunter Leon Oxford Brogue. "
            "Deep burgundy suede panel with gold HL architectural monogram. "
            "Black gradient leather body, brogue detailing, air-cushioned sole. "
            "For those who wear their initials where it counts."
        ),
        "price_display": "$199.00",
        "tags": ["monogram shoes", "HL shoe", "luxury oxford", "mens dress shoes",
                 "personalized shoes", "burgundy leather shoes", "hunter leon",
                 "custom brogue", "premium footwear", "air sole oxford"],
        "image_prompt": (
            "Editorial product photo of black Oxford brogue with deep burgundy suede vamp panel. "
            "Gold HL architectural monogram on the panel. Dual-tone black-and-burgundy leather body. "
            "Air sole visible. Moody studio lighting on dark marble surface."
        ),
        "mockup_prompt": (
            "3D mockup pair of Oxford brogues — deep burgundy suede panel on black leather, "
            "gold HL monogram logo, brogue stitching detail. Angled 45-degree product shot, "
            "luxury packaging box open beside them."
        ),
        "sales_copy": (
            "Hunter Leon HL Monogram. The burgundy suede panel. The gold mark. "
            "Everything else is just a shoe."
        ),
        "checkout_recommendation": "Etsy + Shopify (Etsy for discovery, Shopify for brand store)",
        "manufacturer_url": "https://www.jetprintapp.com/custom-branding",
        "manufacturer_notes": (
            "JetPrint: upload HL monogram as SVG, select vamp panel placement on Oxford Leather SKU. "
            "Use heat-transfer vinyl (HTV) method for leather adhesion. Branded box +$1.50."
        ),
        "launch_checklist": [
            "Export HL monogram as clean SVG from design file",
            "Upload to JetPrint and configure vamp placement",
            "Order 1 sample unit ($85-95 cost basis) to verify print quality",
            "Photograph sample for listing images",
            "List on Etsy at $199 with 30-day return policy",
            "Create brand story page on Shopify Hunter Leon store",
        ],
        "source_opportunity": "Hunter Leon Brand — Luxury Footwear",
        "status": "draft",
        "next_action": "Export HL monogram SVG and upload to JetPrint configurator",
    },
]


def get_created_products(session: Session) -> list[CreatedProduct]:
    return list(session.exec(select(CreatedProduct).order_by(CreatedProduct.created_at.desc())).all())


def create_product(session: Session, data: dict) -> CreatedProduct:
    pack = data.get("product_pack")
    product = CreatedProduct(
        name=data["name"],
        source_opportunity=data.get("source_opportunity"),
        platform=data.get("platform", "manual"),
        status=data.get("status", "draft"),
        url=data.get("url"),
        next_action=data.get("next_action"),
        price=data.get("price"),
        estimated_margin=data.get("estimated_margin"),
        product_pack=json.dumps(pack) if isinstance(pack, dict) else pack,
        design_variant=data.get("design_variant"),
        manufacturer=data.get("manufacturer"),
        notes=data.get("notes"),
    )
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def seed_hunter_leon_products(session: Session) -> int:
    """Seed Hunter Leon shoe products if they don't exist yet."""
    seeded = 0
    for p in HUNTER_LEON_PRODUCTS:
        existing = session.exec(
            select(CreatedProduct).where(CreatedProduct.name == p["name"])
        ).first()
        if not existing:
            create_product(session, p)
            seeded += 1
    return seeded


def mark_launched(session: Session, product_id: int, url: str | None = None) -> CreatedProduct | None:
    product = session.get(CreatedProduct, product_id)
    if not product:
        return None
    product.status = "launched"
    product.launched_at = datetime.now(timezone.utc)
    if url:
        product.url = url
    product.updated_at = datetime.now(timezone.utc)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def generate_product_pack(opportunity_title: str, opportunity_type: str = "digital") -> dict:
    """Generate a product pack skeleton from a Quick-Cash/Forge opportunity."""
    return {
        "title": opportunity_title,
        "platform": "gumroad" if opportunity_type == "digital" else "etsy",
        "price": None,
        "description": "",
        "tags": [],
        "image_prompt": f"Professional product image for: {opportunity_title}",
        "sales_copy": "",
        "launch_checklist": [
            "Create product/listing on chosen platform",
            "Add product images",
            "Set price and shipping (if physical)",
            "Publish and share link",
        ],
        "status": "draft",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
