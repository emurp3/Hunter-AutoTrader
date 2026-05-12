"""
System readiness and health endpoints.

GET /system/health      — lightweight liveness ping
GET /system/readiness   — full live brokerage readiness report
POST /system/activate-live  — status endpoint (Hunter is already live)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.config import (
    ALPACA_API_KEY,
    ALPACA_BASE_URL,
    ALPACA_ENABLED,
    ALPACA_PAPER,
    ALPACA_SECRET_KEY,
    APOLLO_API_KEY,
    AUTOTRADER_FILE_PATH,
    AUTOTRADER_HTTP_URL,
    AUTOTRADER_SOURCE_TYPE,
    COMMONROOM_API_KEY,
    DEEPSEEK_API_KEY,
    EXECUTION_MODE,
    EXECUTION_PROVIDER,
    FAST_RECYCLE_TRANCHE,
    LIVE_EXECUTION_PROFILE,
    GROK_API_KEY,
    LIVE_API_KEY,
    LIVE_BASE_URL,
    LIVE_SECRET_KEY,
    SANDBOX_API_KEY,
    SANDBOX_SECRET_KEY,
    SOURCES_WEEKLY_MINIMUM,
    STRATEGY_WEEKLY_MINIMUM,
    USE_ONLY_FAST_RECYCLE_BUCKET,
    VENICE_API_KEY,
    WEEKLY_BUDGET,
)
from app.database.config import get_session
from app.services import budget as budget_svc
from app.services import strategies as strategy_svc
from app.services.autotrader import refresh_intake_state

router = APIRouter(prefix="/system", tags=["system"])


# ── Connectivity checks ────────────────────────────────────────────────

def _check_live_brokerage() -> dict:
    """Attempt connection to Alpaca live API."""
    api_key = ALPACA_API_KEY
    secret_key = ALPACA_SECRET_KEY

    if not ALPACA_ENABLED:
        return {"connected": False, "reason": "ALPACA_ENABLED=false"}
    if not api_key or not secret_key:
        return {
            "connected": False,
            "reason": (
                "Alpaca credentials not set. Add LIVE_API_KEY and LIVE_SECRET_KEY "
                "(or SANDBOX_API_KEY / SANDBOX_SECRET_KEY as legacy names) to the Render dashboard."
            ),
            "required_vars": ["LIVE_API_KEY", "LIVE_SECRET_KEY"],
        }
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(api_key, secret_key, paper=False, url_override=ALPACA_BASE_URL)
        acct = client.get_account()
        return {
            "connected": True,
            "mode": "live",
            "account_id": str(acct.id),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "currency": acct.currency or "USD",
            "status": str(acct.status),
            "base_url": ALPACA_BASE_URL,
        }
    except EnvironmentError as exc:
        return {"connected": False, "reason": str(exc)}
    except Exception as exc:
        return {
            "connected": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }


def _check_autotrader() -> dict:
    state = refresh_intake_state()
    return {
        "configured": state.source_configured,
        "source_type": AUTOTRADER_SOURCE_TYPE or None,
        "reachable": state.source_reachable,
        "live_data_status": state.live_data_status,
        "current_data_mode": state.current_data_mode,
        "last_scan_at": state.last_scan_at.isoformat() if state.last_scan_at else None,
        "last_status": state.last_status,
        "using_fallback": state.using_fallback,
        "note": (
            "Not configured. Set AUTOTRADER_SOURCE_TYPE=file and AUTOTRADER_FILE_PATH."
            if not state.source_configured
            else None
        ),
    }


def _check_advisors() -> dict:
    present = {
        "venice": bool(VENICE_API_KEY),
        "deepseek": bool(DEEPSEEK_API_KEY),
        "grok": bool(GROK_API_KEY),
    }
    configured = [k for k, v in present.items() if v]
    missing = [k for k, v in present.items() if not v]
    return {
        "configured": configured,
        "missing": missing,
        "any_configured": bool(configured),
    }


def _check_leads() -> dict:
    return {
        "apollo": {
            "configured": bool(APOLLO_API_KEY),
            "note": None if APOLLO_API_KEY else "Set APOLLO_API_KEY in .env",
        },
        "commonroom": {
            "configured": bool(COMMONROOM_API_KEY),
            "note": None if COMMONROOM_API_KEY else "Set COMMONROOM_API_KEY in .env",
        },
        "any_configured": bool(APOLLO_API_KEY or COMMONROOM_API_KEY),
    }


def _check_env_vars() -> dict:
    from app.config import _resolve_env_value as _rev
    live_creds = {
        "LIVE_API_KEY": bool(_rev("LIVE_API_KEY", prefer_non_empty=True)),
        "LIVE_SECRET_KEY": bool(_rev("LIVE_SECRET_KEY", prefer_non_empty=True)),
        # Legacy names that may hold live credentials
        "SANDBOX_API_KEY": bool(_rev("SANDBOX_API_KEY", prefer_non_empty=True)),
        "SANDBOX_SECRET_KEY": bool(_rev("SANDBOX_SECRET_KEY", prefer_non_empty=True)),
    }
    # Credentials are present if either canonical or legacy names are set
    creds_present = (
        (live_creds["LIVE_API_KEY"] and live_creds["LIVE_SECRET_KEY"])
        or (live_creds["SANDBOX_API_KEY"] and live_creds["SANDBOX_SECRET_KEY"])
    )
    optional = {
        "AUTOTRADER_SOURCE_TYPE": bool(_rev("AUTOTRADER_SOURCE_TYPE", prefer_non_empty=True)),
        "APOLLO_API_KEY": bool(APOLLO_API_KEY),
        "COMMONROOM_API_KEY": bool(COMMONROOM_API_KEY),
        "VENICE_API_KEY": bool(VENICE_API_KEY),
        "DEEPSEEK_API_KEY": bool(DEEPSEEK_API_KEY),
        "GROK_API_KEY": bool(GROK_API_KEY),
    }
    return {
        "live_credentials": live_creds,
        "live_credentials_present": creds_present,
        "optional": optional,
        "alpaca_base_url": ALPACA_BASE_URL,
        "execution_mode": EXECUTION_MODE,
    }


# ── Endpoints ───────────────────────────────────────────────────────────

@router.get("/health")
def health_check():
    """Lightweight liveness ping."""
    return {
        "status": "ok",
        "service": "Hunter v0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_mode": EXECUTION_MODE,
        "brokerage_mode": "live",
    }


@router.get("/readiness")
def readiness(session: Session = Depends(get_session)):
    """
    Full live brokerage readiness report.

    Reports:
    - brokerage_ready (live Alpaca connected and operational)
    - tool readiness (AutoTrader, advisors, leads)
    - account status and capital
    - quota status
    - required env vars
    """
    env_check = _check_env_vars()
    live_brokerage = _check_live_brokerage()
    autotrader = _check_autotrader()
    advisors = _check_advisors()
    leads = _check_leads()

    # Budget
    open_budget = budget_svc.get_open_budget(session)
    if open_budget:
        remaining = budget_svc.recalc_remaining(session, open_budget)
        current = budget_svc.recalc_current_bankroll(session, open_budget)
        budget_status = {
            "open": True,
            "starting_bankroll": open_budget.starting_bankroll,
            "current_bankroll": round(current, 2),
            "available_capital": round(remaining, 2),
            "evaluation_end_date": open_budget.evaluation_end_date.isoformat(),
        }
    else:
        budget_status = {
            "open": False,
            "note": "No open capital cycle. POST /budget/open-week to initialize.",
        }

    # Quotas
    strategy_quota = strategy_svc.check_quota(session)
    source_quota = strategy_svc.check_source_discovery_quota(session)

    # Readiness conditions
    brokerage_connected = live_brokerage.get("connected", False)
    brokerage_ready = brokerage_connected and env_check["live_credentials_present"]

    # Blockers
    blockers = []
    if not env_check["live_credentials_present"]:
        blockers.append(
            "Missing Alpaca credentials: set LIVE_API_KEY + LIVE_SECRET_KEY "
            "(or SANDBOX_API_KEY + SANDBOX_SECRET_KEY) in Render dashboard"
        )
    if env_check["live_credentials_present"] and not brokerage_connected:
        blockers.append(f"Brokerage not connected: {live_brokerage.get('reason', 'unknown')}")
    if not open_budget:
        blockers.append("No open capital cycle — POST /budget/open-week")

    # Warnings
    warnings = []
    if not autotrader["configured"]:
        warnings.append("AutoTrader not configured — intake disabled until AUTOTRADER_SOURCE_TYPE is set")
    if not advisors["any_configured"]:
        warnings.append("No advisor keys configured — consensus layer inactive")
    if not leads["any_configured"]:
        warnings.append("No lead intelligence keys configured — Apollo/CommonRoom inactive")
    if not strategy_quota["quota_met"]:
        warnings.append(f"Strategy quota shortfall: {strategy_quota['active_count']}/{STRATEGY_WEEKLY_MINIMUM}")
    if not source_quota["quota_met"]:
        warnings.append(f"Source discovery shortfall: {source_quota.get('sources_found_this_week', 0)}/{SOURCES_WEEKLY_MINIMUM}")

    # Next steps
    if not brokerage_ready:
        next_steps = [
            "1. Add LIVE_API_KEY and LIVE_SECRET_KEY to Render dashboard (main hunter service)",
            "2. Trigger a Manual Deploy on the hunter service",
            "3. GET /system/readiness — brokerage_ready should be true",
            "4. POST /autotrader/run-intake to pull opportunities",
        ]
    else:
        next_steps = [
            "Hunter is live and operational.",
            "- POST /autotrader/run-intake to ingest opportunities",
            "- POST /operations/run-quotas to enforce weekly requirements",
            "- GET /reports/daily for today's operational report",
        ]

    return {
        "brokerage_ready": brokerage_ready,
        "broker_connection_ready": brokerage_connected,
        "broker_account_mode": "live",
        "execution_policy_mode": EXECUTION_MODE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_mode": EXECUTION_MODE,
        "blockers": blockers,
        "warnings": warnings,
        "modules": {
            "backend": {
                "status": "ok",
                "version": "0.2.0",
            },
            "brokerage": {
                "status": "connected" if brokerage_connected else "disconnected",
                "mode": "live",
                "provider": EXECUTION_PROVIDER,
                "account_mode": "live",
                "execution_policy_mode": EXECUTION_MODE,
                **live_brokerage,
            },
            "autotrader": {
                "status": "ready" if autotrader.get("live_data_status") == "ready" else "offline",
                **autotrader,
            },
            "advisors": {
                "status": "partial" if advisors["any_configured"] else "inactive",
                **advisors,
            },
            "leads": {
                "status": "partial" if leads["any_configured"] else "inactive",
                **leads,
            },
            "budget": {
                "status": "open" if open_budget else "no_active_cycle",
                **budget_status,
            },
        },
        "accounts": {
            "broker_account_connected": brokerage_connected,
            "broker_account_mode": "live",
            "broker_account_id": live_brokerage.get("account_id"),
            "live_credentials_present": env_check["live_credentials_present"],
        },
        "quotas": {
            "strategy_deployment": strategy_quota,
            "source_discovery": source_quota,
            "all_met": strategy_quota["quota_met"] and source_quota["quota_met"],
        },
        "capital": {
            "starting_bankroll": open_budget.starting_bankroll if open_budget else WEEKLY_BUDGET,
            "available": budget_status.get("available_capital", 0),
            "status": "open" if open_budget else "no_cycle",
            "fast_recycle_tranche": FAST_RECYCLE_TRANCHE,
            "live_execution_profile": LIVE_EXECUTION_PROFILE,
            "use_only_fast_recycle_bucket": USE_ONLY_FAST_RECYCLE_BUCKET,
        },
        "env_vars": env_check,
        "next_steps": next_steps,
    }


@router.post("/activate-live")
def activate_live_mode():
    """
    Live mode status endpoint.
    Hunter is always in live mode. Real capital. Real trades.
    """
    return {
        "current_mode": "live",
        "status": "active",
        "brokerage_mode": "live",
        "paper_mode": False,
        "message": "Hunter is running in live mode. Real capital is deployed.",
    }


# ── Worker notifications ────────────────────────────────────────────────

class NotifyRequest(BaseModel):
    title: str
    body: str
    priority: str = "medium"
    alert_type: str = "review_required"
    source_id: str | None = None
    worker_id: str | None = None


@router.post("/notify", status_code=201)
def worker_notify(body: NotifyRequest, session: Session = Depends(get_session)):
    """
    Receive a Commander notification from the assistant worker.
    Credentials are NEVER included in notification payloads.
    """
    from app.services import alerts as alert_svc
    from app.models.alert import AlertType, AlertPriority

    priority_map = {
        "low": AlertPriority.low,
        "medium": AlertPriority.medium,
        "high": AlertPriority.high,
        "critical": AlertPriority.critical,
    }
    priority = priority_map.get(body.priority, AlertPriority.medium)

    alert = alert_svc.raise_alert(
        alert_type=body.alert_type,
        title=body.title,
        body=body.body,
        session=session,
        priority=priority,
        source_id=body.source_id,
    )
    return {
        "status": "notified",
        "alert_id": alert.id,
        "title": body.title,
        "priority": priority,
    }
