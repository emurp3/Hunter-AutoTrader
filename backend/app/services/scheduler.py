"""
APScheduler-based background tasks for Hunter.

Two jobs are registered at startup:

  daily_scan_task    — runs every 24 hours; calls the AutoTrader intake pipeline
                       and logs scan / inserted / updated / skipped / error counts.

  weekly_report_task — runs every 7 days; queries all IncomeSource records,
                       sorts by score desc, and writes a JSON summary to
                       HUNTER_REPORTS_PATH. Also includes budget_commander_summary.

Both jobs open their own Session(engine) — they run outside the FastAPI
request lifecycle so FastAPI's Depends() is not available.
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app.database.config import engine
from app.models.income_source import IncomeSource
from app.models.strategy import Strategy, StrategyStatus
from app.services.autotrader import run_intake
from app.services.budget import get_budget_commander_summary
from app.services import strategies as strategy_svc
from app.services import alerts as alert_svc

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

_REPORTS_PATH = Path(os.getenv("HUNTER_REPORTS_PATH", "./outputs/reports"))


def _run_weekly_quota_checks(session: Session) -> dict:
    """
    Enforce all hard weekly requirements:
      1. Source discovery quota  — ≥ HUNTER_SOURCES_WEEKLY_MINIMUM new sources this week
      2. Strategy quota          — ≥ HUNTER_STRATEGY_WEEKLY_MINIMUM active strategies
      3. Strategy activity audit — flag stale active strategies as underperforming
    Raises alerts for every violation.
    """
    from app.config import STRATEGY_WEEKLY_MINIMUM, SOURCES_WEEKLY_MINIMUM, STRATEGY_STALE_DAYS

    # 1. Source discovery quota
    discovery = strategy_svc.check_source_discovery_quota(session, minimum=SOURCES_WEEKLY_MINIMUM)
    if not discovery["quota_met"]:
        alert_svc.raise_source_discovery_shortfall_alert(
            discovery["sources_found_this_week"],
            discovery["required"],
            discovery["week_start"],
            session,
        )

    # 2. Strategy quota — auto-promote then re-check
    promoted = strategy_svc.auto_promote_candidates(session, minimum=STRATEGY_WEEKLY_MINIMUM)
    strategy_quota = strategy_svc.check_quota(session, minimum=STRATEGY_WEEKLY_MINIMUM)
    if not strategy_quota["quota_met"]:
        alert_svc.raise_strategy_shortfall_alert(
            strategy_quota["active_count"], strategy_quota["required"], session
        )

    # 3. Stale strategy detection — active with no evidence_of_activity
    stale = strategy_svc.flag_stale_active_strategies(session, stale_after_days=STRATEGY_STALE_DAYS)
    for s in stale:
        days_active = (date.today() - s.date_activated).days
        alert_svc.raise_strategy_stale_alert(s.strategy_id, s.strategy_name, days_active, session)

    return {
        "source_discovery": discovery,
        "strategy_quota": strategy_quota,
        "strategies_promoted": len(promoted),
        "strategies_flagged_stale": len(stale),
    }


def _build_weekly_report(session: Session) -> dict:
    sources = session.exec(
        select(IncomeSource).order_by(IncomeSource.score.desc())
    ).all()

    total_profit = sum(s.estimated_profit for s in sources)
    by_status: dict[str, list] = {}
    for s in sources:
        status_key = s.status.value if hasattr(s.status, 'value') else str(s.status)
        by_status.setdefault(status_key, []).append(
            {
                "source_id": s.source_id,
                "description": s.description,
                "estimated_profit": s.estimated_profit,
                "currency": s.currency,
                "score": s.score,
                "origin_module": s.origin_module,
                "category": s.category,
                "confidence": s.confidence,
                "next_action": s.next_action,
            }
        )

    from app.config import SOURCES_WEEKLY_MINIMUM, STRATEGY_WEEKLY_MINIMUM
    strategy_status = strategy_svc.get_weekly_status(session)
    discovery_quota = strategy_svc.check_source_discovery_quota(session, minimum=SOURCES_WEEKLY_MINIMUM)
    strategy_quota = strategy_svc.check_quota(session, minimum=STRATEGY_WEEKLY_MINIMUM)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sources": len(sources),
        "total_estimated_monthly_profit": round(total_profit, 2),
        "top_10_by_score": [
            {
                "source_id": s.source_id,
                "description": s.description,
                "score": s.score,
                "priority_band": s.priority_band,
                "origin_module": s.origin_module,
            }
            for s in sources[:10]
        ],
        "by_status": by_status,
        "budget_commander_summary": get_budget_commander_summary(session),
        "strategy_weekly_status": strategy_status,
        "weekly_quotas": {
            "all_met": discovery_quota["quota_met"] and strategy_quota["quota_met"],
            "source_discovery": discovery_quota,
            "strategy_deployment": strategy_quota,
        },
    }


async def daily_scan_task() -> None:
    """
    Full daily operations pipeline:
      1. AutoTrader intake (ingest → score → orchestrate → alert → packet)
      2. Strategy quota check (auto-promote candidates, alert if shortfall)
    """
    logger.info("daily_scan_task: starting")

    # ── Step 1: AutoTrader intake ─────────────────────────────────────────────
    logger.info("daily_scan_task: [1/2] running AutoTrader intake")
    with Session(engine) as session:
        result = run_intake(session)

    if result.aborted:
        logger.error(
            "daily_scan_task: intake aborted — reason=%s details=%s",
            result.abort_reason,
            result.error_details,
        )
    else:
        logger.info(
            "daily_scan_task: intake complete — scanned=%d inserted=%d updated=%d skipped=%d errors=%d",
            result.scanned,
            result.inserted,
            result.updated,
            result.skipped,
            result.errors,
        )
        for detail in result.error_details:
            logger.warning("daily_scan_task: finding error — %s", detail)

    # ── Step 2: Weekly quota enforcement ─────────────────────────────────────
    logger.info("daily_scan_task: [2/2] enforcing weekly quotas")
    with Session(engine) as session:
        quota_result = _run_weekly_quota_checks(session)

    disc = quota_result["source_discovery"]
    strat = quota_result["strategy_quota"]
    logger.info(
        "daily_scan_task: source discovery — found=%d required=%d quota_met=%s",
        disc["sources_found_this_week"], disc["required"], disc["quota_met"],
    )
    logger.info(
        "daily_scan_task: strategy quota — active=%d required=%d promoted=%d stale_flagged=%d quota_met=%s",
        strat["active_count"], strat["required"],
        quota_result["strategies_promoted"], quota_result["strategies_flagged_stale"],
        strat["quota_met"],
    )
    if not disc["quota_met"]:
        logger.warning(
            "daily_scan_task: SOURCE DISCOVERY SHORTFALL — %d/%d sources this week",
            disc["sources_found_this_week"], disc["required"],
        )
    if not strat["quota_met"]:
        logger.warning(
            "daily_scan_task: STRATEGY QUOTA SHORTFALL — %d/%d active strategies",
            strat["active_count"], strat["required"],
        )

    logger.info("daily_scan_task: complete")


async def weekly_report_task() -> None:
    """Generate and persist the weekly report to HUNTER_REPORTS_PATH."""
    logger.info("weekly_report_task: generating report")
    with Session(engine) as session:
        report = _build_weekly_report(session)

    _REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    filename = f"weekly_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    output_path = _REPORTS_PATH / filename
    output_path.write_text(json.dumps(report, indent=2))
    logger.info("weekly_report_task: report written to %s", output_path)


def build_weekly_report_now() -> dict:
    """Synchronous helper used by the /reports/weekly endpoint."""
    with Session(engine) as session:
        return _build_weekly_report(session)
