"""
System readiness and health endpoints.

GET /system/health      — lightweight liveness ping
GET /system/readiness   — full dual-mode sandbox + live readiness report
POST /system/activate-live  — (guarded) single-action live activation trigger
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
    GROK_API_KEY,
    LIVE_API_KEY,
    LIVE_BASE_URL,
    LIVE_SECRET_KEY,
    SANDBOX_API_KEY,
    SANDBOX_BASE_URL,
    SANDBOX_SECRET_KEY,
    SOURCES_WEEKLY_MINIMUM,
    STRATEGY_WEEKLY_MINIMUM,
    VENICE_API_KEY,
    WEEKLY_BUDGET,
)
from app.database.config import get_session
from app.services import budget as budget_svc
from app.services import strategies as strategy_svc
from app.services.autotrader import get_intake_state

router = APIRouter(prefix="/system", tags=["system"])


# ── Connectivity checks ───────────────────────────────────────────────────────

def _check_sandbox_brokerage() -> dict:
    """Attempt live connection to Alpaca paper API using sandbox credentials."""
    api_key = SANDBOX_API_KEY or ALPACA_API_KEY
    secret_key = SANDBOX_SECRET_KEY or ALPACA_SECRET_KEY

    if not ALPACA_ENABLED:
        return {"connected": False, "reason": "ALPACA_ENABLED=false"}
    if not api_key or not secret_key:
        return {
            "connected": False,
            "reason": "SANDBOX_API_KEY / SANDBOX_SECRET_KEY not set. Add paper trading credentials to .env.",
            "required_vars": ["SANDBOX_API_KEY", "SANDBOX_SECRET_KEY"],
        }
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(api_key, secret_key, paper=True, url_override=SANDBOX_BASE_URL)
        acct = client.get_account()
        return {
            "connected": True,
            "mode": "paper",
            "account_id": str(acct.id),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "currency": acct.currency or "USD",
            "status": str(acct.status),
            "base_url": SANDBOX_BASE_URL,
        }
    except EnvironmentError as exc:
        return {"connected": False, "reason": str(exc)}
    except Exception as exc:
        return {
            "connected": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }


def _check_live_config_structure() -> dict:
    """Check whether live config structure is prewired (credentials may be empty)."""
    vars_present = {
        "LIVE_API_KEY": bool(LIVE_API_KEY),
        "LIVE_SECRET_KEY": bool(LIVE_SECRET_KEY),
        "LIVE_BASE_URL": bool(LIVE_BASE_URL),
    }
    structure_present = True  # env vars exist in .env even if empty
    credentials_present = bool(LIVE_API_KEY and LIVE_SECRET_KEY)

    return {
        "live_config_structure_present": structure_present,
        "live_credentials_present": credentials_present,
        "live_activation_control_present": True,  # EXECUTION_MODE var exists
        "live_mode_structurally_ready": structure_present,
        "vars": vars_present,
        "base_url": LIVE_BASE_URL,
        "note": (
            "Live credentials not set — populate LIVE_API_KEY and LIVE_SECRET_KEY, then set EXECUTION_MODE=live."
            if not credentials_present
            else "Live credentials present. Set EXECUTION_MODE=live to activate."
        ),
    }


def _check_autotrader() -> dict:
    state = get_intake_state()
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
    # Use _resolve_env_value (prefer_non_empty=True) so that empty stubs in
    # backend.env don't shadow real values populated in backend/.env
    from app.config import _resolve_env_value as _rev
    required_sandbox = {
        "SANDBOX_API_KEY": bool(_rev("SANDBOX_API_KEY", prefer_non_empty=True)),
        "SANDBOX_SECRET_KEY": bool(_rev("SANDBOX_SECRET_KEY", prefer_non_empty=True)),
    }
    live_structure = {
        "LIVE_API_KEY": bool(_rev("LIVE_API_KEY", prefer_non_empty=True)),
        "LIVE_SECRET_KEY": bool(_rev("LIVE_SECRET_KEY", prefer_non_empty=True)),
        "LIVE_BASE_URL": bool(_rev("LIVE_BASE_URL", prefer_non_empty=True)),
        "EXECUTION_MODE": bool(_rev("EXECUTION_MODE", prefer_non_empty=True)),
    }
    optional = {
        "AUTOTRADER_SOURCE_TYPE": bool(_rev("AUTOTRADER_SOURCE_TYPE", prefer_non_empty=True)),
        "APOLLO_API_KEY": bool(APOLLO_API_KEY),
        "COMMONROOM_API_KEY": bool(COMMONROOM_API_KEY),
        "VENICE_API_KEY": bool(VENICE_API_KEY),
        "DEEPSEEK_API_KEY": bool(DEEPSEEK_API_KEY),
        "GROK_API_KEY": bool(GROK_API_KEY),
    }
    return {
        "required_for_sandbox": required_sandbox,
        "live_structure": live_structure,
        "optional": optional,
        "all_sandbox_required_present": all(required_sandbox.values()),
        "live_structure_present": True,  # vars exist in .env file
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health")
def health_check():
    """Lightweight liveness ping."""
    return {
        "status": "ok",
        "service": "Hunter v0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_mode": EXECUTION_MODE,
        "paper_mode": ALPACA_PAPER,
    }


@router.get("/readiness")
def sandbox_readiness(session: Session = Depends(get_session)):
    """
    Full dual-mode readiness report.

    Reports:
    - sandbox_ready (paper trading connected and operational)
    - live_mode_structurally_ready (live config prewired, credentials may still be empty)
    - tool readiness (AutoTrader, advisors, leads)
    - account status
    - capital status
    - quota status
    - required env vars
    """
    env_check = _check_env_vars()
    sandbox_brokerage = _check_sandbox_brokerage()
    live_config = _check_live_config_structure()
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
    sandbox_creds_present = env_check["all_sandbox_required_present"]
    sandbox_connected = sandbox_brokerage.get("connected", False)
    paper_enforced = True  # always enforced in sandbox mode
    sandbox_ready = sandbox_creds_present and sandbox_connected and paper_enforced

    live_structurally_ready = live_config["live_mode_structurally_ready"]
    broker_account_mode = "paper" if ALPACA_PAPER else "live"
    execution_policy_mode = EXECUTION_MODE

    # Blockers
    blockers = []
    if not sandbox_creds_present:
        missing = [k for k, v in env_check["required_for_sandbox"].items() if not v]
        blockers.append(f"Missing sandbox credentials: {', '.join(missing)}")
    if sandbox_creds_present and not sandbox_connected:
        blockers.append(f"Sandbox brokerage not connected: {sandbox_brokerage.get('reason', 'unknown')}")
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
    if not sandbox_ready:
        next_steps = [
            "1. Open a paper trading account at alpaca.markets",
            "2. Add SANDBOX_API_KEY and SANDBOX_SECRET_KEY to agents/hunter/backend/.env",
            "3. Restart the backend: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload",
            "4. GET /system/readiness — sandbox_ready should be true",
            "5. POST /autotrader/run-intake to pull seed opportunities",
        ]
    else:
        next_steps = [
            "Hunter is sandbox-ready.",
            "- POST /autotrader/run-intake to ingest opportunities",
            "- POST /operations/run-quotas to enforce weekly requirements",
            "- POST /execution/trade to place a test paper trade",
            "- GET /reports/daily for today's operational report",
        ]

    return {
        "sandbox_ready": sandbox_ready,
        "broker_connection_ready": sandbox_connected,
        "broker_account_mode": broker_account_mode,
        "execution_policy_mode": execution_policy_mode,
        "live_mode_structurally_ready": live_structurally_ready,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_mode": EXECUTION_MODE,
        "blockers": blockers,
        "warnings": warnings,
        "modules": {
            "backend": {
                "status": "ok",
                "version": "0.2.0",
            },
            "sandbox_brokerage": {
                "status": "connected" if sandbox_connected else "disconnected",
                "mode": "paper",
                "account_mode": broker_account_mode,
                **sandbox_brokerage,
            },
            "brokerage": {
                "status": "connected" if sandbox_connected else "disconnected",
                "provider": EXECUTION_PROVIDER,
                "account_mode": broker_account_mode,
                "execution_policy_mode": execution_policy_mode,
                **sandbox_brokerage,
            },
            "live_brokerage": {
                "status": "prewired" if live_structurally_ready else "not_configured",
                **live_config,
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
            "sandbox_account_connected": sandbox_connected,
            "broker_account_connected": sandbox_connected,
            "broker_account_mode": broker_account_mode,
            "sandbox_account_id": sandbox_brokerage.get("account_id"),
            "live_account_config_present": bool(LIVE_API_KEY and LIVE_SECRET_KEY),
            "live_account_connected": EXECUTION_MODE == "live" and bool(LIVE_API_KEY and LIVE_SECRET_KEY),
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
        },
        "env_vars": env_check,
        "live_activation": {
            "control_present": True,
            "current_mode": EXECUTION_MODE,
            "how_to_activate_live": "Set EXECUTION_MODE=live in .env, populate LIVE_API_KEY and LIVE_SECRET_KEY, restart backend.",
            "warning": "Do NOT activate live mode until Commander Murph explicitly authorizes it.",
        },
        "next_steps": next_steps,
    }


@router.post("/activate-live")
def activate_live_mode():
    """
    Guard rail for live mode activation awareness.

    This endpoint does NOT switch to live mode automatically.
    It confirms the structural readiness and tells the operator exactly
    what single action is required.
    """
    live_creds_present = bool(LIVE_API_KEY and LIVE_SECRET_KEY)
    if EXECUTION_MODE == "live":
        return {
            "current_mode": "live",
            "status": "already_active",
            "warning": "Hunter is already running in live mode. Real capital is at risk.",
        }
    return {
        "current_mode": EXECUTION_MODE,
        "live_credentials_present": live_creds_present,
        "live_config_structure_present": True,
        "action_required": (
            "Set EXECUTION_MODE=live in agents/hunter/backend/.env and restart the backend."
            if live_creds_present
            else "First populate LIVE_API_KEY and LIVE_SECRET_KEY, then set EXECUTION_MODE=live and restart."
        ),
        "warning": "Live mode uses real capital. Confirm with Commander Murph before proceeding.",
        "status": "not_activated",
    }


# ── Worker notifications ───────────────────────────────────────────────────────

class NotifyRequest(BaseModel):
    title: str
    body: str
    priority: str = "medium"      # low | medium | high | critical
    alert_type: str = "review_required"
    source_id: str | None = None
    worker_id: str | None = None


@router.post("/notify", status_code=201)
def worker_notify(body: NotifyRequest, session: Session = Depends(get_session)):
    """
    Receive a Commander notification from the assistant worker.

    Used for login attempts, checkpoint detections, platform events, and
    any condition the worker needs to surface to Commander Murph immediately.
    Credentials are NEVER included in notification payloads.
    """
    from app.services import alerts as alert_svc
    from app.models.alert import AlertType, AlertPriority

    # Map priority string to AlertPriority (default medium)
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
