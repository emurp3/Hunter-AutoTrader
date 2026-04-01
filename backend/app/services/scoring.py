"""
Scoring engine for income sources.

score_opportunity(income, session=None) -> ScoringResult
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session

from app.models.income_source import IncomeSource, PriorityBand, SourceStatus

PROFIT_CEILING = 10_000.0

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

    profit_ratio = math.log1p(income.estimated_profit) / math.log1p(PROFIT_CEILING)
    profit_score = round(min(profit_ratio, 1.0) * 60.0, 2)
    factors.append(f"profit={profit_score:.1f}/60")

    status_score = _STATUS_SCORE.get(income.status, 0.0)
    factors.append(f"status={status_score:.0f}/15")

    confidence_score = 0.0
    if income.confidence is not None:
        confidence_score = round(income.confidence * 12.0, 2)
    factors.append(f"confidence={confidence_score:.1f}/12")

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

    total = round(profit_score + status_score + confidence_score + recency_score + completeness_score, 2)

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
