"""
Morning brief — a condensed "is Hunter operating correctly today" digest,
distinct from /reports/daily (which is a full operational dump aimed at
someone actively working the queue).

This exists so Hunter can PROACTIVELY submit a status report every
morning without being asked -- the point being a watcher shouldn't have
to come chase Hunter for it, the way a report is supposed to already be
on your supervisor's desk before they ask where it is.

Delivery is a webhook POST to AMETHYST_REPORT_WEBHOOK_URL if configured
(see morning_report_task in scheduler.py). If unset, the brief is still
built and available via GET /reports/morning so it can be reviewed or
tested before any receiving endpoint exists -- it never errors out or
blocks anything just because delivery isn't configured yet.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, func, select

from app.config import EXECUTION_MODE, STRATEGY_WEEKLY_MINIMUM
from app.models.alert import Alert, AlertPriority
from app.models.copy_signal import CopySignal
from app.services import budget as budget_svc
from app.services import strategies as strategy_svc
from app.services.autotrader import get_intake_state
from app.services.source_acquisition import get_source_status

# Thresholds for overall_status classification. Deliberately conservative
# (biased toward flagging "needs_attention" over "healthy") since the whole
# point of a watcher report is to surface backlog, not hide it.
_UNACKED_ALERTS_NEEDS_ATTENTION = 20
_UNACKED_ALERTS_CRITICAL = 200


def build_morning_brief(session: Session) -> dict[str, Any]:
    today = date.today()

    # ── Capital ──────────────────────────────────────────────────────────
    open_budget = budget_svc.get_open_budget(session)
    capital_mismatch = False
    if open_budget:
        capital_state = budget_svc.get_broker_reconciled_capital_state(session)
        capital = {
            "available": capital_state["available_capital"],
            "current_bankroll": capital_state["current_bankroll"],
            "roi_pct": round(
                (capital_state["current_bankroll"] - open_budget.starting_bankroll)
                / open_budget.starting_bankroll * 100, 2
            ) if open_budget.starting_bankroll else 0.0,
        }
        capital_mismatch = bool(capital_state.get("mismatch_detected"))
    else:
        capital = {"available": 0.0, "current_bankroll": 0.0, "roi_pct": 0.0}

    # ── Opportunities ────────────────────────────────────────────────────
    from app.models.income_source import IncomeSource, PriorityBand
    total_opps = session.exec(select(func.count(IncomeSource.id))).one()
    elite_opps = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == PriorityBand.elite)
    ).one()
    high_opps = session.exec(
        select(func.count(IncomeSource.id)).where(IncomeSource.priority_band == PriorityBand.high)
    ).one()

    # ── Discovery source health ──────────────────────────────────────────
    source_status = get_source_status()
    source_health = source_status.get("sources", {})
    ok_sources = sum(1 for s in source_health.values() if s.get("live"))
    degraded_sources = [name for name, s in source_health.items() if not s.get("live") and s.get("enabled")]

    # ── Signal engine (crypto + disclosure monitoring) ──────────────────
    vip_pending = session.exec(
        select(func.count(CopySignal.id)).where(CopySignal.decision == "pending_approval")
    ).one()
    signals_total = session.exec(select(func.count(CopySignal.id))).one()

    # ── Strategy quota ───────────────────────────────────────────────────
    strategy_quota = strategy_svc.check_quota(session)

    # ── Alerts ───────────────────────────────────────────────────────────
    unacked_alerts = session.exec(
        select(func.count(Alert.id)).where(Alert.acknowledged == False)  # noqa: E712
    ).one()
    high_priority_unacked = session.exec(
        select(func.count(Alert.id)).where(
            Alert.acknowledged == False,  # noqa: E712
            Alert.priority.in_([AlertPriority.high, AlertPriority.critical]),
        )
    ).one()

    # ── Trading connectivity ────────────────────────────────────────────
    at_state = get_intake_state()

    # ── Overall status classification ───────────────────────────────────
    reasons: list[str] = []
    status = "healthy"

    if capital_mismatch:
        status = "critical"
        reasons.append("Capital ledger mismatch detected against broker")
    if at_state.live_data_status != "ready":
        status = "critical" if status != "critical" else status
        reasons.append("AutoTrader/broker data not confirmed live")
    if unacked_alerts >= _UNACKED_ALERTS_CRITICAL:
        status = "critical"
        reasons.append(f"{unacked_alerts} unacknowledged alerts (backlog)")
    elif unacked_alerts >= _UNACKED_ALERTS_NEEDS_ATTENTION and status == "healthy":
        status = "needs_attention"
        reasons.append(f"{unacked_alerts} unacknowledged alerts")

    if vip_pending and status == "healthy":
        status = "needs_attention"
    if vip_pending:
        reasons.append(f"{vip_pending} VIP signal(s) awaiting approval")

    if degraded_sources and status == "healthy":
        status = "needs_attention"
    if degraded_sources:
        reasons.append(f"{len(degraded_sources)} discovery source(s) degraded/blocked: {', '.join(degraded_sources[:4])}")

    if not strategy_quota["quota_met"] and status == "healthy":
        status = "needs_attention"
    if not strategy_quota["quota_met"]:
        reasons.append(
            f"Strategy quota shortfall: {strategy_quota['active_count']}/{STRATEGY_WEEKLY_MINIMUM} active"
        )

    if not reasons:
        summary = f"Hunter is healthy. {high_opps + elite_opps} high-value opportunities open, ${capital['available']:,.2f} available capital."
    else:
        lead_in = "URGENT" if status == "critical" else "Hunter needs attention"
        summary = f"{lead_in}: {reasons[0]}" + (f" (+{len(reasons)-1} more)" if len(reasons) > 1 else "")

    return {
        "report_type": "morning_brief",
        "report_date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "hunter",
        "execution_mode": EXECUTION_MODE,
        "overall_status": status,
        "summary": summary,
        "reasons": reasons,
        "capital": capital,
        "opportunities": {"total": total_opps, "elite": elite_opps, "high": high_opps},
        "discovery_sources": {
            "total": len(source_health),
            "live": ok_sources,
            "degraded_or_blocked": degraded_sources,
        },
        "signal_engine": {
            "total_signals": signals_total,
            "vip_pending_approval": vip_pending,
        },
        "strategies": {
            "active": strategy_quota["active_count"],
            "required": STRATEGY_WEEKLY_MINIMUM,
            "quota_met": strategy_quota["quota_met"],
        },
        "alerts": {
            "unacknowledged": unacked_alerts,
            "high_priority_unacknowledged": high_priority_unacked,
        },
        "trading": {
            "live_data_status": at_state.live_data_status,
            "current_data_mode": at_state.current_data_mode,
        },
    }
