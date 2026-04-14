"""
Operations dashboard endpoints.

GET  /operations/summary       — high-level system health snapshot
GET  /operations/pipeline      — opportunity pipeline breakdown by status/band
GET  /operations/events        — recent audit events (optionally filtered by source)
GET  /operations/diagnostics   — explains exactly why execution is blocked/idle
POST /operations/run-quotas    — trigger weekly quota enforcement
POST /operations/run-decisions — re-run decision engine on all scored sources
POST /operations/run-pipeline  — re-run decisions + allocate + execute for ready sources
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.database.config import get_session
from app.models.event import OpportunityEvent
from app.models.income_source import IncomeSource, PriorityBand, SourceStatus
from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.strategy import Strategy, StrategyStatus
from app.services import strategies as strategy_svc
from app.services import alerts as alert_svc
from app.services import budget as budget_svc
from app.services import execution as execution_svc

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/summary")
def operations_summary(session: Session = Depends(get_session)):
    total_sources = session.exec(select(func.count(IncomeSource.id))).one()
    active_sources = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.status == SourceStatus.active)
    ).one()
    elite_count = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == PriorityBand.elite)
    ).one()
    high_count = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == PriorityBand.high)
    ).one()
    unack_alerts = len(alert_svc.get_active_alerts(session))
    ready_packets = session.exec(
        select(func.count(ActionPacket.id)).where(ActionPacket.status == PacketStatus.ready)
    ).one()
    active_executions = session.exec(
        select(func.count(ActionPacket.id)).where(
            ActionPacket.execution_state.in_([ExecutionState.active.value, ExecutionState.in_progress.value])
        )
    ).one()
    completed_executions = session.exec(
        select(func.count(ActionPacket.id)).where(ActionPacket.execution_state == ExecutionState.completed)
    ).one()
    failed_executions = session.exec(
        select(func.count(ActionPacket.id)).where(
            ActionPacket.execution_state.in_([ExecutionState.failed.value, ExecutionState.canceled.value])
        )
    ).one()
    underperforming_strategies = session.exec(
        select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.underperforming)
    ).one()
    strategy_quota = strategy_svc.check_quota(session)
    source_discovery_quota = strategy_svc.check_source_discovery_quota(session)
    open_budget = budget_svc.get_open_budget(session)

    all_quotas_met = strategy_quota["quota_met"] and source_discovery_quota["quota_met"]

    return {
        "total_opportunities": total_sources,
        "active_opportunities": active_sources,
        "elite_opportunities": elite_count,
        "high_opportunities": high_count,
        "unacknowledged_alerts": unack_alerts,
        "ready_packets": ready_packets,
        "execution": {
            "active": active_executions,
            "completed": completed_executions,
            "failed": failed_executions,
        },
        "underperforming_strategies": underperforming_strategies,
        "budget": (
            {
                "status": "open",
                "starting_bankroll": open_budget.starting_bankroll,
                "current_bankroll": budget_svc.recalc_current_bankroll(session, open_budget),
                "available_capital": budget_svc.recalc_remaining(session, open_budget),
                "committed_capital": budget_svc.recalc_committed_capital(session, open_budget),
                "realized_profit": budget_svc.recalc_realized_return(session, open_budget),
                "evaluation_end_date": open_budget.evaluation_end_date.isoformat(),
            }
            if open_budget
            else {"status": "no_active_bankroll"}
        ),
        "execution_provider": execution_svc.get_execution_provider_status(session),
        "weekly_quotas": {
            "all_met": all_quotas_met,
            "source_discovery": source_discovery_quota,
            "strategy_deployment": strategy_quota,
        },
    }


@router.get("/pipeline")
def pipeline_breakdown(session: Session = Depends(get_session)):
    """Count of opportunities by status and priority band."""
    # By status
    status_counts: dict[str, int] = {}
    for status in SourceStatus:
        count = session.exec(
            select(func.count(IncomeSource.id)).where(IncomeSource.status == status.value)
        ).one()
        if count:
            status_counts[status.value] = count

    # By band
    band_counts: dict[str, int] = {}
    for band in PriorityBand:
        count = session.exec(
            select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == band.value)
        ).one()
        if count:
            band_counts[band.value] = count

    # Top elite/high sources
    top_sources = session.exec(
        select(IncomeSource)
        .where(IncomeSource.score != None)  # noqa: E711
        .order_by(IncomeSource.score.desc())
        .limit(10)
    ).all()

    return {
        "by_status": status_counts,
        "by_band": band_counts,
        "top_10": [
            {
                "source_id": s.source_id,
                "description": s.description,
                "score": s.score,
                "priority_band": s.priority_band,
                "status": s.status,
                "estimated_profit": s.estimated_profit,
            }
            for s in top_sources
        ],
    }


@router.post("/run-quotas")
def run_quotas_now(session: Session = Depends(get_session)):
    """
    Manually trigger the full weekly quota enforcement cycle:
    source discovery check, strategy auto-promote, stale strategy detection.
    Equivalent to the quota step of the daily pipeline, callable on demand.
    """
    from app.services.scheduler import _run_weekly_quota_checks
    return _run_weekly_quota_checks(session)


@router.get("/events")
def recent_events(source_id: Optional[str] = None, limit: int = 100, session: Session = Depends(get_session)):
    stmt = select(OpportunityEvent).order_by(OpportunityEvent.created_at.desc()).limit(limit)
    if source_id:
        stmt = (
            select(OpportunityEvent)
            .where(OpportunityEvent.source_id == source_id)
            .order_by(OpportunityEvent.created_at.desc())
            .limit(limit)
        )
    events = list(session.exec(stmt).all())
    return {"count": len(events), "events": events}


@router.post("/run-decisions")
def run_decisions_now(session: Session = Depends(get_session)):
    """
    Re-run the decision engine on all scored opportunities using current thresholds.
    Updates execution_ready flags in-place.  Call this after deploying threshold
    changes (e.g. HUNTER_DECISION_AUTO_EXECUTE_SCORE) or after code fixes to
    decision.py so that existing DB rows reflect the new logic.
    """
    from app.services.decision import run_decisions
    return run_decisions(session)


@router.post("/run-pipeline")
def run_pipeline_now(session: Session = Depends(get_session)):
    """
    Flush the execution queue for all execution_ready trading decisions:
      1. Auto-allocate budget for any source that lacks a BudgetAllocation.
      2. Auto-place the Alpaca trade via auto_place_trade_for_source.

    Safe to call multiple times -- allocation and trade placement are both
    idempotent (existing allocations / open orders are detected and skipped).
    """
    from app.models.decision import OpportunityDecision
    from app.models.budget import BudgetAllocation
    from app.services.budget import auto_allocate_for_source
    from app.services.execution import auto_place_trade_for_source

    ready_decisions = session.exec(
        select(OpportunityDecision).where(
            OpportunityDecision.execution_ready == True,  # noqa: E712
            OpportunityDecision.execution_path == "trading",
        )
    ).all()

    results: dict = {
        "eligible": len(ready_decisions),
        "allocated": 0,
        "allocation_skipped": 0,
        "trades_placed": 0,
        "errors": [],
    }

    for dec in ready_decisions:
        existing_alloc = session.exec(
            select(BudgetAllocation).where(BudgetAllocation.source_id == dec.source_id)
        ).first()

        if not existing_alloc:
            try:
                alloc_result = auto_allocate_for_source(dec.source_id, session)
                if alloc_result and not alloc_result.get("skipped"):
                    results["allocated"] += 1
                else:
                    results["allocation_skipped"] += 1
            except Exception as exc:
                results["errors"].append(
                    {"source_id": dec.source_id, "step": "allocate", "error": str(exc)}
                )
                continue
        else:
            results["allocation_skipped"] += 1

        try:
            trade = auto_place_trade_for_source(dec.source_id, session)
            if trade:
                results["trades_placed"] += 1
        except Exception as exc:
            results["errors"].append(
                {"source_id": dec.source_id, "step": "trade", "error": str(exc)}
            )

    return results


@router.get("/diagnostics")
def execution_diagnostics(session: Session = Depends(get_session)):
    """
    Explains exactly why committed_capital, funded_packets, or realized_profit
    may be zero.  Reports active thresholds, decision state breakdown, score
    coverage vs thresholds, budget allocation gaps, and broker connection status.
    """
    from app.models.decision import OpportunityDecision
    from app.models.budget import BudgetAllocation
    from app.services.decision import _thresholds

    t = _thresholds()

    # -- Decision breakdown
    all_decisions = session.exec(select(OpportunityDecision)).all()
    by_state: dict[str, int] = {}
    by_path: dict[str, int] = {}
    for d in all_decisions:
        by_state[d.action_state] = by_state.get(d.action_state, 0) + 1
        by_path[d.execution_path] = by_path.get(d.execution_path, 0) + 1

    decision_summary = {
        "total": len(all_decisions),
        "execution_ready": sum(1 for d in all_decisions if d.execution_ready),
        "blocked_by_approval": sum(1 for d in all_decisions if d.blocked_by == "approval"),
        "blocked_by_action_state": sum(1 for d in all_decisions if d.blocked_by == "action_state"),
        "blocked_by_low_confidence": sum(1 for d in all_decisions if d.blocked_by == "low_confidence"),
        "by_action_state": by_state,
        "by_execution_path": by_path,
    }

    # -- Score coverage vs thresholds
    scored_sources = session.exec(
        select(IncomeSource).where(IncomeSource.score.is_not(None))
    ).all()
    trading_sources = [s for s in scored_sources if (s.category or "").lower() == "trading"]

    score_coverage = {
        "total_scored": len(scored_sources),
        "trading_total": len(trading_sources),
        "trading_above_auto_execute_score": sum(
            1 for s in trading_sources if (s.score or 0) >= t["auto_execute_score"]
        ),
        "trading_above_auto_execute_score_and_confidence": sum(
            1 for s in trading_sources
            if (s.score or 0) >= t["auto_execute_score"]
            and (s.confidence or 0) >= t["auto_execute_confidence"]
        ),
        "trading_meeting_all_auto_execute_criteria": sum(
            1 for s in trading_sources
            if (s.score or 0) >= t["auto_execute_score"]
            and (s.confidence or 0) >= t["auto_execute_confidence"]
            and (s.estimated_profit or 0) <= t["auto_execute_max_capital"]
        ),
    }

    # -- Allocation coverage
    total_allocations = session.exec(select(func.count(BudgetAllocation.id))).one()
    trading_source_ids = [s.source_id for s in trading_sources]
    trading_with_allocs = 0
    if trading_source_ids:
        trading_with_allocs = session.exec(
            select(func.count(BudgetAllocation.id)).where(
                BudgetAllocation.source_id.in_(trading_source_ids)
            )
        ).one()

    # -- Blocked trading decisions sample
    blocked_sample = [
        {
            "source_id": d.source_id,
            "action_state": d.action_state,
            "blocked_by": d.blocked_by,
            "approval_reason": d.approval_reason,
            "score_at_decision": d.score_at_decision,
            "confidence_at_decision": d.confidence_at_decision,
            "capital_recommendation": d.capital_recommendation,
        }
        for d in all_decisions
        if d.execution_path == "trading" and not d.execution_ready
    ][:20]

    # -- Plain-English diagnosis
    reasons: list[str] = []
    exec_ready = decision_summary["execution_ready"]
    if exec_ready == 0:
        if score_coverage["trading_meeting_all_auto_execute_criteria"] == 0:
            reasons.append(
                f"No trading sources meet all auto-execute criteria simultaneously "
                f"(score>={t['auto_execute_score']}, "
                f"confidence>={t['auto_execute_confidence']}, "
                f"estimated_profit<={t['auto_execute_max_capital']}). "
                f"Run POST /operations/run-decisions to re-evaluate with current thresholds."
            )
        elif decision_summary["blocked_by_approval"] > 0:
            reasons.append(
                f"{decision_summary['blocked_by_approval']} decision(s) blocked by approval gate -- "
                f"capital exceeds limit or commander review required."
            )
    else:
        reasons.append(f"{exec_ready} source(s) are execution_ready.")
        if trading_with_allocs == 0:
            reasons.append(
                "No budget allocations exist for trading sources despite execution_ready decisions. "
                "Run POST /operations/run-pipeline to allocate and execute."
            )

    broker = execution_svc.get_execution_provider_status(session)
    if not broker.get("connected"):
        reasons.append(
            f"Execution provider not connected: {broker.get('error', 'unknown')}. "
            "Check ALPACA_API_KEY / ALPACA_SECRET_KEY."
        )
    elif not broker.get("sync_success", True):
        reasons.append("Broker connected but last sync failed -- account state may be stale.")

    if not reasons:
        reasons.append("Execution pipeline appears healthy.")

    return {
        "thresholds": t,
        "decisions": decision_summary,
        "score_coverage": score_coverage,
        "allocations": {
            "total": total_allocations,
            "trading_sources": len(trading_sources),
            "trading_with_allocations": trading_with_allocs,
            "trading_without_allocations": len(trading_sources) - trading_with_allocs,
        },
        "broker": broker,
        "blocked_trading_decisions_sample": blocked_sample,
        "diagnosis": reasons,
    }
