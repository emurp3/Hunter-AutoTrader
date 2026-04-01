"""
Advisor coordination layer.

store_advisor_input()  — persist an advisor's recommendation for a source
get_inputs()           — all advisor inputs for a source
get_consensus()        — majority recommendation across advisors
get_disagreements()    — sources where advisors disagree
format_summary()       — human-readable summary for action packets
"""

from collections import Counter
from typing import Optional

from sqlmodel import Session, select

from app.models.advisor import AdvisorInput, AdvisorRecommendation


def store_advisor_input(
    source_id: str,
    advisor_name: str,
    recommendation: str,
    reasoning: str,
    session: Session,
    *,
    confidence: Optional[float] = None,
    raw_response_json: Optional[str] = None,
) -> AdvisorInput:
    entry = AdvisorInput(
        source_id=source_id,
        advisor_name=advisor_name,
        recommendation=recommendation,
        confidence=confidence,
        reasoning=reasoning,
        raw_response_json=raw_response_json,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def get_inputs(source_id: str, session: Session) -> list[AdvisorInput]:
    stmt = select(AdvisorInput).where(AdvisorInput.source_id == source_id).order_by(AdvisorInput.created_at)
    return list(session.exec(stmt).all())


def get_consensus(source_id: str, session: Session) -> Optional[str]:
    """Return the majority recommendation, or None if no inputs exist."""
    inputs = get_inputs(source_id, session)
    if not inputs:
        return None
    counts = Counter(i.recommendation for i in inputs)
    top, top_count = counts.most_common(1)[0]
    # Require strict majority (>50%) to call it consensus
    if top_count > len(inputs) / 2:
        return top
    return None


def get_disagreements(session: Session) -> list[str]:
    """Return source_ids where advisors gave conflicting recommendations."""
    stmt = select(AdvisorInput.source_id).distinct()
    source_ids = list(session.exec(stmt).all())
    disagreements = []
    for sid in source_ids:
        inputs = get_inputs(sid, session)
        if len(inputs) < 2:
            continue
        unique_recs = {i.recommendation for i in inputs}
        if len(unique_recs) > 1:
            disagreements.append(sid)
    return disagreements


def format_summary(source_id: str, session: Session) -> str:
    """One-line human-readable summary of advisor positions."""
    inputs = get_inputs(source_id, session)
    if not inputs:
        return "No advisor input."
    parts = [f"{i.advisor_name}={i.recommendation}({i.confidence:.0%})" if i.confidence is not None else f"{i.advisor_name}={i.recommendation}" for i in inputs]
    consensus = get_consensus(source_id, session)
    consensus_str = f" → consensus: {consensus}" if consensus else " → no consensus"
    return ", ".join(parts) + consensus_str
