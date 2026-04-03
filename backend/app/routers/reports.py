"""
Reporting endpoints.

GET /reports/daily   — daily operational status (opportunities, capital, blockers, next actions)
GET /reports/weekly  — full weekly review (MurphBoard-ready)
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.config import (
    EXECUTION_MODE,
    SOURCES_WEEKLY_MINIMUM,
    STRATEGY_WEEKLY_MINIMUM,
    WEEKLY_BUDGET,
)
from app.database.config import get_session
from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.alert import Alert
from app.models.budget import BudgetAllocation, BudgetOutcome, WeeklyBudget
from app.models.income_source import IncomeSource, PriorityBand, SourceStatus
from app.models.strategy import Strategy, StrategyStatus
from app.services import budget as budget_svc
from app.services import strategies as strategy_svc
from app.services.autotrader import get_intake_state
from app.services.scheduler import build_weekly_report_now

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily")
def get_daily_report(session: Session = Depends(get_session)) -> dict:
    """
    Daily operational status report.

    Covers:
    - opportunities found today and in total
    - strategies active vs quota
    - capital status
    - active executions
    - unacknowledged alerts
    - blockers
    - next recommended actions
    """
    today = date.today()

    # Sources
    total_sources = session.exec(select(func.count(IncomeSource.id))).one()
    elite_sources = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == PriorityBand.elite)
    ).one()
    high_sources = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == PriorityBand.high)
    ).one()
    active_sources = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.status == SourceStatus.active)
    ).one()
    review_ready = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.status == SourceStatus.review_ready)
    ).one()

    # Strategies
    strategy_quota = strategy_svc.check_quota(session)
    source_discovery = strategy_svc.check_source_discovery_quota(session)
    active_strategies = session.exec(
        select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.active)
    ).one()
    underperforming = session.exec(
        select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.underperforming)
    ).one()

    # Capital
    open_budget = budget_svc.get_open_budget(session)
    capital = {}
    if open_budget:
        remaining = budget_svc.recalc_remaining(session, open_budget)
        committed = budget_svc.recalc_committed_capital(session, open_budget)
        realized = budget_svc.recalc_realized_return(session, open_budget)
        current_bankroll = budget_svc.recalc_current_bankroll(session, open_budget)
        capital = {
            "starting_bankroll": open_budget.starting_bankroll,
            "current_bankroll": round(current_bankroll, 2),
            "available_capital": round(remaining, 2),
            "committed_capital": round(committed, 2),
            "realized_profit": round(realized, 2),
            "roi_pct": round((current_bankroll - open_budget.starting_bankroll) / open_budget.starting_bankroll * 100, 2)
            if open_budget.starting_bankroll > 0
            else 0.0,
            "status": "open",
            "evaluation_end_date": open_budget.evaluation_end_date.isoformat(),
        }
    else:
        capital = {
            "starting_bankroll": WEEKLY_BUDGET,
            "current_bankroll": 0,
            "available_capital": 0,
            "committed_capital": 0,
            "realized_profit": 0,
            "roi_pct": 0.0,
            "status": "no_active_cycle",
            "evaluation_end_date": None,
        }

    # Executions
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
    ready_packets = session.exec(
        select(func.count(ActionPacket.id)).where(ActionPacket.status == PacketStatus.ready)
    ).one()

    # Alerts
    unacked_alerts = session.exec(
        select(func.count(Alert.id)).where(Alert.acknowledged == False)  # noqa: E712
    ).one()

    # AutoTrader
    at_state = get_intake_state()

    # Allocations needing approval
    approval_pending = session.exec(
        select(func.count(BudgetAllocation.id)).where(
            BudgetAllocation.approval_required == True,  # noqa: E712
            BudgetAllocation.approved_by_commander == False,  # noqa: E712
        )
    ).one()

    # Build blockers list
    blockers = []
    if not open_budget:
        blockers.append("No open capital cycle — POST /budget/open-week to initialize")
    if not strategy_quota["quota_met"]:
        blockers.append(
            f"Strategy quota shortfall: {strategy_quota['active_count']}/{STRATEGY_WEEKLY_MINIMUM} active. Run /operations/run-quotas"
        )
    if not source_discovery["quota_met"]:
        blockers.append(
            f"Source discovery shortfall: {source_discovery.get('sources_found_this_week', 0)}/{SOURCES_WEEKLY_MINIMUM} this week. Run intake."
        )
    if at_state.live_data_status != "ready":
        blockers.append("AutoTrader not connected. Set AUTOTRADER_SOURCE_TYPE and run intake.")
    if approval_pending:
        blockers.append(f"{approval_pending} allocation(s) waiting Commander approval — GET /budget/allocations")
    if underperforming:
        blockers.append(f"{underperforming} underperforming strategies need review or evidence logged.")

    # Build next actions
    next_actions = []
    if review_ready:
        next_actions.append(f"Review {review_ready} review-ready opportunities — GET /opportunities/ranked")
    if ready_packets:
        next_actions.append(f"Execute {ready_packets} ready packets — GET /packets/")
    if active_executions:
        next_actions.append(f"Monitor {active_executions} active execution(s) — GET /execution/status")
    if not strategy_quota["quota_met"]:
        next_actions.append("Activate strategy candidates to reach quota — POST /strategies/auto-promote")
    if not next_actions:
        next_actions.append("System is stable. Run POST /autotrader/run-intake to pull fresh opportunities.")

    return {
        "report_date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": EXECUTION_MODE,
        "opportunities": {
            "total": total_sources,
            "elite": elite_sources,
            "high": high_sources,
            "active": active_sources,
            "review_ready": review_ready,
        },
        "strategies": {
            "active": active_strategies,
            "required": STRATEGY_WEEKLY_MINIMUM,
            "quota_met": strategy_quota["quota_met"],
            "shortfall": strategy_quota.get("shortfall", 0),
            "underperforming": underperforming,
        },
        "capital": capital,
        "executions": {
            "active": active_executions,
            "completed": completed_executions,
            "failed": failed_executions,
            "ready_packets": ready_packets,
        },
        "alerts": {
            "unacknowledged": unacked_alerts,
            "approval_pending": approval_pending,
        },
        "autotrader": {
            "live_data_status": at_state.live_data_status,
            "current_data_mode": at_state.current_data_mode,
            "last_scan_at": at_state.last_scan_at.isoformat() if at_state.last_scan_at else None,
        },
        "blockers": blockers,
        "next_actions": next_actions,
    }


@router.get("/weekly")
def get_weekly_report() -> dict:
    """
    Full weekly MurphBoard review.

    Covers opportunities, strategies (≥10 check), capital performance,
    ROI, best/worst strategy, lessons learned placeholder, and next-week plan.
    """
    return build_weekly_report_now()
