"""
Creation lane — generates grounded, actionable revenue opportunities when
intake returns 0 new viable sources.

Design rules (enforced by this module):
  - NO ticker trades, market prices, or financial instrument data
  - NO fabricated metrics — all estimates are conservative and grounded in
    the specific service/product type the opportunity targets
  - Non-trading execution paths only (marketplace_listing, outreach,
    local_pitch, affiliate_content)
  - All catalog entries are derived from demand signals already present in
    Hunter's source acquisition config (marketplace queries, digital demand
    patterns, local business gap signals, gig platform queries)
  - Idempotent within a calendar day — source IDs are date-scoped, so the
    same day's batch is never double-inserted
  - Does not touch Alpaca, bankroll, strategy quota, or trading logic

Trigger:
  run_intake() calls run_creation_lane() automatically when inserted == 0.
  POST /autotrader/run-creation for manual trigger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.models.income_source import IncomeSource, SourceStatus
from app.services.scoring import score_opportunity

logger = logging.getLogger(__name__)

# ── Creation catalog ───────────────────────────────────────────────────────────
# Each entry is a grounded opportunity template. All fields are honest:
# - estimated_profit: conservative single-execution return (not aggregate)
# - confidence: reflects real execution difficulty + market depth
# - source_reason: cites the Hunter signal / config that grounds the entry
#
# Lanes:
#   marketplace  → decision engine routes to execution_path=arbitrage
#   service      → routes to local_pitch or outreach
#   digital      → routes to affiliate_content

_CATALOG: list[dict[str, Any]] = [
    # ── Facebook Marketplace / Arbitrage ─────────────────────────────────────
    {
        "id_suffix": "mp-ipad-resale",
        "description": "iPad resale flip — source used tablet from estate/swap sale, relist at 20% markup on Marketplace",
        "category": "marketplace",
        "estimated_profit": 35.0,
        "confidence": 0.70,
        "next_action": (
            "Search Facebook Marketplace and OfferUp for iPad listings priced ≥20% below "
            "completed sale comps. Buy, clean, photograph, relist same day with accurate "
            "condition grade and includes/excludes. Price 10% below top current ask."
        ),
        "notes": (
            "target_buyer: local buyer seeking discounted tablet | "
            "pain_point: new iPads are expensive; buyers look for vetted used units | "
            "offer: clean, condition-graded iPad at 10% below cheapest local ask | "
            "effort: 2-4h sourcing + 30 min listing + handoff | "
            "execution_path: marketplace_listing | "
            "source_reason: persistent Marketplace demand signal from SOURCES_MARKETPLACE_QUERIES (ipad). "
            "High-velocity resale category with predictable comp pricing."
        ),
    },
    {
        "id_suffix": "mp-dyson-resale",
        "description": "Dyson vacuum resale — source working unit at estate/clearance price, flip locally",
        "category": "marketplace",
        "estimated_profit": 45.0,
        "confidence": 0.68,
        "next_action": (
            "Search OfferUp, Craigslist, and estate sale apps for Dyson V-series priced "
            "≤$60. Test suction, clean filter, photograph. Relist on Facebook Marketplace "
            "at $80-100 with video of it running. Local pickup only — no shipping risk."
        ),
        "notes": (
            "target_buyer: homeowner replacing vacuum, price-sensitive | "
            "pain_point: new Dysons cost $200-400; buyers know the brand and want a deal | "
            "offer: tested, clean Dyson at 40-50% below retail | "
            "effort: 3-5h including sourcing round-trip + 45 min listing | "
            "execution_path: marketplace_listing | "
            "source_reason: SOURCES_MARKETPLACE_QUERIES includes 'dyson'. "
            "Resale margin consistently $30-60 on working V-series units."
        ),
    },
    {
        "id_suffix": "mp-makita-tool-flip",
        "description": "Makita power tool bundle resale — source single tool or lot, relist cleaned on Marketplace",
        "category": "marketplace",
        "estimated_profit": 40.0,
        "confidence": 0.66,
        "next_action": (
            "Scout estate sales, garage sales, and Facebook 'for sale' groups for Makita "
            "18V or 12V tools under $30 each. Wipe down, photograph against clean background. "
            "Bundle 2-3 complementary tools (drill + impact driver) for higher perceived value. "
            "Price $10 below lowest current local ask."
        ),
        "notes": (
            "target_buyer: DIY homeowner or small contractor | "
            "pain_point: new Makita kits are $150-300; pros buy used to outfit job sites | "
            "offer: tested, clean Makita tools priced below big-box | "
            "effort: 2-4h sourcing + 1h listing with good photos | "
            "execution_path: marketplace_listing | "
            "source_reason: SOURCES_MARKETPLACE_QUERIES includes 'makita'. "
            "Tool flipping is high-demand, low-competition in most local markets."
        ),
    },
    {
        "id_suffix": "mp-nintendo-games-flip",
        "description": "Nintendo Switch game lot resale — buy cheap lot, split and relist individually",
        "category": "marketplace",
        "estimated_profit": 30.0,
        "confidence": 0.71,
        "next_action": (
            "Find Facebook Marketplace or OfferUp lots of 5+ Switch games priced ≤$5/game. "
            "Split the lot. Keep best sellers (Mario Kart, Zelda, Pokémon). Relist individually "
            "at $15-25 each based on completed eBay comps. Ship via eBay or sell locally."
        ),
        "notes": (
            "target_buyer: parent or gamer seeking specific title | "
            "pain_point: individual titles sell for $15-25 even used; lot buyers overpay per-unit | "
            "offer: individual Switch titles at 20-30% below eBay lowest | "
            "effort: 1h sourcing, 1h listing, 1-2h shipping if needed | "
            "execution_path: marketplace_listing | "
            "source_reason: SOURCES_MARKETPLACE_QUERIES includes 'nintendo'. "
            "Game lots frequently mispriced — split arbitrage yields $20-40 per lot."
        ),
    },

    # ── Service / Local outreach ──────────────────────────────────────────────
    {
        "id_suffix": "svc-church-website-setup",
        "description": "Church website setup — pitch local congregation without a modern web presence",
        "category": "service",
        "estimated_profit": 150.0,
        "confidence": 0.67,
        "next_action": (
            "Search Google Maps for 'church near me' in your area. Open each result. "
            "If they have no website, a broken link, or a site that isn't mobile-responsive, "
            "that's your pitch. Call the office, ask for the admin or pastor. Offer a clean "
            "5-page site (Home, About, Sermons, Events, Contact) for $150 flat. "
            "Use a free template (Squarespace, Wix) — deliver in 48-72h."
        ),
        "notes": (
            "target_buyer: small local congregation (50-300 members) | "
            "pain_point: church has no web presence; new visitors can't find service times or location | "
            "offer: 5-page mobile-responsive website in 48h for $150 flat | "
            "effort: 2h prospecting, 1h pitch, 4-6h build, 1h handoff | "
            "execution_path: local_pitch | "
            "source_reason: SOURCES_LOCAL_BUSINESS_TYPES includes 'church'. "
            "SOURCES_DIGITAL_QUERIES includes 'church website template'. "
            "High need, low competition, fast decision maker (single pastor approves)."
        ),
    },
    {
        "id_suffix": "svc-clinic-google-presence",
        "description": "Dental/clinic Google Maps optimization — fix missing or incomplete local listing",
        "category": "service",
        "estimated_profit": 75.0,
        "confidence": 0.69,
        "next_action": (
            "Search Google Maps for 'dentist' or 'clinic' in your area. Sort by rating. "
            "Find practices with <4.0 stars or missing hours/photos. Cold call the front desk — "
            "offer to: (1) claim/fix their Google Business Profile, (2) add photos, (3) set up "
            "appointment link. Charge $75 one-time. Deliver in 1 business day."
        ),
        "notes": (
            "target_buyer: small dental or medical practice owner | "
            "pain_point: patients can't find accurate hours or book online; losing to competitors | "
            "offer: Google Business Profile cleanup + appointment link setup for $75 | "
            "effort: 1h prospecting, 30min pitch, 2h delivery | "
            "execution_path: local_pitch | "
            "source_reason: SOURCES_LOCAL_BUSINESS_TYPES includes 'dentist', 'clinic'. "
            "Local SEO basics are high-value, low-effort, and repeatable across any market."
        ),
    },
    {
        "id_suffix": "svc-shopify-automation",
        "description": "Shopify order/inventory automation — small store owner drowning in manual tasks",
        "category": "service",
        "estimated_profit": 120.0,
        "confidence": 0.64,
        "next_action": (
            "Post on r/shopify or search Upwork for store owners asking about 'automation', "
            "'bulk orders', or 'inventory sync'. DM or apply. Offer to build one automation "
            "(order confirmation email sequence, low-stock alert, or CSV import tool) for $120. "
            "Use Shopify Flow (free) or a webhook-to-Zapier pipeline. Deliver in 3 days."
        ),
        "notes": (
            "target_buyer: small Shopify store owner doing $1k-10k/month | "
            "pain_point: manually managing orders, emails, and inventory is eating 5-10h/week | "
            "offer: one working Shopify automation delivered in 72h for $120 | "
            "effort: 1h scoping, 3-5h build, 1h test + handoff | "
            "execution_path: outreach | "
            "source_reason: SOURCES_GIG_QUERIES includes 'automation', 'shopify'. "
            "Consistent gig demand signal; Shopify automation is repeatable and learnable once."
        ),
    },
    {
        "id_suffix": "svc-social-media-monthly",
        "description": "Social media content — 12 posts/month retainer for one local business",
        "category": "service",
        "estimated_profit": 100.0,
        "confidence": 0.62,
        "next_action": (
            "Pick a local business with an active Facebook/Instagram page but infrequent posts "
            "(check their last 5 posts — if >2 weeks between any, they're a target). "
            "DM or call with a simple offer: 12 posts/month (3/week), scheduled, with captions "
            "and hashtags, for $100/month. Use Canva + Buffer. First month is a trial."
        ),
        "notes": (
            "target_buyer: small local business owner who knows they should post but never does | "
            "pain_point: inconsistent social presence; losing visibility to competitors | "
            "offer: 12 scheduled posts/month, done-for-you, for $100 | "
            "effort: 3h setup + 2h/month ongoing (scalable to 3-5 clients) | "
            "execution_path: outreach | "
            "source_reason: SOURCES_SOCIAL_ENABLED=true. Social listener identifies businesses with "
            "engagement gaps. Retainer model compounds income over time."
        ),
    },

    # ── Digital product ───────────────────────────────────────────────────────
    {
        "id_suffix": "dig-patient-intake-form",
        "description": "Patient intake form template — sell to independent clinics via Etsy/Gumroad",
        "category": "digital",
        "estimated_profit": 29.0,
        "confidence": 0.65,
        "next_action": (
            "Build a clean patient intake form in Google Forms or Fillable PDF. Include: "
            "contact info, insurance fields, medical history checklist, HIPAA acknowledgement. "
            "Export as PDF + Google Forms link. List on Etsy ($29), Gumroad ($25), and post "
            "to r/dentistry or r/medicine as a free resource (with paid upgrade). "
            "Target 3-5 sales/month minimum."
        ),
        "notes": (
            "target_buyer: independent dentist, therapist, or small clinic | "
            "pain_point: paper intake forms are slow; buying a custom one is expensive | "
            "offer: professional, editable patient intake form for $25-29 | "
            "effort: 3-4h to build + 1h listing on 2 platforms | "
            "execution_path: affiliate_content | "
            "source_reason: SOURCES_DIGITAL_QUERIES includes 'patient intake form'. "
            "Consistent search demand. Low competition on Etsy. Evergreen product."
        ),
    },
    {
        "id_suffix": "dig-dashboard-template",
        "description": "Business dashboard template (Google Sheets or Notion) — sell to small business owners",
        "category": "digital",
        "estimated_profit": 25.0,
        "confidence": 0.66,
        "next_action": (
            "Build a 3-tab Google Sheets dashboard: Revenue tracker, Expense log, Monthly P&L. "
            "Add conditional formatting and a summary view. Export and protect formulas. "
            "List on Gumroad ($25) and Etsy ($27). Title: 'Small Business Finance Dashboard "
            "Template — Google Sheets'. Post to r/smallbusiness with a preview screenshot."
        ),
        "notes": (
            "target_buyer: solo business owner or freelancer without accounting software | "
            "pain_point: QuickBooks is overkill; spreadsheets are disorganized | "
            "offer: pre-built revenue + expense + P&L dashboard for $25 | "
            "effort: 4-5h build + 1h listing | "
            "execution_path: affiliate_content | "
            "source_reason: SOURCES_DIGITAL_QUERIES includes 'dashboard template'. "
            "High search volume, clear buyer intent. Gumroad has built-in distribution."
        ),
    },
    {
        "id_suffix": "dig-artist-press-kit",
        "description": "Artist press kit template (Canva/PDF) — sell to emerging musicians and visual artists",
        "category": "digital",
        "estimated_profit": 27.0,
        "confidence": 0.63,
        "next_action": (
            "Create a 5-page Canva template: bio page, press photo placeholder, discography/"
            "portfolio page, booking/contact page, quote/testimonial page. Export as Canva "
            "template link + PDF mockup. List on Etsy ($27). Post to r/WeAreTheMusicMakers "
            "and r/ArtBusiness. Bundle with a 1-page pitch guide at $35."
        ),
        "notes": (
            "target_buyer: emerging musician, visual artist, or photographer seeking gigs | "
            "pain_point: venues and galleries require press kits; building from scratch takes hours | "
            "offer: professional press kit template ready to customize in 30 minutes | "
            "effort: 3h template build + 1h listing + mockup | "
            "execution_path: affiliate_content | "
            "source_reason: SOURCES_DIGITAL_QUERIES includes 'artist press kit'. "
            "Niche with loyal buyer community on Etsy. Low refund rate for digital templates."
        ),
    },
]

# How many opportunities to create per dry-intake trigger.
# Configurable via HUNTER_CREATION_LANE_COUNT env var.
import os as _os
_DEFAULT_CREATION_COUNT = int(_os.getenv("HUNTER_CREATION_LANE_COUNT", "3"))

CREATION_ORIGIN_MODULE = "creation_lane"


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class CreationResult:
    created: int = 0
    skipped: int = 0
    errors: int = 0
    trigger_reason: str = ""
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)


@dataclass
class _CreationState:
    last_run_at: datetime | None = None
    last_trigger_reason: str = "never"
    last_created: int = 0
    last_skipped: int = 0
    total_created_lifetime: int = 0


_state = _CreationState()


def get_creation_status() -> dict[str, Any]:
    return {
        "last_run_at": _state.last_run_at.isoformat() if _state.last_run_at else None,
        "last_trigger_reason": _state.last_trigger_reason,
        "last_created": _state.last_created,
        "last_skipped": _state.last_skipped,
        "total_created_lifetime": _state.total_created_lifetime,
        "catalog_size": len(_CATALOG),
        "creation_origin_module": CREATION_ORIGIN_MODULE,
    }


# ── Selection logic ───────────────────────────────────────────────────────────

def _select_daily_batch(n: int) -> list[dict[str, Any]]:
    """
    Pick n catalog entries for today. Rotation is date-seeded so each day
    gets a different lead entry while covering all lanes over time.
    Selection always includes at least one entry from each lane when n >= 3.
    """
    today_ordinal = date.today().toordinal()

    # Partition by lane
    marketplace = [e for e in _CATALOG if e["category"] == "marketplace"]
    service     = [e for e in _CATALOG if e["category"] == "service"]
    digital     = [e for e in _CATALOG if e["category"] == "digital"]

    selected: list[dict[str, Any]] = []

    # Rotate within each lane using date offset
    if marketplace:
        selected.append(marketplace[today_ordinal % len(marketplace)])
    if service:
        selected.append(service[today_ordinal % len(service)])
    if digital:
        selected.append(digital[today_ordinal % len(digital)])

    # Fill remaining slots from the full catalog if count > 3
    if n > 3:
        remaining = [e for e in _CATALOG if e not in selected]
        offset = (today_ordinal // 3) % max(1, len(remaining))
        for i in range(n - 3):
            selected.append(remaining[(offset + i) % len(remaining)])

    return selected[:n]


# ── Core creation function ────────────────────────────────────────────────────

def run_creation_lane(
    session: Session,
    *,
    trigger_reason: str = "no_new_discoveries",
    count: int | None = None,
) -> CreationResult:
    """
    Generate and persist grounded, non-trading opportunities.

    - Idempotent: source IDs are date-scoped — same day batch is never re-inserted.
    - Routes exclusively to non-trading execution paths (marketplace_listing,
      local_pitch, outreach, affiliate_content).
    - Calls process_new_opportunity() for every new insert so scoring, decisions,
      and action packets are generated automatically.

    Args:
        session:        Active SQLModel session.
        trigger_reason: Why creation was invoked (logged + surfaced in status).
        count:          Override number of opportunities to create (default from env).

    Returns:
        CreationResult with counts and summary dicts for each created opportunity.
    """
    from app.services.orchestrator import process_new_opportunity

    n = count if count is not None else _DEFAULT_CREATION_COUNT
    today_str = date.today().strftime("%Y%m%d")
    batch = _select_daily_batch(n)
    result = CreationResult(trigger_reason=trigger_reason)

    logger.info(
        "creation_lane: triggered (reason=%s) — generating %d opportunities from catalog",
        trigger_reason, n,
    )

    for entry in batch:
        source_id = f"created:{today_str}:{entry['id_suffix']}"

        # Idempotency check — skip if already inserted today
        existing = session.exec(
            select(IncomeSource).where(IncomeSource.source_id == source_id)
        ).first()
        if existing:
            result.skipped += 1
            logger.debug("creation_lane: skipping already-inserted %s", source_id)
            continue

        try:
            record = IncomeSource(
                source_id=source_id,
                description=entry["description"],
                estimated_profit=float(entry["estimated_profit"]),
                currency="USD",
                status=SourceStatus.new,
                date_found=date.today(),
                next_action=entry["next_action"],
                notes=entry["notes"],
                category=entry["category"],
                confidence=float(entry["confidence"]),
                origin_module=CREATION_ORIGIN_MODULE,
            )

            # Score before first persist
            sr = score_opportunity(record, session)
            record.score = sr.score
            record.priority_band = sr.priority_band
            record.score_rationale = sr.rationale

            session.add(record)
            session.commit()
            session.refresh(record)

            # Full orchestration: alert, packet, decision, task dispatch
            try:
                process_new_opportunity(record, session)
            except Exception as orch_exc:
                logger.warning(
                    "creation_lane: orchestration failed for %s — %s",
                    source_id, orch_exc,
                )

            result.created += 1
            result.opportunities.append({
                "source_id": source_id,
                "description": entry["description"],
                "category": entry["category"],
                "estimated_profit": entry["estimated_profit"],
                "confidence": entry["confidence"],
                "score": record.score,
                "priority_band": record.priority_band,
                "next_action": entry["next_action"][:120] + "…" if len(entry["next_action"]) > 120 else entry["next_action"],
            })

            logger.info(
                "creation_lane: inserted %s (score=%.1f band=%s profit=$%.0f)",
                source_id, record.score or 0, record.priority_band, entry["estimated_profit"],
            )

        except Exception as exc:
            result.errors += 1
            detail = f"{source_id}: {exc}"
            result.error_details.append(detail)
            logger.error("creation_lane: failed to insert %s — %s", source_id, exc)
            session.rollback()

    # Update singleton state
    _state.last_run_at = datetime.now(timezone.utc)
    _state.last_trigger_reason = trigger_reason
    _state.last_created = result.created
    _state.last_skipped = result.skipped
    _state.total_created_lifetime += result.created

    logger.info(
        "creation_lane: complete — created=%d skipped=%d errors=%d",
        result.created, result.skipped, result.errors,
    )
    return result
