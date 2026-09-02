"""
APScheduler-based background tasks for Hunter.

Jobs registered at startup:

  recycle_cycle_task  — runs every RECYCLE_CYCLE_INTERVAL_SECONDS (default 60s);
                       drives the INTRADAY_RECYCLE sell-first/buy-after loop.
                       Only active when STRATEGY_MODE=RECYCLE and ALPACA_ENABLED=True.

  discovery_scan_task — runs every DISCOVERY_SCAN_INTERVAL_SECONDS (default 3h);
                       lightweight discovery-only pass across all source
                       adapters (grants, marketplace deals, gigs, local
                       business leads, digital product gaps, GitHub bounties,
                       social listening, affiliate). Added 2026-09-02 because
                       daily_scan_task's once-per-24h cadence meant every
                       source only got one shot a day — narrow-window deals
                       and near-deadline grants could be missed entirely.

  signal_scan_task    — runs every SIGNAL_SCAN_INTERVAL_SECONDS (default 6h);
                       runs signal_engine.run_signal_scan() across ALL four
                       adapters — crypto (CoinGecko), congressional STOCK Act
                       filings, SEC Form 4, and executive-branch OGE 278T
                       (Trump/cabinet/VP disclosures). Every signal is logged
                       as a CopySignal record with a mirror/partial/watchlist/
                       reject routing decision for review. Unattended REAL
                       TRADE execution for VIP-watchlist matches
                       (_execute_vip_micro_invest — $15/trade, $75/day, no
                       review step, bypasses the budget ledger) stays gated
                       behind HUNTER_ENABLE_VIP_AUTO_INVEST (default False)
                       until there's an explicit decision to let Hunter place
                       real money on a name match with zero human review.

  daily_scan_task     — runs every 24 hours; full pipeline — daily advisor
                       opportunity, trading candidate generation, source
                       acquisition (redundant with discovery_scan_task but
                       harmless/deduped), and weekly quota enforcement.

  weekly_report_task  — runs every 7 days; queries all IncomeSource records,
                       sorts by score desc, and writes a JSON summary to
                       HUNTER_REPORTS_PATH. Also includes budget_commander_summary.

  morning_report_task — runs daily at HUNTER_MORNING_REPORT_HOUR:MINUTE
                       (default 7:00 ET). Hunter proactively builds and
                       submits its own condensed status brief (see
                       app.services.morning_brief) -- pushed via webhook to
                       AMETHYST_REPORT_WEBHOOK_URL if configured, otherwise
                       just logged and available at GET /reports/morning.
                       The point: the report is submitted on schedule
                       without anyone having to come ask for it.

All jobs open their own Session(engine) — they run outside the FastAPI
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
from app.services.trading_candidates import generate_trading_candidates
from app.services.budget import get_budget_commander_summary
from app.services import strategies as strategy_svc
from app.services import alerts as alert_svc
from app.services import reporting as reporting_svc
from app.config import RECYCLE_CYCLE_INTERVAL_SECONDS, STRATEGY_MODE, ALPACA_ENABLED
from app.services.policy_engine import run_policy_scan  # noqa: E402

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

    timing_report = reporting_svc.build_weekly_report(session)
    report = {
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
    report["position_timing"] = timing_report["timing"]
    report["fast_recycle_performance"] = timing_report["fast_recycle"]
    report["legacy_performance"] = timing_report["legacy"]
    report["open_position_snapshot"] = timing_report["open_position_snapshot"]
    return report


async def morning_report_task() -> None:
    """
    Hunter proactively submits its own morning status brief -- not
    waiting to be asked for it. Builds the condensed watcher digest (see
    app.services.morning_brief) and, if AMETHYST_REPORT_WEBHOOK_URL is
    configured, POSTs it there. If no webhook is configured, this just
    logs the summary line and overall_status -- it never fails the job
    or blocks anything else on delivery not being wired up yet.
    """
    logger.info("morning_report_task: starting")
    from app.database.config import engine as _engine
    from app.services.morning_brief import build_morning_brief
    from app.config import AMETHYST_REPORT_WEBHOOK_URL, AMETHYST_REPORT_WEBHOOK_TOKEN

    with Session(_engine) as session:
        try:
            brief = build_morning_brief(session)
        except Exception as exc:  # noqa: BLE001
            logger.error("morning_report_task: failed to build brief — %s", exc)
            return

    logger.info(
        "morning_report_task: brief built — status=%s | %s",
        brief["overall_status"], brief["summary"],
    )

    if not AMETHYST_REPORT_WEBHOOK_URL:
        logger.info("morning_report_task: no delivery webhook configured — brief available at GET /reports/morning only")
        return

    try:
        import httpx
        headers = {"Content-Type": "application/json"}
        if AMETHYST_REPORT_WEBHOOK_TOKEN:
            headers["Authorization"] = f"Bearer {AMETHYST_REPORT_WEBHOOK_TOKEN}"
        with httpx.Client(timeout=15) as client:
            resp = client.post(AMETHYST_REPORT_WEBHOOK_URL, json=brief, headers=headers)
        if resp.status_code >= 400:
            logger.warning("morning_report_task: delivery failed — HTTP %d: %s", resp.status_code, resp.text[:300])
        else:
            logger.info("morning_report_task: delivered to webhook — HTTP %d", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("morning_report_task: delivery exception — %s", exc)


async def signal_scan_task() -> None:
    """
    Runs the signal engine (crypto momentum + congressional/executive-branch
    disclosure monitoring). See module docstring for exactly what does and
    does not auto-execute.
    """
    logger.info("signal_scan_task: starting")
    from app.database.config import engine as _engine
    from app.services.signal_engine import run_signal_scan

    with Session(_engine) as session:
        try:
            result = run_signal_scan(session)
            logger.info(
                "signal_scan_task: complete — new=%d skipped=%d errors=%d",
                result.get("new", 0),
                result.get("skipped", 0),
                len(result.get("errors", [])),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("signal_scan_task: failed — %s", exc)


async def discovery_scan_task() -> None:
    """
    Lightweight, frequent discovery-only pass — separate from
    daily_scan_task's full pipeline (advisor opportunity, trading
    candidates, quota enforcement).

    daily_scan_task previously carried ALL opportunity discovery on a
    once-per-24-hours cron. That meant every source adapter (grants,
    marketplace deals, gigs, local business leads, etc.) only got a
    single chance per day to surface something — a discounted item with
    a narrow buying window, or a grant close to its deadline, could be
    missed entirely between runs. This job just re-runs source
    acquisition (all adapters, already parallelized and error-isolated
    in run_source_acquisition) on a much shorter interval. It does NOT
    duplicate the trading-candidate generation, daily advisor opportunity,
    or quota checks — those stay on daily_scan_task.
    """
    logger.info("discovery_scan_task: starting")
    from app.services.source_acquisition import run_source_acquisition

    with Session(engine) as session:
        try:
            result = run_source_acquisition(session)
            logger.info(
                "discovery_scan_task: complete — found=%d inserted=%d updated=%d skipped=%d errors=%d",
                result.get("found", 0),
                result.get("inserted", 0),
                result.get("updated", 0),
                result.get("skipped", 0),
                len(result.get("errors", [])),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("discovery_scan_task: failed — %s", exc)


async def daily_scan_task() -> None:
    """
    Full daily operations pipeline:
      0. Daily advisor opportunity (one advisor owns the day — generates direction)
      1. Generate fresh trading candidates → autotrader.json
      2. AutoTrader intake (ingest → score → orchestrate → alert → packet)
      3. Strategy quota check (auto-promote candidates, alert if shortfall)
    """
    logger.info("daily_scan_task: starting")

    # ── Step 0: Daily advisor opportunity ─────────────────────────────────────
    logger.info("daily_scan_task: [0/3] generating daily advisor opportunity")
    try:
        from app.services.daily_opportunity import generate_today_opportunity_and_sync, get_day_owner
        with Session(engine) as session:
            assigned = get_day_owner()
            opp = generate_today_opportunity_and_sync(session)
            logger.info(
                "daily_scan_task: daily opportunity — id=%d assigned=%s actual=%s lane=%s profit=$%.2f confidence=%.0f%%",
                opp.id, assigned, opp.actual_advisor, opp.lane,
                opp.expected_profit, opp.confidence * 100,
            )
    except Exception as exc:
        logger.warning("daily_scan_task: daily opportunity generation failed — %s", exc)

    # ── Step 1: Generate fresh trading candidates → autotrader.json ──────────
    logger.info("daily_scan_task: [0/2] generating trading candidates")
    n_candidates = generate_trading_candidates()
    logger.info("daily_scan_task: trading candidates generated — count=%d", n_candidates)

    # ── Step 1: AutoTrader intake ─────────────────────────────────────────────
    logger.info("daily_scan_task: [2/3] running AutoTrader intake")
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

        if result.inserted == 0 and result.source_mode == "live":
            logger.info("daily_scan_task: intake_dry — creation lane auto-triggered (see autotrader service log)")

    # ── Step 2: Weekly quota enforcement ─────────────────────────────────────
    logger.info("daily_scan_task: [3/3] enforcing weekly quotas")
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
    (_REPORTS_PATH / filename).write_text(
        __import__("json").dumps(report, indent=2, default=str)
    )
    logger.info("weekly_report_task: report written to %s", output_path)


async def recycle_cycle_task() -> None:
    """
    INTRADAY_RECYCLE cycle task — fires every RECYCLE_CYCLE_INTERVAL_SECONDS.

    Sequence (sell-first, buy-after):
      1. Cancel stale open buy orders
      2. Sync broker state
      3. Evaluate and execute exits (profit target / stop loss / max hold / EOD)
      4. Wait 2 s for fills to settle
      5. Re-sync broker state
      6. Evaluate and execute replacements (swap weaker position for stronger candidate)
      7. Evaluate and execute new entries (only if effective_buying_power >= MIN_REQUIRED_BUYING_POWER)
      8. Final sync

    Guarded by:
      - STRATEGY_MODE == "RECYCLE"
      - ALPACA_ENABLED == True
      - max_instances=1 on the APScheduler job (no concurrent runs)
    """
    if not ALPACA_ENABLED or STRATEGY_MODE != "RECYCLE":
        logger.debug("recycle_cycle_task: skipped (ALPACA_ENABLED=%s STRATEGY_MODE=%s)",
                     ALPACA_ENABLED, STRATEGY_MODE)
        return

    logger.info("recycle_cycle_task: starting cycle")
    from app.services.recycle_engine import run_recycle_cycle

    try:
        result = run_recycle_cycle()
        logger.info(
            "recycle_cycle_task: done — exits=%d/%d entries=%d/%d replacements=%d skipped=%s",
            result.exits_submitted, result.exits_submitted + result.exits_failed,
            result.entries_submitted, result.entries_submitted + result.entries_failed,
            result.replacements_triggered,
            result.cycle_skipped,
        )
        if result.errors:
            for err in result.errors:
                logger.error("recycle_cycle_task error: %s", err)
        if result.warnings:
            for w in result.warnings:
                logger.warning("recycle_cycle_task warning: %s", w)
    except Exception as exc:
        logger.exception("recycle_cycle_task: unhandled exception — %s", exc)


# ── Leon Daily Commerce Task ────────────────────────────────────────────────────────────
async def leon_daily_commerce_task():
    """
    Leon's daily autonomous commerce run.
    Runs every 24h at 8 AM. Responsibilities:
      1. Check deadline urgency — alert if list-by date within 3 days
      2. Auto-generate a seasonal product if no new product in 7 days
      3. Generate campaign briefs for any products missing one
      4. Notify SAPP Campaign Room of pending briefs
      5. Report summary to Hunter log
    """
    logger.info("Leon: Daily commerce run starting")
    try:
        from app.services.store_agent import DEADLINES, _days_until, get_store_dashboard
        from app.services.product_creation import create_product
        from app.services.campaign_agent import generate_campaign_brief, get_campaign_briefs
        from app.models.created_product import CreatedProduct
        from sqlmodel import Session, select
        from datetime import datetime, timezone, timedelta

        with Session(engine) as session:
            # 1. Check deadlines
            for dl in DEADLINES:
                days_to_list = _days_until(dl["list_by"])
                if 0 <= days_to_list <= 3:
                    logger.warning(
                        "Leon ⚨️ URGENT: '%s' list-by date in %d day(s). Get products live NOW.",
                        dl["name"], days_to_list
                    )

            # 2. Auto-generate if no new product in last 7 days
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            recent_products = session.exec(
                select(CreatedProduct).where(CreatedProduct.created_at >= week_ago)
            ).all()
            if not recent_products:
                logger.info("Leon: No new products in 7 days. Auto-generating one.")
                from app.services.store_agent import DEADLINES
                # Pick most urgent theme
                urgent_theme = None
                for dl in DEADLINES:
                    if _days_until(dl["event_date"]) <= 60 and dl["tags"]:
                        urgent_theme = dl["tags"][0]
                        break
                from app.services.store_agent import auto_generate_product
                pack = auto_generate_product(session, theme=urgent_theme, branded=False)
                logger.info("Leon: Auto-generated '%s'", pack.get("name", "unknown"))

            # 3. Generate briefs for products that don't have one
            from app.models.campaign_brief import CampaignBrief
            all_products = session.exec(select(CreatedProduct)).all()
            briefed_ids = set(
                b.product_id for b in session.exec(select(CampaignBrief)).all()
                if b.product_id is not None
            )
            unbriefed = [p for p in all_products if p.id not in briefed_ids]
            for product in unbriefed[:3]:  # max 3 per run to avoid spam
                try:
                    generate_campaign_brief(session, product)
                    logger.info("Leon: Brief generated for '%s'", product.name)
                except Exception as e:
                    logger.warning("Leon: Brief failed for '%s': %s", product.name, e)


            # Trends check
            try:
                from app.services.leon_trends import fetch_trend_signals
                trend_signals = fetch_trend_signals()
                for ts in trend_signals[:2]:  # max 2 trend products per run
                    from app.services.store_agent import auto_generate_product
                    result = auto_generate_product(session, theme=ts["topic"], branded=False)
                    logger.info("Leon Trends AUTO-CREATE: '%s' (momentum %.2f)", ts["topic"], ts["momentum"])
            except Exception as te:
                logger.warning("Leon Trends check failed: %s", te)
            logger.info(
                "Leon: Daily run complete. Products: %d, Unbriefed handled: %d",
                len(all_products), min(3, len(unbriefed))
            )
    except Exception as exc:
        logger.exception("Leon: Daily commerce task failed: %s", exc)



async def policy_scan_task() -> None:
    """
    Policy-to-Profit Engine daily scan.
    Runs at 06:30 ET — before markets open, so fresh intelligence is ready.
    Monitors all P2P sources, processes new events through LLM, and creates
    scored IncomeSource records for the MurphBoard dashboard.
    """
    logger.info("policy_scan_task: starting Policy-to-Profit scan")
    try:
        result = run_policy_scan()
        logger.info(
            "policy_scan_task: complete — fetched=%d new=%d opportunities=%d",
            result.get("total_events_fetched", 0),
            result.get("total_new_events", 0),
            result.get("total_opportunities_created", 0),
        )
    except Exception as exc:
        logger.exception("policy_scan_task: failed — %s", exc)


def build_weekly_report_now(*, session: Session | None = None) -> dict:
    """Synchronous helper — builds and persists the weekly report immediately.

    Called by the /reports/weekly endpoint for on-demand generation.
    Returns the report dict (same structure as weekly_report_task produces).
    """
    if session is None:
        with Session(engine) as owned_session:
            report = _build_weekly_report(owned_session)
    else:
        report = _build_weekly_report(session)

    _REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    import json
    filename = f"weekly_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    (_REPORTS_PATH / filename).write_text(json.dumps(report, indent=2, default=str))
    logger.info("build_weekly_report_now: report written to %s", filename)
    return report
