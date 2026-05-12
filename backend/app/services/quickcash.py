"""
Quick-Cash Board — unified cross-lane opportunity ranker.

Aggregates from:
  - trading lane  (IncomeSource)
  - signal_copy   (CopySignal decision=mirror/partial_mirror)
  - forge         (ForgeOpportunity status=approved/live/detected)

Rank formula: (1/days_to_cash) * expected_revenue * confidence / effort_multiplier
"""
from __future__ import annotations
import logging
from datetime import datetime
from sqlmodel import Session, select

from app.models.copy_signal import CopySignal
from app.models.forge import ForgeOpportunity
from app.models.income_source import IncomeSource, SourceStatus

logger = logging.getLogger(__name__)

EFFORT_MULT = {"low": 1.0, "medium": 1.5, "high": 2.5}


def _rank(days_to_cash, revenue, confidence, effort) -> float:
    dtc = max(1, float(days_to_cash or 14))
    rev = float(revenue or 0)
    mult = EFFORT_MULT.get(str(effort), 1.5)
    return round((1 / dtc) * rev * confidence / mult, 4)


def get_quick_cash_board(session: Session, limit: int = 50) -> dict:
    items = []

    # Lane 1: Core Trading
    sources = session.exec(
        select(IncomeSource)
        .where(IncomeSource.status.in_([
            SourceStatus.active, SourceStatus.review_ready,
            SourceStatus.scored, SourceStatus.ingested,
        ]))
        .order_by(IncomeSource.score.desc())
        .limit(20)
    ).all()

    for s in sources:
        rev = float(s.estimated_profit or 0)
        conf = float(s.confidence or 0.5)
        items.append({
            "lane": "trading",
            "lane_label": "Core Trading",
            "id": s.id,
            "title": (s.description or "Trade Opportunity")[:80],
            "ticker_or_product": s.source_id,
            "expected_revenue": rev,
            "expected_margin_pct": None,
            "confidence_score": conf,
            "effort_level": "medium",
            "days_to_launch": 1,
            "days_to_cash": 3,
            "status": s.status,
            "action": "run_intake",
            "rank_score": _rank(3, rev, conf, "medium"),
        })

    # Lane 2: Signal Copy
    signals = session.exec(
        select(CopySignal)
        .where(CopySignal.decision.in_(["mirror", "partial_mirror"]))
        .where(CopySignal.executed == False)  # noqa: E712
        .order_by(CopySignal.confidence_score.desc())
        .limit(20)
    ).all()

    for sig in signals:
        amt = sig.amount_midpoint or sig.amount_high or sig.amount_low or 0
        est_pnl = float(amt) * 0.02
        items.append({
            "lane": "signal_copy",
            "lane_label": "Signal Copy",
            "id": sig.id,
            "title": f"{sig.decision.replace('_', ' ').title()}: {sig.ticker} ({sig.filer_name})",
            "ticker_or_product": sig.ticker,
            "expected_revenue": round(est_pnl, 2),
            "expected_margin_pct": 0.02,
            "confidence_score": sig.confidence_score,
            "effort_level": "low",
            "days_to_launch": 0,
            "days_to_cash": 2,
            "status": sig.decision,
            "action": f"/signals/{sig.id}/execute",
            "rank_score": _rank(2, est_pnl, sig.confidence_score, "low"),
            "filer": sig.filer_name,
            "source": sig.source,
            "latency_hours": sig.latency_hours,
            "decision_reason": sig.decision_reason,
        })

    # Lane 3: Forge
    forge_opps = session.exec(
        select(ForgeOpportunity)
        .where(ForgeOpportunity.status.in_(["detected", "scored", "approved", "live"]))
        .order_by(ForgeOpportunity.confidence_score.desc())
        .limit(20)
    ).all()

    for opp in forge_opps:
        items.append({
            "lane": "forge",
            "lane_label": "Opportunity Forge",
            "id": opp.id,
            "title": opp.title,
            "ticker_or_product": opp.vendor_name or opp.fulfillment_model,
            "expected_revenue": opp.estimated_revenue,
            "expected_margin_pct": opp.estimated_margin_pct,
            "confidence_score": opp.confidence_score,
            "effort_level": opp.effort_level,
            "days_to_launch": opp.days_to_launch,
            "days_to_cash": opp.days_to_cash,
            "status": opp.status,
            "action": f"/forge/{opp.id}/approve",
            "trigger_date": opp.trigger_date.isoformat() if opp.trigger_date else None,
            "trigger_name": opp.trigger_name,
            "rank_score": _rank(
                opp.days_to_cash, opp.estimated_revenue,
                opp.confidence_score, opp.effort_level
            ),
        })

    ranked = sorted(items, key=lambda x: x["rank_score"], reverse=True)

    return {
        "total": len(ranked),
        "board": ranked[:limit],
        "lanes": {
            "trading": sum(1 for x in ranked if x["lane"] == "trading"),
            "signal_copy": sum(1 for x in ranked if x["lane"] == "signal_copy"),
            "forge": sum(1 for x in ranked if x["lane"] == "forge"),
        },
        "top_opportunity": ranked[0] if ranked else None,
        "generated_at": datetime.utcnow().isoformat(),
    }
