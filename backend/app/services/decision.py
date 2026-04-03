"""
Decision engine — the action layer between scoring and execution.

Evaluates each IncomeSource and produces an OpportunityDecision that captures:
  1. action_state   — what to do (ignore / watch / review_ready / ready_to_act / auto_execute)
  2. execution_path — which channel (outreach / arbitrage / local_pitch / automation / affiliate / trading)
  3. action_payload — concrete, structured next-step data (not vague summaries)
  4. capital_recommendation — how much bankroll to commit based on decision state
  5. approval gate  — what is blocking execution and why
  6. feedback loop  — score adjustment from historical lane/category performance

Thresholds are configurable via .env:
  HUNTER_DECISION_AUTO_EXECUTE_SCORE      (default 85)
  HUNTER_DECISION_AUTO_EXECUTE_CONFIDENCE (default 0.75)
  HUNTER_DECISION_READY_TO_ACT_SCORE      (default 65)
  HUNTER_DECISION_READY_TO_ACT_CONFIDENCE (default 0.55)
  HUNTER_DECISION_REVIEW_SCORE            (default 45)
  HUNTER_DECISION_WATCH_SCORE             (default 25)
  HUNTER_DECISION_AUTO_EXECUTE_MAX_CAPITAL (default 25.0)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.models.decision import ActionState, ExecutionPath, OpportunityDecision
from app.models.income_source import IncomeSource

logger = logging.getLogger(__name__)

# ── Configurable thresholds ───────────────────────────────────────────────────

def _thresh(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _thresholds() -> dict[str, float]:
    return {
        "auto_execute_score": _thresh("HUNTER_DECISION_AUTO_EXECUTE_SCORE", 85.0),
        "auto_execute_confidence": _thresh("HUNTER_DECISION_AUTO_EXECUTE_CONFIDENCE", 0.75),
        "auto_execute_max_capital": _thresh("HUNTER_DECISION_AUTO_EXECUTE_MAX_CAPITAL", 25.0),
        "ready_to_act_score": _thresh("HUNTER_DECISION_READY_TO_ACT_SCORE", 65.0),
        "ready_to_act_confidence": _thresh("HUNTER_DECISION_READY_TO_ACT_CONFIDENCE", 0.55),
        "review_score": _thresh("HUNTER_DECISION_REVIEW_SCORE", 45.0),
        "watch_score": _thresh("HUNTER_DECISION_WATCH_SCORE", 25.0),
    }


# ── Action state determination ────────────────────────────────────────────────

def _determine_action_state(source: IncomeSource, t: dict[str, float]) -> ActionState:
    score = source.score or 0.0
    confidence = source.confidence or 0.0
    profit = source.estimated_profit or 0.0

    if (
        score >= t["auto_execute_score"]
        and confidence >= t["auto_execute_confidence"]
        and profit <= t["auto_execute_max_capital"]
    ):
        return ActionState.auto_execute

    if score >= t["ready_to_act_score"] and confidence >= t["ready_to_act_confidence"]:
        return ActionState.ready_to_act

    if score >= t["review_score"] or source.priority_band in ("high", "elite"):
        return ActionState.review_ready

    if score >= t["watch_score"]:
        return ActionState.watch

    return ActionState.ignore


# ── Execution path routing ────────────────────────────────────────────────────

# Priority order: category overrides > origin_module default
_CATEGORY_PATH_MAP: dict[str, ExecutionPath] = {
    "trading": ExecutionPath.trading,
    "electronics-flip": ExecutionPath.arbitrage,
    "home-goods-flip": ExecutionPath.arbitrage,
    "collectible-flip": ExecutionPath.arbitrage,
    "camera-flip": ExecutionPath.arbitrage,
    "tool-flip": ExecutionPath.arbitrage,
    "general-flip": ExecutionPath.arbitrage,
}

_ORIGIN_PATH_MAP: dict[str, ExecutionPath] = {
    "gig_scanner": ExecutionPath.outreach,
    "marketplace_scanner": ExecutionPath.arbitrage,
    "local_business_prospector": ExecutionPath.local_pitch,
    "github_scanner": ExecutionPath.automation_proposal,
    "social_listener": ExecutionPath.affiliate_content,
    "digital_product_scanner": ExecutionPath.affiliate_content,
    "autotrader": ExecutionPath.trading,
    "autotrader_seed": ExecutionPath.advisor_review,
}


def _route_execution_path(source: IncomeSource) -> ExecutionPath:
    category = (source.category or "").lower()
    if category in _CATEGORY_PATH_MAP:
        return _CATEGORY_PATH_MAP[category]
    origin = (source.origin_module or "").lower()
    return _ORIGIN_PATH_MAP.get(origin, ExecutionPath.advisor_review)


# ── Approval gate ─────────────────────────────────────────────────────────────

def _check_approval(
    source: IncomeSource,
    action_state: ActionState,
    capital_rec: float,
    t: dict[str, float],
) -> tuple[bool, str | None, bool, str | None]:
    """Returns (approval_required, approval_reason, execution_ready, blocked_by)."""
    approval_required = False
    approval_reason = None
    blocked_by = None

    confidence = source.confidence or 0.0

    # Low confidence always blocks execution
    if confidence < 0.35:
        return True, "Confidence below 0.35 — manual review required", False, "low_confidence"

    # Auto-execute only when ALL criteria pass
    if action_state == ActionState.auto_execute:
        approval_required = False
        blocked_by = None
        return False, None, True, None

    # Capital over threshold requires approval
    if capital_rec and capital_rec > t["auto_execute_max_capital"]:
        approval_required = True
        approval_reason = f"Capital recommendation ${capital_rec:.0f} exceeds auto-execute limit ${t['auto_execute_max_capital']:.0f}"
        blocked_by = "approval"

    # review_ready and ready_to_act require commander sign-off
    if action_state in (ActionState.review_ready, ActionState.ready_to_act) and not approval_required:
        approval_required = True
        approval_reason = "Requires commander review before execution"
        blocked_by = "approval"

    # watch / ignore are never execution-ready
    if action_state in (ActionState.watch, ActionState.ignore):
        return False, None, False, "action_state"

    execution_ready = not approval_required
    return approval_required, approval_reason, execution_ready, blocked_by


# ── Capital recommendation ────────────────────────────────────────────────────

def _capital_recommendation(
    source: IncomeSource,
    action_state: ActionState,
    t: dict[str, float],
) -> float | None:
    if action_state == ActionState.ignore:
        return 0.0
    if action_state == ActionState.watch:
        return 0.0

    confidence = source.confidence or 0.0
    profit = source.estimated_profit or 0.0
    band = source.priority_band or "low"

    if confidence < 0.35:
        return 0.0

    # Base: fraction of estimated profit scaled by confidence
    base = profit * confidence

    # Band multiplier
    multipliers = {"elite": 0.30, "high": 0.20, "medium": 0.12, "low": 0.05}
    base *= multipliers.get(band, 0.10)

    # Auto-execute capped at the configured max
    if action_state == ActionState.auto_execute:
        base = min(base, t["auto_execute_max_capital"])

    return round(max(0.0, base), 2) if base > 0 else None


# ── Action payload generation ─────────────────────────────────────────────────

def _generate_action_payload(source: IncomeSource, path: ExecutionPath) -> dict[str, Any]:
    desc = source.description or ""
    profit = source.estimated_profit or 0.0
    confidence = source.confidence or 0.0
    notes = source.notes or ""
    next_action = source.next_action or ""

    if path == ExecutionPath.outreach:
        # Extract job title — typically the first part before " at "
        title = desc.split(" at ")[0].strip() if " at " in desc else desc[:60]
        company = desc.split(" at ")[1].split("[")[0].strip() if " at " in desc else "the company"
        return {
            "path": "outreach",
            "headline": title,
            "target": company,
            "action": f"Apply for {title} at {company}",
            "pitch": f"Lead with automation/efficiency experience — estimated ${profit:.0f}/mo value",
            "response_template": (
                f"Hi {company} team, I'm applying for the {title} role. "
                f"I bring direct experience in [{', '.join(_extract_tags(desc))}] "
                f"and can deliver results within the first 30 days."
            ),
            "time_to_revenue_estimate": "2–6 weeks",
            "priority_action": next_action or f"Apply via source link — deadline sensitive",
            "confidence": confidence,
        }

    if path == ExecutionPath.arbitrage:
        # Extract price from description
        import re
        price_match = re.search(r"\$([0-9,]+\.?\d*)", desc)
        buy_price = float(price_match.group(1).replace(",", "")) if price_match else profit / 0.15
        sell_target = round(buy_price * 1.15, 2)
        margin = round(sell_target - buy_price, 2)
        return {
            "path": "arbitrage",
            "item": desc[:80],
            "buy_price": round(buy_price, 2),
            "sell_target": sell_target,
            "estimated_margin": margin,
            "sell_platforms": ["eBay", "Facebook Marketplace", "OfferUp"],
            "action": f"Buy at ${buy_price:.0f}, list at ${sell_target:.0f} — target ${margin:.0f} margin",
            "time_to_revenue_estimate": "24–72 hours",
            "priority_action": next_action or "Purchase within 24h and list immediately",
            "confidence": confidence,
        }

    if path == ExecutionPath.local_pitch:
        # Extract business name from description
        business = desc.split(" shows ")[0].strip() if " shows " in desc else desc[:50]
        gap = desc.split("gaps: ")[1].strip() if "gaps: " in desc else "online presence gaps"
        return {
            "path": "local_pitch",
            "business": business,
            "gap_identified": gap,
            "service_offer": f"Fix {gap} for ${profit:.0f}",
            "pitch": (
                f"Hi, I noticed {business} is missing {gap}. "
                f"I can set this up professionally within 48h for ${profit:.0f}. "
                f"No upfront risk — you pay when satisfied."
            ),
            "contact_strategy": "Google Maps → call → leave voicemail → follow-up email",
            "pricing_suggestion": round(profit * 1.2, 0),
            "time_to_revenue_estimate": "3–10 days",
            "priority_action": next_action or f"Call or visit {business} within 24h",
            "confidence": confidence,
        }

    if path == ExecutionPath.automation_proposal:
        # GitHub issue/repo context
        tags = _extract_tags(desc)
        return {
            "path": "automation_proposal",
            "project": desc[:80],
            "opportunity_type": "bounty" if "bounty" in desc.lower() else "open_source_consulting",
            "skills_required": tags,
            "proposal": f"Implement {desc[:60]} — deliver working solution with tests",
            "monetization": f"Claim bounty / invoice at ${profit:.0f} for implementation",
            "response_template": (
                f"I'd like to take on this issue. I have experience with {', '.join(tags[:3])} "
                f"and can deliver a working solution with tests within [X] days. "
                f"Happy to discuss approach before starting."
            ),
            "time_to_revenue_estimate": "1–3 weeks",
            "priority_action": next_action or "Comment on issue with proposal summary",
            "confidence": confidence,
        }

    if path == ExecutionPath.affiliate_content:
        topic = desc[:60]
        tags = _extract_tags(desc)
        return {
            "path": "affiliate_content",
            "topic": topic,
            "content_angle": f"Review/comparison piece on {topic}",
            "platforms": ["Reddit", "HN", "personal blog", "Medium"],
            "affiliate_angle": f"Include affiliate links for {', '.join(tags[:2]) if tags else 'related products'}",
            "content_outline": [
                f"Hook: Why {topic} matters now",
                "Personal experience / use case",
                "Top 3 options with comparison",
                "Recommendation with affiliate link",
                "Call to action",
            ],
            "estimated_monthly_revenue": round(profit, 2),
            "time_to_revenue_estimate": "1–4 weeks",
            "priority_action": next_action or "Draft 500-word piece within 72h",
            "confidence": confidence,
        }

    if path == ExecutionPath.trading:
        return {
            "path": "trading",
            "description": desc[:80],
            "estimated_return": profit,
            "action": "Route to Alpaca execution via POST /execution/trade",
            "requires": ["symbol", "qty_or_notional", "side"],
            "time_to_revenue_estimate": "minutes to days",
            "priority_action": next_action or "Generate trade order and submit for review",
            "confidence": confidence,
        }

    # advisor_review or none
    return {
        "path": path.value,
        "description": desc[:80],
        "action": "Route to advisor consensus layer before execution",
        "priority_action": next_action or "Request advisor consensus via POST /advisors/{source_id}/consult",
        "confidence": confidence,
    }


def _extract_tags(text: str) -> list[str]:
    """Pull likely skill/category tags from free text."""
    import re
    # grab capitalized phrases and bracketed content
    tags: list[str] = []
    bracketed = re.findall(r"\[([^\]]+)\]", text)
    for b in bracketed:
        tags.extend(t.strip() for t in b.split(","))
    words = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\b", text)
    tags.extend(words)
    # dedupe and limit
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        low = t.lower()
        if low not in seen and len(t) > 2:
            seen.add(low)
            result.append(t)
        if len(result) >= 6:
            break
    return result


# ── Feedback loop ─────────────────────────────────────────────────────────────

def _get_feedback_adjustment(source: IncomeSource, session: Session) -> float:
    """
    Query historical performance for this source's lane/category.
    Returns a float score adjustment (-4.0 to +4.0).
    Uses performance service to keep logic in one place.
    """
    try:
        from app.services.performance import get_feedback_adjustment
        result = get_feedback_adjustment(source, session)
        # performance service returns a dict with an "adjustment" key
        if isinstance(result, dict):
            return float(result.get("adjustment", 0.0))
        return float(result)
    except Exception as exc:
        logger.debug("feedback_adjustment skipped: %s", exc)
        return 0.0


# ── Main decision function ────────────────────────────────────────────────────

def decide(source: IncomeSource, session: Session) -> OpportunityDecision:
    """
    Evaluate an IncomeSource and produce (or update) its OpportunityDecision.
    Idempotent — calling again updates the existing record.
    """
    t = _thresholds()
    feedback = _get_feedback_adjustment(source, session)

    # Use feedback-adjusted score for decisions
    adjusted_score = (source.score or 0.0) + feedback

    # Temporarily swap score for threshold checks
    original_score = source.score
    source.score = adjusted_score
    action_state = _determine_action_state(source, t)
    source.score = original_score  # restore

    execution_path = _route_execution_path(source)
    capital_rec = _capital_recommendation(source, action_state, t)
    approval_required, approval_reason, execution_ready, blocked_by = _check_approval(
        source, action_state, capital_rec or 0.0, t
    )
    payload = _generate_action_payload(source, execution_path)

    # Upsert decision record
    existing = session.exec(
        select(OpportunityDecision).where(OpportunityDecision.source_id == source.source_id)
    ).first()

    now = datetime.now(timezone.utc)

    if existing:
        decision = existing
    else:
        decision = OpportunityDecision(source_id=source.source_id, decided_at=now)

    decision.action_state = action_state.value
    decision.execution_path = execution_path.value
    decision.score_at_decision = source.score
    decision.confidence_at_decision = source.confidence
    decision.feedback_adjustment = round(feedback, 2)
    decision.approval_required = approval_required
    decision.approval_reason = approval_reason
    decision.execution_ready = execution_ready
    decision.blocked_by = blocked_by
    decision.capital_recommendation = capital_rec
    decision.set_action_payload(payload)
    decision.updated_at = now

    session.add(decision)
    session.commit()
    session.refresh(decision)

    logger.info(
        "decide: %s → state=%s path=%s capital=%.2f feedback%+.1f",
        source.source_id,
        action_state.value,
        execution_path.value,
        capital_rec or 0.0,
        feedback,
    )

    return decision


def run_decisions(session: Session, limit: int = 200) -> dict:
    """Run the decision engine across all scored opportunities."""
    sources = session.exec(
        select(IncomeSource)
        .where(IncomeSource.score.is_not(None))
        .order_by(IncomeSource.score.desc())
        .limit(limit)
    ).all()

    results: dict[str, int] = {
        "auto_execute": 0,
        "ready_to_act": 0,
        "review_ready": 0,
        "watch": 0,
        "ignore": 0,
        "errors": 0,
    }

    for source in sources:
        try:
            d = decide(source, session)
            results[d.action_state] = results.get(d.action_state, 0) + 1
        except Exception as exc:
            logger.error("decide failed for %s: %s", source.source_id, exc)
            results["errors"] += 1

    results["total_processed"] = len(sources)
    return results


def get_decision(source_id: str, session: Session) -> OpportunityDecision | None:
    return session.exec(
        select(OpportunityDecision).where(OpportunityDecision.source_id == source_id)
    ).first()


def approve_decision(source_id: str, reviewer_note: str | None, session: Session) -> OpportunityDecision:
    decision = get_decision(source_id, session)
    if not decision:
        raise ValueError(f"No decision found for source_id={source_id}")

    decision.approval_required = False
    decision.approved_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    decision.reviewer_note = reviewer_note
    decision.reviewed_at = datetime.now(timezone.utc)
    decision.execution_ready = decision.action_state not in (ActionState.ignore, ActionState.watch)
    decision.blocked_by = None
    decision.updated_at = datetime.now(timezone.utc)

    session.add(decision)
    session.commit()
    session.refresh(decision)
    return decision


def list_decisions(
    session: Session,
    action_state: str | None = None,
    execution_path: str | None = None,
    execution_ready: bool | None = None,
    limit: int = 100,
) -> list[OpportunityDecision]:
    stmt = select(OpportunityDecision).order_by(
        OpportunityDecision.updated_at.desc()
    ).limit(limit)
    decisions = session.exec(stmt).all()

    if action_state:
        decisions = [d for d in decisions if d.action_state == action_state]
    if execution_path:
        decisions = [d for d in decisions if d.execution_path == execution_path]
    if execution_ready is not None:
        decisions = [d for d in decisions if d.execution_ready == execution_ready]

    return decisions
