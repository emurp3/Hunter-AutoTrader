"""
Campaign Agent service — Leon → SAPP handoff protocol.

When Leon creates or auto-generates a product, this service:
  1. Generates a campaign brief (product info + creative direction)
  2. Saves it to CampaignBrief table
  3. Notifies SAPP's Campaign Room via POST to SAPP_BASE_URL/api/campaign-intake

SAPP then picks up the brief, creates campaign content, and marks it as accepted.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlmodel import Session, select

from app.models.campaign_brief import CampaignBrief
from app.models.created_product import CreatedProduct

logger = logging.getLogger(__name__)

SAPP_BASE_URL = os.getenv("SAPP_BASE_URL", "")  # e.g. https://sapp.onrender.com
SAPP_SECRET = os.getenv("SAPP_SECRET", "")       # shared secret for Hunter->SAPP calls


# Platform priority by product type
PLATFORM_PRIORITY = {
    "juneteenth": ["tiktok", "instagram", "youtube", "facebook"],
    "america_250": ["instagram", "facebook", "tiktok", "youtube"],
    "royal_legacy": ["instagram", "tiktok", "youtube"],
    "black_excellence": ["instagram", "tiktok", "facebook"],
    "default": ["instagram", "tiktok", "facebook"],
}

HASHTAGS_BY_THEME = {
    "juneteenth": ["#Juneteenth", "#JuneteenthDay", "#FreedomDay", "#1865", "#BlackFreedom", "#HunterGrowing"],
    "america_250": ["#America250", "#1776", "#July4th", "#BlackLegacy", "#AmericanStory", "#HunterGrowing"],
    "royal_legacy": ["#RoyalLegacy", "#BlackExcellence", "#BlackKings", "#HunterLeon", "#HunterGrowing"],
    "black_excellence": ["#BlackExcellence", "#BlackPride", "#Melanin", "#BlackCulture", "#HunterGrowing"],
    "melanin_magic": ["#MelaninMagic", "#BlackBeauty", "#Melanin", "#BlackGirl", "#HunterGrowing"],
    "default": ["#HunterGrowing", "#HunterLeon", "#StyleAndLegacy", "#WearYourStory"],
}


def _detect_theme(product_name: str, design_variant: Optional[str]) -> str:
    text = ((product_name or "") + " " + (design_variant or "")).lower()
    if "juneteenth" in text: return "juneteenth"
    if "america" in text or "250" in text or "independence" in text: return "america_250"
    if "royal legacy" in text or "royal_legacy" in text: return "royal_legacy"
    if "black excellence" in text or "black_excellence" in text: return "black_excellence"
    if "melanin" in text: return "melanin_magic"
    return "default"


def generate_campaign_brief(
    session: Session,
    product: CreatedProduct,
    urgency_note: Optional[str] = None,
) -> CampaignBrief:
    """Generate and save a campaign brief for a product. Auto-notifies SAPP."""
    theme = _detect_theme(product.name, product.design_variant)
    platforms = PLATFORM_PRIORITY.get(theme, PLATFORM_PRIORITY["default"])
    hashtags = HASHTAGS_BY_THEME.get(theme, HASHTAGS_BY_THEME["default"])

    # Load product pack if available
    pack = {}
    if product.product_pack:
        try:
            pack = json.loads(product.product_pack)
        except Exception:
            pass

    # Build creative direction
    video_concept = (
        f"Short-form video (15-30s) showcasing the {product.name.replace(' (MARQUEE)', '')}. "
        f"Open with a person putting on the shirt — confident, intentional. "
        f"Cut to the design details in slow motion. "
        f"Close with the tagline: {pack.get('sales_copy', 'Hunter Growing.')} "
        f"Upbeat music. Text overlay: product name + price ({product.price}). "
        f"End card: Etsy link + Hunter Growing logo."
    )

    social_caption = (
        f"{pack.get('sales_copy', product.name)}

"
        f"{pack.get('description', '')[:120]}

"
        f"Available now on Etsy — link in bio.

"
        + " ".join(hashtags[:6])
    )

    clean_name = product.name.replace(' (MARQUEE)', '')
    brief = CampaignBrief(
        product_id=product.id,
        product_name=clean_name,
        status="pending",
        campaign_title=f"{clean_name} — Launch Campaign",
        product_description=pack.get("description", "") or clean_name,
        target_audience=_audience_for_theme(theme),
        key_message=pack.get("sales_copy") or clean_name,
        urgency_note=urgency_note,
        platforms=json.dumps(platforms),
        hashtags=json.dumps(hashtags),
        video_concept=video_concept,
        social_caption=social_caption,
        image_prompt=pack.get("image_prompt"),
        price=product.price,
        platform=product.platform,
        product_url=product.url,
    )
    session.add(brief)
    session.commit()
    session.refresh(brief)
    logger.info("Leon: Campaign brief %d generated for '%s'", brief.id, clean_name)

    # Notify SAPP
    _notify_sapp(brief)
    return brief


def _audience_for_theme(theme: str) -> str:
    audiences = {
        "juneteenth": "Black Americans, heritage buyers, Juneteenth celebrants, gift buyers",
        "america_250": "Patriotic buyers, Black Americans, July 4th shoppers, veterans, gift buyers",
        "royal_legacy": "Black men 25-50, premium fashion buyers, Father's Day gift shoppers",
        "black_excellence": "Black professionals, empowerment community, family reunion shoppers",
        "melanin_magic": "Black women, melanin pride community, gift buyers",
        "default": "Heritage fashion buyers, Black Americans, premium streetwear audience",
    }
    return audiences.get(theme, audiences["default"])


def _notify_sapp(brief: CampaignBrief) -> None:
    """POST the campaign brief to SAPP's Campaign Room intake endpoint."""
    if not SAPP_BASE_URL:
        logger.info("Leon: SAPP_BASE_URL not set, skipping SAPP notification for brief %d", brief.id)
        return
    try:
        payload = {
            "source": "hunter_leon",
            "brief_id": brief.id,
            "product_name": brief.product_name,
            "campaign_title": brief.campaign_title,
            "product_description": brief.product_description,
            "target_audience": brief.target_audience,
            "key_message": brief.key_message,
            "urgency_note": brief.urgency_note,
            "platforms": json.loads(brief.platforms or "[]"),
            "hashtags": json.loads(brief.hashtags or "[]"),
            "video_concept": brief.video_concept,
            "social_caption": brief.social_caption,
            "image_prompt": brief.image_prompt,
            "price": brief.price,
            "product_url": brief.product_url,
            "secret": SAPP_SECRET,
        }
        resp = httpx.post(
            f"{SAPP_BASE_URL}/api/campaign-intake",
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info("Leon: SAPP Campaign Room notified for brief %d", brief.id)
        else:
            logger.warning("Leon: SAPP notification returned %d for brief %d", resp.status_code, brief.id)
    except Exception as exc:
        logger.warning("Leon: SAPP notification failed for brief %d: %s", brief.id, exc)


def get_campaign_briefs(session: Session, status: Optional[str] = None) -> list[CampaignBrief]:
    q = select(CampaignBrief).order_by(CampaignBrief.created_at.desc())
    if status:
        q = q.where(CampaignBrief.status == status)
    return list(session.exec(q).all())


def mark_brief_accepted(session: Session, brief_id: int, sapp_campaign_id: Optional[str] = None) -> Optional[CampaignBrief]:
    brief = session.get(CampaignBrief, brief_id)
    if not brief:
        return None
    brief.status = "accepted"
    brief.sapp_campaign_id = sapp_campaign_id
    brief.updated_at = datetime.now(timezone.utc)
    session.add(brief)
    session.commit()
    session.refresh(brief)
    return brief
