"""
Daily opportunity rotation service.

30-day system: one advisor owns each calendar day and must produce
a structured, actionable profit opportunity. No debate loops.

Rotation schedule (perpetual, not just 30 days — scoreboard resets weekly):
  Monday    → Venice
  Tuesday   → Grok
  Wednesday → DeepSeek
  Thursday  → Venice
  Friday    → Grok
  Saturday  → DeepSeek
  Sunday    → Grok (fallback/review day)

Fallback order: assigned advisor → Grok → Venice → DeepSeek
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from sqlmodel import Session, select

from app.models.daily_opportunity import (
    AdvisorWeeklyScore,
    DailyOpportunity,
    OpportunityStatus,
)

logger = logging.getLogger(__name__)

# ── Rotation schedule ─────────────────────────────────────────────────────────
# weekday() → 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
_DAY_OWNER = {
    0: "venice",    # Monday
    1: "grok",      # Tuesday
    2: "deepseek",  # Wednesday
    3: "venice",    # Thursday
    4: "grok",      # Friday
    5: "deepseek",  # Saturday
    6: "grok",      # Sunday (review/fallback)
}

# Priority fallback order — always try Grok first if assigned advisor fails
_FALLBACK_ORDER = ["grok", "venice", "deepseek"]

# ── Advisor config ─────────────────────────────────────────────────────────────
_ADVISOR_CONFIG = {
    "venice": {
        "url_env": "VENICE_API_URL",
        "key_env": "VENICE_API_KEY",
        "default_url": "https://api.venice.ai/api/v1",
        "model_env": "VENICE_MODEL",
        "default_model": "llama-3.3-70b",
    },
    "deepseek": {
        "url_env": "DEEPSEEK_API_URL",
        "key_env": "DEEPSEEK_API_KEY",
        "default_url": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
    },
    "grok": {
        "url_env": "GROK_API_URL",
        "key_env": "GROK_API_KEY",
        "default_url": "https://api.x.ai/v1",
        "model_env": "GROK_MODEL",
        "default_model": "grok-3",
    },
}

_OPPORTUNITY_SYSTEM_PROMPT = """You are Hunter's chief profit advisor. Your job is to identify ONE specific, executable profit opportunity for today.

Rules:
- Be concrete. Name the actual product, service, platform, or trade.
- Profit must be achievable within 24-48 hours with under $100 capital.
- No abstract brainstorming. Output must be immediately actionable.
- Respond in valid JSON only.

Response format:
{
  "title": "Short opportunity name (under 10 words)",
  "lane": "trading" | "marketplace" | "service" | "digital",
  "rationale": "Why this should produce profit TODAY specifically (2-3 sentences)",
  "required_action": "The exact first step Hunter must take right now",
  "expected_profit": 25.00,
  "confidence": 0.75,
  "handoff_path": "marketplace_listing | service_outreach | trading | digital_product_launch | manual"
}"""

_OPPORTUNITY_USER_PROMPT = """Today is {today}. It is {weekday}.

Hunter has $100 weekly budget, operates in the US, and can execute across:
- Trading (paper/live Alpaca)
- Marketplace (Facebook Marketplace, OfferUp, Craigslist flips)
- Service (local business outreach, website builds, gig work)
- Digital (Etsy templates, Gumroad, simple digital products)

Identify the single best profit opportunity for today. Be decisive. No hedging."""


# ── Public API ─────────────────────────────────────────────────────────────────

def get_day_owner(target_date: Optional[date] = None) -> str:
    """Return the assigned advisor name for the given date (default: today)."""
    d = target_date or date.today()
    return _DAY_OWNER[d.weekday()]


def get_today_opportunity(session: Session) -> Optional[DailyOpportunity]:
    """Return today's opportunity if it already exists."""
    today = date.today()
    stmt = select(DailyOpportunity).where(DailyOpportunity.opp_date == today)
    return session.exec(stmt).first()


def generate_today_opportunity(session: Session) -> DailyOpportunity:
    """
    Generate today's opportunity using the assigned advisor (with fallback).
    If today's opportunity already exists, return it without re-generating.
    """
    existing = get_today_opportunity(session)
    if existing:
        logger.info("daily_opportunity: already exists for today — id=%d", existing.id)
        return existing

    today = date.today()
    assigned = get_day_owner(today)
    result = _call_with_fallback(assigned, today)

    opp = DailyOpportunity(
        opp_date=today,
        assigned_advisor=assigned,
        actual_advisor=result["actual_advisor"],
        title=result["title"],
        lane=result["lane"],
        rationale=result["rationale"],
        required_action=result["required_action"],
        expected_profit=result["expected_profit"],
        confidence=result["confidence"],
        handoff_path=result.get("handoff_path"),
        status=OpportunityStatus.pending,
        raw_response_json=result.get("raw_json"),
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)

    _upsert_weekly_score(assigned, session, generated=1)

    logger.info(
        "daily_opportunity: generated — id=%d advisor=%s lane=%s profit=$%.2f",
        opp.id, opp.actual_advisor, opp.lane, opp.expected_profit,
    )
    return opp


def mark_outcome(
    opp_id: int,
    status: str,
    session: Session,
    *,
    actual_profit: Optional[float] = None,
    notes: Optional[str] = None,
) -> DailyOpportunity:
    """Record what actually happened with an opportunity."""
    opp = session.get(DailyOpportunity, opp_id)
    if not opp:
        raise ValueError(f"DailyOpportunity id={opp_id} not found")

    opp.status = status
    opp.actual_profit = actual_profit
    opp.outcome_notes = notes
    opp.updated_at = datetime.now(timezone.utc)
    session.add(opp)
    session.commit()
    session.refresh(opp)

    # Update weekly score
    succeeded = status == OpportunityStatus.succeeded
    dispatched = status in (OpportunityStatus.dispatched, OpportunityStatus.succeeded)
    _upsert_weekly_score(
        opp.assigned_advisor,
        session,
        dispatched=1 if dispatched else 0,
        succeeded=1 if succeeded else 0,
        profit=actual_profit or 0.0,
    )
    _refresh_winner(opp.opp_date, session)
    return opp


def get_weekly_scoreboard(session: Session, week_start: Optional[date] = None) -> list[dict]:
    """Return ranked advisor scores for the current (or given) week."""
    ws = week_start or _current_week_start()
    stmt = select(AdvisorWeeklyScore).where(AdvisorWeeklyScore.week_start == ws)
    rows = list(session.exec(stmt).all())
    rows.sort(key=lambda r: (r.total_actual_profit, r.opportunities_succeeded), reverse=True)
    return [
        {
            "advisor": r.advisor_name,
            "week_start": r.week_start.isoformat(),
            "opportunities_generated": r.opportunities_generated,
            "opportunities_dispatched": r.opportunities_dispatched,
            "opportunities_succeeded": r.opportunities_succeeded,
            "total_actual_profit": round(r.total_actual_profit, 2),
            "is_winner": r.is_winner,
        }
        for r in rows
    ]


def get_opportunity_history(session: Session, limit: int = 30) -> list[DailyOpportunity]:
    """Return recent daily opportunities, newest first."""
    stmt = (
        select(DailyOpportunity)
        .order_by(DailyOpportunity.opp_date.desc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


# ── Internal helpers ───────────────────────────────────────────────────────────

def _call_with_fallback(assigned: str, target_date: date) -> dict:
    """
    Try the assigned advisor. On failure or missing key, fall back through
    Grok → Venice → DeepSeek while preserving the assigned label for scoring.
    """
    attempt_order = [assigned] + [a for a in _FALLBACK_ORDER if a != assigned]

    today_str = target_date.strftime("%Y-%m-%d")
    weekday_str = target_date.strftime("%A")

    for advisor in attempt_order:
        cfg = _ADVISOR_CONFIG.get(advisor)
        if not cfg:
            continue
        api_key = os.getenv(cfg["key_env"], "")
        if not api_key:
            logger.info("daily_opportunity: %s has no API key — skipping", advisor)
            continue
        try:
            result = _call_advisor_api(advisor, cfg, today_str, weekday_str)
            result["actual_advisor"] = advisor
            return result
        except Exception as exc:
            logger.warning("daily_opportunity: %s failed — %s", advisor, exc)
            continue

    # All advisors unavailable — return a safe structural placeholder
    logger.error("daily_opportunity: all advisors unavailable — using placeholder")
    return {
        "actual_advisor": "none",
        "title": "No advisor available today",
        "lane": "marketplace",
        "rationale": "All advisor APIs unavailable. Manual direction required.",
        "required_action": "Check advisor API key configuration in Render.",
        "expected_profit": 0.0,
        "confidence": 0.0,
        "handoff_path": "manual",
        "raw_json": None,
    }


def _call_advisor_api(advisor: str, cfg: dict, today_str: str, weekday_str: str) -> dict:
    api_key = os.getenv(cfg["key_env"], "")
    base_url = os.getenv(cfg["url_env"], cfg["default_url"])
    model = os.getenv(cfg["model_env"], cfg["default_model"])

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _OPPORTUNITY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _OPPORTUNITY_USER_PROMPT.format(
                    today=today_str, weekday=weekday_str
                ),
            },
        ],
        "temperature": 0.4,
        "max_tokens": 600,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
        timeout=45.0,
    )
    response.raise_for_status()
    data = response.json()
    raw_content = data["choices"][0]["message"]["content"]

    # Strip markdown fences if present
    content = raw_content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()

    parsed = json.loads(content)
    lane = parsed.get("lane", "marketplace")
    valid_lanes = {"trading", "marketplace", "service", "digital"}
    if lane not in valid_lanes:
        lane = "marketplace"

    confidence = float(parsed.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    return {
        "title": str(parsed.get("title", "Untitled opportunity"))[:200],
        "lane": lane,
        "rationale": str(parsed.get("rationale", ""))[:1000],
        "required_action": str(parsed.get("required_action", ""))[:500],
        "expected_profit": float(parsed.get("expected_profit", 0.0)),
        "confidence": confidence,
        "handoff_path": parsed.get("handoff_path"),
        "raw_json": json.dumps(data),
    }


def _current_week_start() -> date:
    today = date.today()
    return today.replace(day=today.day - today.weekday())  # Monday of current week


def _upsert_weekly_score(
    advisor: str,
    session: Session,
    *,
    generated: int = 0,
    dispatched: int = 0,
    succeeded: int = 0,
    profit: float = 0.0,
) -> AdvisorWeeklyScore:
    ws = _current_week_start()
    stmt = select(AdvisorWeeklyScore).where(
        AdvisorWeeklyScore.week_start == ws,
        AdvisorWeeklyScore.advisor_name == advisor,
    )
    row = session.exec(stmt).first()
    if not row:
        row = AdvisorWeeklyScore(week_start=ws, advisor_name=advisor)
        session.add(row)

    row.opportunities_generated += generated
    row.opportunities_dispatched += dispatched
    row.opportunities_succeeded += succeeded
    row.total_actual_profit += profit
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row


def _refresh_winner(ref_date: date, session: Session) -> None:
    """Mark the current weekly winner (highest profit, then most successes)."""
    ws = _current_week_start()
    stmt = select(AdvisorWeeklyScore).where(AdvisorWeeklyScore.week_start == ws)
    rows = list(session.exec(stmt).all())
    if not rows:
        return
    best = max(rows, key=lambda r: (r.total_actual_profit, r.opportunities_succeeded))
    for r in rows:
        r.is_winner = r.advisor_name == best.advisor_name
        r.updated_at = datetime.now(timezone.utc)
        session.add(r)
    session.commit()
