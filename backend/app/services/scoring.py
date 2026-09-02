"""
Scoring engine for income sources.

score_opportunity(income, session=None) -> ScoringResult

--------------------------------------------------------------------------
2026-09-02 rewrite — expected-value ranking (fixes real production bug)
--------------------------------------------------------------------------
Previous version scored raw estimated_profit against a hard $10,000
ceiling: `min(profit_ratio, 1.0)`. That meant a $500,000 grant and a
$10,000 opportunity scored identically on the dominant 60-point profit
axis — the exact "big opportunity buried under fifty small ones" failure
mode. Confidence/probability was a separate, minor 12-point factor instead
of being multiplied into profit, so a 90%-confidence $500 flip and a
5%-confidence $500 long-shot scored the same on the profit axis too.

This version:
  1. Uses EXPECTED VALUE (estimated_profit * confidence) as the basis for
     the profit axis, not raw estimated_profit.
  2. Removes the hard ceiling clip — profit_score can exceed its nominal
     60-point budget for genuinely large EV opportunities instead of
     flattening out. PROFIT_CEILING remains only as the log-scale anchor,
     not a cap.
  3. Adds a small speed-to-cash factor: a fast, short-path opportunity
     (e.g. a discounted item with an identified buyer) can now edge out
     a slower opportunity of similar EV, per instructions that a very
     short discovery-to-cash path should be able to jump the queue.
  4. Adds a capability-fit factor: opportunities that plausibly match
     EMurph's existing skills/assets (see app.services.capability_fit)
     get a modest boost, since those have a materially shorter real
     path to execution than a cold-start opportunity of the same EV.
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session

from app.models.income_source import IncomeSource, PriorityBand, SourceStatus
from app.services.capability_fit import score_capability_fit

PROFIT_CEILING = 10_000.0  # log-scale anchor only — NOT a hard cap, see above.

# Default confidence used when an opportunity has no explicit confidence
# value, so it isn't scored as if EV were zero (which would bury sourced
# opportunities that simply didn't set a confidence field).
_DEFAULT_CONFIDENCE = 0.3

# Rough time-to-cash heuristic by category/origin_module. This is a
# heuristic, not a measured field — Hunter doesn't yet track a real
# discovery-to-cash timestamp per opportunity. Keys are matched against
# category and origin_module (lowercased); first match wins.
_FAST_CASH_SIGNALS: dict[str, float] = {
    "marketplace": 10.0,       # discounted item, resale/flip — often same-day
    "resale": 10.0,
    "arbitrage": 9.0,
    "local_business": 6.0,     # service pitch — days, not weeks
    "service": 6.0,
    "digital": 5.0,            # build + list — days
    "gig": 5.0,
    "affiliate": 4.0,
    "github": 3.0,
    "social": 3.0,
    "grant": 0.5,              # months-long application/award cycle
    "rfp": 0.5,
    "contract": 1.0,
}
_FAST_CASH_KEYWORD_BONUS = 2.0
_FAST_CASH_KEYWORDS = ("identified buyer", "ready to execute", "same day", "immediate", "1 business day")

_STATUS_SCORE: dict[str, float] = {
    SourceStatus.active: 15.0,
    SourceStatus.review_ready: 14.0,
    SourceStatus.budgeted: 13.0,
    SourceStatus.prioritized: 12.0,
    SourceStatus.ingested: 11.0,
    SourceStatus.scored: 10.0,
    SourceStatus.new: 10.0,
    SourceStatus.parked: 5.0,
    SourceStatus.outcome_logged: 4.0,
    SourceStatus.complete: 3.0,
    SourceStatus.archived: 1.0,
    SourceStatus.exhausted: 0.0,
    SourceStatus.rejected: 0.0,
    SourceStatus.failed: 0.0,
}


@dataclass
class ScoringResult:
    score: float
    priority_band: str
    rationale: str


def score_opportunity(income: IncomeSource, session: Optional[Session] = None) -> ScoringResult:
    factors: list[str] = []

    confidence = income.confidence if income.confidence is not None else _DEFAULT_CONFIDENCE
    expected_value = max(0.0, income.estimated_profit) * confidence

    # No hard ceiling clip: a genuinely large EV opportunity keeps growing
    # this score past its nominal 60-point budget instead of flattening to
    # the same value as a $10k opportunity. See module docstring.
    profit_ratio = math.log1p(expected_value) / math.log1p(PROFIT_CEILING)
    profit_score = round(profit_ratio * 60.0, 2)
    factors.append(f"profit(EV=${expected_value:,.0f})={profit_score:.1f}/60")

    status_score = _STATUS_SCORE.get(income.status, 0.0)
    factors.append(f"status={status_score:.0f}/15")

    # Confidence still contributes a small standalone factor beyond its role
    # inside EV, so two equal-EV opportunities where one is "80% at $100"
    # and the other is "8% at $1,000" don't score identically — the more
    # certain one is still slightly preferred, all else equal.
    confidence_score = round(confidence * 6.0, 2)
    factors.append(f"confidence={confidence_score:.1f}/6")

    recency_score = 0.0
    if income.date_found:
        age_days = (date.today() - income.date_found).days
        if age_days <= 7:
            recency_score = 8.0
        elif age_days <= 30:
            recency_score = 5.0
        elif age_days <= 90:
            recency_score = 2.0
    factors.append(f"recency={recency_score:.0f}/8")

    completeness_score = 0.0
    if income.next_action:
        completeness_score += 2.0
    if income.notes:
        completeness_score += 2.0
    if income.category:
        completeness_score += 1.0
    factors.append(f"completeness={completeness_score:.0f}/5")

    speed_score = 0.0
    category_key = (income.category or "").lower()
    origin_key = (income.origin_module or "").lower()
    for signal, points in _FAST_CASH_SIGNALS.items():
        if signal in category_key or signal in origin_key:
            speed_score = max(speed_score, points)
    haystack = f"{income.description or ''} {income.next_action or ''}".lower()
    if any(keyword in haystack for keyword in _FAST_CASH_KEYWORDS):
        speed_score = min(10.0, speed_score + _FAST_CASH_KEYWORD_BONUS)
    factors.append(f"speed_to_cash={speed_score:.1f}/10")

    fit = score_capability_fit(
        description=income.description or "",
        next_action=income.next_action or "",
        category=income.category or "",
        origin_module=income.origin_module or "",
    )
    factors.append(f"capability_fit={fit.score:.1f}/10" + (f" ({','.join(fit.matched_tags)})" if fit.matched_tags else ""))

    total = round(
        profit_score
        + status_score
        + confidence_score
        + recency_score
        + completeness_score
        + speed_score
        + fit.score,
        2,
    )

    if session is not None:
        try:
            from app.services.performance import get_feedback_adjustment

            feedback = get_feedback_adjustment(income, session)
            adjustment = feedback["adjustment"]
            if adjustment:
                total = round(max(0.0, min(100.0, total + adjustment)), 2)
                factors.append(f"feedback={adjustment:+.1f}")
        except Exception:
            pass

    if total >= 80:
        band = PriorityBand.elite
    elif total >= 60:
        band = PriorityBand.high
    elif total >= 35:
        band = PriorityBand.medium
    else:
        band = PriorityBand.low

    rationale = f"[{band.value.upper()}] score={total} | {' | '.join(factors)}"
    return ScoringResult(score=total, priority_band=band.value, rationale=rationale)


def compute_score(income: IncomeSource, session: Optional[Session] = None) -> float:
    return score_opportunity(income, session=session).score
