"""
Operations dashboard endpoints.

GET /operations/summary   — high-level system health snapshot
GET /operations/pipeline  — opportunity pipeline breakdown by status/band
GET /operations/events    — recent audit events (optionally filtered by source)
"""

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
