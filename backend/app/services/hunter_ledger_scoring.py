"""
Scoring for CanonicalOpportunity (Hunter execution ledger). Separate from
app.services.scoring (which scores IncomeSource) because the ledger's
rescue schema carries a different, addendum-defined field set — this is
Hunter's own scorer for that schema, not a Claude-side judgment call.

Deliberately simple and log-scaled like the existing IncomeSource scorer:
value dominates, red/yellow risk flags and an unresolved Commander
checkpoint pull the score down. 0-100 scale.
"""

from __future__ import annotations

import math

from app.models.hunter_ledger import CanonicalOpportunity

_VALUE_CEILING = 10_000.0
_BASELINE = 20.0
_RED_PENALTY = 12.0
_YELLOW_PENALTY = 4.0
_CHECKPOINT_PENALTY = 5.0
_MISSING_ELIGIBILITY_PENALTY = 8.0

_RISK_FIELDS = ("compliance_risk", "legal_risk", "privacy_risk", "financial_risk", "platform_risk")


def score_candidate(opp: CanonicalOpportunity) -> float:
    value = opp.probability_adjusted_pending_value or 0.0
    value_score = min(60.0, math.log10(max(value, 1.0)) / math.log10(_VALUE_CEILING) * 60.0) if value > 0 else 0.0

    penalty = 0.0
    for field_name in _RISK_FIELDS:
        text = (getattr(opp, field_name, None) or "").strip().lower()
        if text.startswith("red"):
            penalty += _RED_PENALTY
        elif text.startswith("yellow"):
            penalty += _YELLOW_PENALTY

    if opp.required_commander_checkpoints:
        penalty += _CHECKPOINT_PENALTY
    if not opp.eligibility or "not yet verified" in (opp.eligibility or "").lower() or "tbd" in (opp.eligibility or "").lower():
        penalty += _MISSING_ELIGIBILITY_PENALTY

    score = max(0.0, min(100.0, _BASELINE + value_score - penalty))
    return round(score, 1)
