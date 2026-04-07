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
    ExecutabilityClass,
    OpportunityStatus,
)
from app.models.income_source import IncomeSource, SourceStatus

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

CRITICAL — executability ranking:
Hunter is an automated system (AI + HVA worker). Opportunities are ranked by how much Hunter can do WITHOUT human intervention:
  fully_executable  = Hunter/HVA completes the entire workflow autonomously (preferred)
  semi_executable   = automated start with ONE unavoidable human step (e.g. final cash handoff)
  manual_only       = requires physical pickup, offline negotiation, or in-person presence (penalized)

Strongly prefer fully_executable > semi_executable > manual_only.
DO NOT suggest opportunities that require physical pickup, in-person sourcing, or manual negotiation
unless no fully_executable or semi_executable option exists.

Hunter's autonomous execution paths (these are fully_executable or semi_executable):
  - trading          → Alpaca API, fully automated
  - service_outreach → automated email/DM outreach via HVA, semi_executable
  - digital_product_launch → spec + publish to Gumroad/Etsy via HVA, semi_executable
  - marketplace_listing → HVA posts listing, but PHYSICAL PICKUP makes this manual_only

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
  "handoff_path": "marketplace_listing | service_outreach | trading | digital_product_launch | manual",
  "executability": "fully_executable" | "semi_executable" | "manual_only",
  "human_dependency_reason": "null if fully_executable, else short reason why a human must act",
  "required_human_actions": "null if fully_executable, else pipe-separated list: action1 | action2"
}"""

_OPPORTUNITY_USER_PROMPT = """Today is {today}. It is {weekday}.

Hunter has $100 weekly budget, operates in the US, and can execute across:
- Trading (paper/live Alpaca) — FULLY AUTONOMOUS
- Service outreach (automated email/DM to local businesses via HVA) — SEMI-AUTONOMOUS
- Digital products (Etsy templates, Gumroad PDFs, spec-to-launch via HVA) — SEMI-AUTONOMOUS
- Marketplace listing (HVA posts ad, but physical pickup is always required) — MANUAL

Today's priority: identify an opportunity Hunter can execute without physical presence.
Prefer trading or digital/service lanes. Marketplace is acceptable ONLY if the item can be
shipped (no in-person pickup needed).

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


def get_pipeline_source_id(target_date: Optional[date] = None) -> str:
    """Stable IncomeSource id used to surface the daily opportunity in the main pipeline."""
    d = target_date or date.today()
    return f"daily-opp-{d.isoformat()}"


def get_source_id_for_opportunity(opp: DailyOpportunity) -> str:
    return get_pipeline_source_id(opp.opp_date)


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
        executability=result.get("executability", ExecutabilityClass.manual_only),
        human_dependency_reason=result.get("human_dependency_reason"),
        required_human_actions=result.get("required_human_actions"),
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)

    _upsert_weekly_score(assigned, session, generated=1)

    logger.info(
        "daily_opportunity: generated — id=%d advisor=%s lane=%s executability=%s profit=$%.2f",
        opp.id, opp.actual_advisor, opp.lane, opp.executability, opp.expected_profit,
    )
    return opp


def generate_today_opportunity_and_sync(session: Session) -> DailyOpportunity:
    """
    Generate today's daily opportunity and mirror it into the existing
    IncomeSource-based pipeline so the dashboard and quotas can see it.
    """
    existing = get_today_opportunity(session)
    if existing:
        _ensure_pipeline_source(existing, session)
        return existing

    opp = generate_today_opportunity(session)
    _ensure_pipeline_source(opp, session)
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

_EXECUTABILITY_RANK = {
    ExecutabilityClass.fully_executable: 0,
    ExecutabilityClass.semi_executable: 1,
    ExecutabilityClass.manual_only: 2,
}


def _call_with_fallback(assigned: str, target_date: date) -> dict:
    """
    Try the assigned advisor first, then fall back through Grok → Venice → DeepSeek.
    Prefers executability: fully_executable > semi_executable > manual_only.
    If all reachable advisors return manual_only, returns the first manual result
    rather than calling every advisor twice.
    """
    attempt_order = [assigned] + [a for a in _FALLBACK_ORDER if a != assigned]

    today_str = target_date.strftime("%Y-%m-%d")
    weekday_str = target_date.strftime("%A")

    best_result: dict | None = None   # best candidate seen so far
    best_rank = 99                    # lower = better (see _EXECUTABILITY_RANK)

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
            rank = _EXECUTABILITY_RANK.get(result.get("executability"), 2)
            logger.info(
                "daily_opportunity: %s → executability=%s rank=%d",
                advisor, result.get("executability"), rank,
            )
            if rank < best_rank:
                best_rank = rank
                best_result = result
            # Stop early if we already have a fully_executable opportunity
            if best_rank == 0:
                break
        except Exception as exc:
            logger.warning("daily_opportunity: %s failed — %s", advisor, exc)
            continue

    if best_result:
        return best_result

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
        "executability": ExecutabilityClass.manual_only,
        "human_dependency_reason": "No advisor APIs reachable.",
        "required_human_actions": "Check GROK_API_KEY, VENICE_API_KEY, DEEPSEEK_API_KEY in Render.",
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

    # ── Executability classification ───────────────────────────────────────────
    valid_exec = {e.value for e in ExecutabilityClass}
    raw_exec = parsed.get("executability", "manual_only")
    executability = raw_exec if raw_exec in valid_exec else ExecutabilityClass.manual_only

    human_dep = parsed.get("human_dependency_reason")
    if human_dep and str(human_dep).lower() in ("null", "none", ""):
        human_dep = None

    req_human = parsed.get("required_human_actions")
    if req_human and str(req_human).lower() in ("null", "none", ""):
        req_human = None

    return {
        "title": str(parsed.get("title", "Untitled opportunity"))[:200],
        "lane": lane,
        "rationale": str(parsed.get("rationale", ""))[:1000],
        "required_action": str(parsed.get("required_action", ""))[:500],
        "expected_profit": float(parsed.get("expected_profit", 0.0)),
        "confidence": confidence,
        "handoff_path": parsed.get("handoff_path"),
        "executability": executability,
        "human_dependency_reason": str(human_dep)[:500] if human_dep else None,
        "required_human_actions": str(req_human)[:500] if req_human else None,
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


def _ensure_pipeline_source(opp: DailyOpportunity, session: Session) -> IncomeSource:
    """
    Mirror the daily opportunity into the main IncomeSource pipeline so the
    existing dashboard, strategy, and packet flows can reuse it.
    """
    source_id = get_source_id_for_opportunity(opp)
    source = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()

    notes_parts = [
        f"daily_opportunity_id={opp.id}",
        f"assigned_advisor={opp.assigned_advisor}",
        f"actual_advisor={opp.actual_advisor}",
        f"lane={opp.lane}",
        f"executability={opp.executability}",
        f"status={opp.status}",
        f"rationale={opp.rationale}",
    ]
    if opp.handoff_path:
        notes_parts.append(f"handoff_path={opp.handoff_path}")
    if opp.human_dependency_reason:
        notes_parts.append(f"human_dependency_reason={opp.human_dependency_reason}")
    if opp.required_human_actions:
        notes_parts.append(f"required_human_actions={opp.required_human_actions}")
    notes = "\n".join(notes_parts)

    created = False
    if not source:
        source = IncomeSource(
            source_id=source_id,
            description=opp.title,
            estimated_profit=max(0.0, opp.expected_profit),
            currency="USD",
            status=SourceStatus.new,
            date_found=opp.opp_date,
            next_action=opp.required_action,
            notes=notes,
            origin_module="daily_opportunity",
            category=opp.lane,
            confidence=opp.confidence,
        )
        session.add(source)
        session.commit()
        session.refresh(source)
        created = True
    else:
        source.description = opp.title
        source.estimated_profit = max(0.0, opp.expected_profit)
        source.next_action = opp.required_action
        source.notes = notes
        source.origin_module = source.origin_module or "daily_opportunity"
        source.category = opp.lane
        source.confidence = opp.confidence
        session.add(source)
        session.commit()
        session.refresh(source)

    if created or source.score is None:
        from app.services.orchestrator import process_new_opportunity

        process_new_opportunity(source, session)

    return source
