"""
Centralised runtime settings for Hunter.
All values are read from environment variables. We load the canonical env files
here as well so provider settings are available even if app.config is imported
before database/config.py.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_PATHS = [
    BACKEND_ROOT.parent / "backend.env",
    BACKEND_ROOT / ".env",
    BACKEND_ROOT.parent / "config" / ".env",
]
ENV_FILE_VALUES = {
    str(path): (dotenv_values(path) if path.exists() else {})
    for path in ENV_FILE_PATHS
}


def _resolve_env_value(name: str, default: str = "", *, prefer_non_empty: bool = False) -> str:
    for path in ENV_FILE_PATHS:
        value = ENV_FILE_VALUES[str(path)].get(name)
        if value is None:
            continue
        text = str(value).strip()
        if prefer_non_empty and not text:
            continue
        return text
    runtime_value = os.getenv(name)
    if runtime_value is None:
        return default
    text = runtime_value.strip()
    if prefer_non_empty and not text:
        return default
    return text or default


def _effective_env_source(name: str, *, prefer_non_empty: bool = False) -> str | None:
    for path in ENV_FILE_PATHS:
        value = ENV_FILE_VALUES[str(path)].get(name)
        if value is None:
            continue
        text = str(value).strip()
        if prefer_non_empty and not text:
            continue
        return str(path)
    runtime_value = os.getenv(name)
    if runtime_value is not None:
        text = runtime_value.strip()
        if prefer_non_empty and not text:
            return None
        return "<process-env>"
    return None


def _masked_file_diagnostics(path: Path) -> dict:
    exists = path.exists()
    values = ENV_FILE_VALUES[str(path)] if exists else {}
    return {
        "path": str(path),
        "exists": exists,
        "loaded": load_dotenv(path) if exists else False,
        "alpaca_enabled_in_file": str(values.get("ALPACA_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"},
        "alpaca_paper_in_file": False,  # paper mode removed
        "execution_provider_in_file": (values.get("EXECUTION_PROVIDER") or "").strip().lower() or None,
        "alpaca_base_url_in_file": values.get("ALPACA_BASE_URL") or None,
        "api_key_present_in_file": bool(
            (values.get("LIVE_API_KEY") or values.get("SANDBOX_API_KEY") or values.get("ALPACA_API_KEY") or "").strip()
        ),
        "secret_key_present_in_file": bool(
            (values.get("LIVE_SECRET_KEY") or values.get("SANDBOX_SECRET_KEY") or values.get("ALPACA_SECRET_KEY") or "").strip()
        ),
    }


ENV_LOAD_DIAGNOSTICS = [_masked_file_diagnostics(path) for path in ENV_FILE_PATHS]


def _get_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _get_csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]

# ── Capital / bankroll ─────────────────────────────────────────────
WEEKLY_BUDGET: float = float(os.getenv("HUNTER_INITIAL_BANKROLL", os.getenv("HUNTER_WEEKLY_BUDGET", "100")))

# When True, POST /budget/allocate will be rejected if the amount would exceed
# remaining_budget.
BUDGET_STRICT_MODE: bool = os.getenv("HUNTER_BUDGET_STRICT_MODE", "true").lower() == "true"

# Allocations whose amount_allocated exceeds this threshold are automatically
# flagged approval_required=True.
APPROVAL_REQUIRED_OVER: float = float(os.getenv("HUNTER_APPROVAL_REQUIRED_OVER", "25"))
MAX_ALLOCATION_PER_OPPORTUNITY: float = float(
    os.getenv("HUNTER_MAX_ALLOCATION_PER_OPPORTUNITY", "25")
)

# Hunter's month-end stretch target is to double the bankroll.
FLIP_TARGET_MULTIPLIER: float = float(os.getenv("HUNTER_FLIP_TARGET_MULTIPLIER", "2.0"))

# ── AutoTrader integration ─────────────────────────────────────────────
# Required. Must be "file", "http", or "live".
# If not set, daily intake is aborted and logged as source_missing.
AUTOTRADER_SOURCE_TYPE: str = os.getenv("AUTOTRADER_SOURCE_TYPE", "")

# Required when AUTOTRADER_SOURCE_TYPE=file.
AUTOTRADER_FILE_PATH: str | None = os.getenv("AUTOTRADER_FILE_PATH")

# Required when AUTOTRADER_SOURCE_TYPE=http.
AUTOTRADER_HTTP_URL: str | None = os.getenv("AUTOTRADER_HTTP_URL")

# Optional: Bearer token for the http adapter.
AUTOTRADER_HTTP_API_KEY: str | None = os.getenv("AUTOTRADER_HTTP_API_KEY")

# ── Execution mode ─────────────────────────────────────────────
# Hunter is always in live mode. Real capital. Real trades.
# Paper/sandbox mode is not supported and has been removed.
EXECUTION_MODE: str = _resolve_env_value("EXECUTION_MODE", "live").lower()

# ── Alpaca credentials ─────────────────────────────────────────────
# Canonical names: LIVE_API_KEY / LIVE_SECRET_KEY
# Legacy fallback: SANDBOX_API_KEY / SANDBOX_SECRET_KEY (these env var names
#   now hold live credentials; kept for backward compatibility with existing
#   Render secrets that pre-date this rename)
LIVE_API_KEY: str = _resolve_env_value("LIVE_API_KEY", "", prefer_non_empty=True)
LIVE_SECRET_KEY: str = _resolve_env_value("LIVE_SECRET_KEY", "", prefer_non_empty=True)
LIVE_BASE_URL: str = _resolve_env_value("LIVE_BASE_URL", "https://api.alpaca.markets")
LIVE_ACCOUNT_ID: str = _resolve_env_value("LIVE_ACCOUNT_ID", "")

# Legacy names retained so existing Render secrets continue to work without
# requiring a dashboard rename. Resolution order: LIVE_* → SANDBOX_* → ALPACA_*
SANDBOX_API_KEY: str = _resolve_env_value("SANDBOX_API_KEY", "", prefer_non_empty=True)
SANDBOX_SECRET_KEY: str = _resolve_env_value("SANDBOX_SECRET_KEY", "", prefer_non_empty=True)
SANDBOX_BASE_URL: str = "https://api.alpaca.markets"  # always live endpoint
SANDBOX_ACCOUNT_ID: str = _resolve_env_value("SANDBOX_ACCOUNT_ID", "")

# ── Active brokerage credentials (resolved, live-only) ───────────────────
EXECUTION_PROVIDER: str = _resolve_env_value("EXECUTION_PROVIDER", "alpaca").lower()
ALPACA_ENABLED: bool = _resolve_env_value("ALPACA_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

# Resolution order: LIVE_API_KEY → SANDBOX_API_KEY (legacy) → ALPACA_API_KEY
ALPACA_API_KEY: str = (
    LIVE_API_KEY
    or SANDBOX_API_KEY
    or _resolve_env_value("ALPACA_API_KEY", "", prefer_non_empty=True)
)
ALPACA_SECRET_KEY: str = (
    LIVE_SECRET_KEY
    or SANDBOX_SECRET_KEY
    or _resolve_env_value("ALPACA_SECRET_KEY", "", prefer_non_empty=True)
)

# Paper mode is permanently disabled. Hunter trades with real capital only.
ALPACA_PAPER: bool = False
ALPACA_BASE_URL: str = _resolve_env_value("ALPACA_BASE_URL", "https://api.alpaca.markets")

ALPACA_EFFECTIVE_SOURCES = {
    "execution_mode": EXECUTION_MODE,
    "execution_provider": _effective_env_source("EXECUTION_PROVIDER"),
    "alpaca_enabled": _effective_env_source("ALPACA_ENABLED"),
    "alpaca_paper": "always_false",
    "alpaca_base_url": "live_endpoint",
    "alpaca_api_key": (
        _effective_env_source("LIVE_API_KEY", prefer_non_empty=True)
        or _effective_env_source("SANDBOX_API_KEY", prefer_non_empty=True)
        or _effective_env_source("ALPACA_API_KEY", prefer_non_empty=True)
    ),
    "alpaca_secret_key": (
        _effective_env_source("LIVE_SECRET_KEY", prefer_non_empty=True)
        or _effective_env_source("SANDBOX_SECRET_KEY", prefer_non_empty=True)
        or _effective_env_source("ALPACA_SECRET_KEY", prefer_non_empty=True)
    ),
}

# ── Lead intelligence ─────────────────────────────────────────────
APOLLO_API_KEY: str = os.getenv("APOLLO_API_KEY", "")
APOLLO_BASE_URL: str = os.getenv("APOLLO_BASE_URL", "https://api.apollo.io/v1")
COMMONROOM_API_KEY: str = os.getenv("COMMONROOM_API_KEY", "")
COMMONROOM_BASE_URL: str = os.getenv("COMMONROOM_BASE_URL", "https://api.commonroom.io/community/v1")

# ── Advisor API keys ─────────────────────────────────────────────
VENICE_API_KEY: str = os.getenv("VENICE_API_KEY", "")
VENICE_API_URL: str = os.getenv("VENICE_API_URL", "https://api.venice.ai/api/v1")
VENICE_MODEL: str = os.getenv("VENICE_MODEL", "llama-3.3-70b")

DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL: str = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

GROK_API_KEY: str = os.getenv("GROK_API_KEY", "")
GROK_API_URL: str = os.getenv("GROK_API_URL", "https://api.x.ai/v1")
GROK_MODEL: str = os.getenv("GROK_MODEL", "grok-3")

# ── Weekly quotas — non-negotiable ─────────────────────────────────
SOURCES_WEEKLY_MINIMUM: int = int(os.getenv("HUNTER_SOURCES_WEEKLY_MINIMUM", "10"))
STRATEGY_WEEKLY_MINIMUM: int = int(os.getenv("HUNTER_STRATEGY_WEEKLY_MINIMUM", "10"))
STRATEGY_STALE_DAYS: int = int(os.getenv("HUNTER_STRATEGY_STALE_DAYS", "2"))

# -- Source acquisition ------------------------------------------------------
SOURCES_MAX_RESULTS_PER_RUN: int = int(os.getenv("HUNTER_SOURCES_MAX_RESULTS_PER_RUN", "30"))
SOURCE_REQUEST_TIMEOUT_SECONDS: int = int(
    os.getenv("HUNTER_SOURCE_REQUEST_TIMEOUT_SECONDS", "12")
)
SOURCES_USER_AGENT: str = os.getenv(
    "HUNTER_SOURCES_USER_AGENT",
    "HunterSourceAcquisition/0.3 (+https://localhost/hunter)",
)

SOURCES_SOCIAL_ENABLED: bool = _get_bool("HUNTER_SOURCES_SOCIAL_ENABLED", True)
SOURCES_SOCIAL_MAX_RESULTS: int = int(os.getenv("HUNTER_SOURCES_SOCIAL_MAX_RESULTS", "12"))
SOURCES_SOCIAL_QUERIES: list[str] = _get_csv(
    "HUNTER_SOURCES_SOCIAL_QUERIES",
    "need help,looking for,recommendation for,frustrated with,anyone know",
)
SOURCES_SOCIAL_REDDIT_SUBREDDITS: list[str] = _get_csv(
    "HUNTER_SOURCES_SOCIAL_REDDIT_SUBREDDITS",
    "smallbusiness,entrepreneur,freelance,marketing,shopify,webdev",
)

SOURCES_GIG_ENABLED: bool = _get_bool("HUNTER_SOURCES_GIG_ENABLED", True)
SOURCES_GIG_MAX_RESULTS: int = int(os.getenv("HUNTER_SOURCES_GIG_MAX_RESULTS", "10"))
SOURCES_GIG_QUERIES: list[str] = _get_csv(
    "HUNTER_SOURCES_GIG_QUERIES",
    "automation,lead generation,marketing,shopify,web scraping,research",
)

SOURCES_GITHUB_ENABLED: bool = _get_bool("HUNTER_SOURCES_GITHUB_ENABLED", True)
SOURCES_GITHUB_MAX_RESULTS: int = int(os.getenv("HUNTER_SOURCES_GITHUB_MAX_RESULTS", "12"))
SOURCES_GITHUB_REPO_QUERIES: list[str] = _get_csv(
    "HUNTER_SOURCES_GITHUB_REPO_QUERIES",
    "automation agent stars:>20,small business automation stars:>5,ai workflow stars:>20,integration tool stars:>20",
)
SOURCES_GITHUB_ISSUE_QUERIES: list[str] = _get_csv(
    "HUNTER_SOURCES_GITHUB_ISSUE_QUERIES",
    '"need help" automation state:open,"setup help" integration state:open,"feature request" agent state:open,"how do i" workflow state:open',
)

SOURCES_MARKETPLACE_ENABLED: bool = _get_bool("HUNTER_SOURCES_MARKETPLACE_ENABLED", True)
SOURCES_MARKETPLACE_MAX_RESULTS: int = int(
    os.getenv("HUNTER_SOURCES_MARKETPLACE_MAX_RESULTS", "10")
)
SOURCES_MARKETPLACE_QUERIES: list[str] = _get_csv(
    "HUNTER_SOURCES_MARKETPLACE_QUERIES",
    "dyson,lego,makita,canon,nintendo,ipad",
)

SOURCES_LOCAL_ENABLED: bool = _get_bool("HUNTER_SOURCES_LOCAL_ENABLED", True)
SOURCES_LOCAL_MAX_RESULTS: int = int(os.getenv("HUNTER_SOURCES_LOCAL_MAX_RESULTS", "12"))
SOURCES_LOCAL_BBOX: str = os.getenv(
    "HUNTER_SOURCES_LOCAL_BBOX",
    "40.7128,-74.0060,40.7528,-73.9660",
)
SOURCES_LOCAL_BUSINESS_TYPES: list[str] = _get_csv(
    "HUNTER_SOURCES_LOCAL_BUSINESS_TYPES",
    "dentist,clinic,church,music_school",
)

SOURCES_DIGITAL_ENABLED: bool = _get_bool("HUNTER_SOURCES_DIGITAL_ENABLED", True)
SOURCES_DIGITAL_MAX_RESULTS: int = int(os.getenv("HUNTER_SOURCES_DIGITAL_MAX_RESULTS", "10"))
SOURCES_DIGITAL_QUERIES: list[str] = _get_csv(
    "HUNTER_SOURCES_DIGITAL_QUERIES",
    "dashboard template,spreadsheet template,prompt pack,church website template,artist press kit,patient intake form",
)

SOURCES_RFP_ENABLED: bool = _get_bool("HUNTER_SOURCES_RFP_ENABLED", True)
SOURCES_AFFILIATE_ENABLED: bool = _get_bool("HUNTER_SOURCES_AFFILIATE_ENABLED", True)

# ── Facebook Marketplace compliant execution lane ─────────────────────────
MARKETPLACE_FB_LANE_ENABLED: bool = _get_bool("MARKETPLACE_FB_LANE_ENABLED", False)
MARKETPLACE_FB_PROVIDER: str = os.getenv("MARKETPLACE_FB_PROVIDER", "manual").lower()
API2CART_API_KEY: str = os.getenv("API2CART_API_KEY", "")
API2CART_BASE_URL: str = os.getenv("API2CART_BASE_URL", "https://app.api2cart.com/v1.1")
AUTODS_API_KEY: str = os.getenv("AUTODS_API_KEY", "")
AUTODS_PARTNER_TOKEN: str = os.getenv("AUTODS_PARTNER_TOKEN", "")
AUTODS_BASE_URL: str = os.getenv("AUTODS_BASE_URL", "https://api.autods.com/v2")
MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED: bool = _get_bool(
    "MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED", False
)
MARKETPLACE_FB_RATE_LIMIT_PER_HOUR: int = int(
    os.getenv("MARKETPLACE_FB_RATE_LIMIT_PER_HOUR", "5")
)

# ── Email notifications (SMTP) ───────────────────────────────────────────
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "Hunter")
COMMANDER_EMAIL: str = os.getenv("COMMANDER_EMAIL", "beautillion1@aol.com")

# ── SMS notifications (Twilio) ───────────────────────────────────────────
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "+13502250005")
COMMANDER_PHONE: str = os.getenv("COMMANDER_PHONE", "+14782319790")

# ── Operating account (Robins Financial checking) ─────────────────────────
HUNTER_OPERATING_ACCOUNT_PROVIDER: str = os.getenv(
    "HUNTER_OPERATING_ACCOUNT_PROVIDER", "robins_financial"
)

# ── Live Execution Strategy — INTRADAY_RECYCLE ───────────────────────────
STRATEGY_MODE: str = os.getenv("STRATEGY_MODE", "RECYCLE").upper()
LIVE_EXECUTION_STRATEGY: str = os.getenv(
    "LIVE_EXECUTION_STRATEGY", "INTRADAY_RECYCLE"
).upper()
LIVE_EXECUTION_PROFILE: str = os.getenv(
    "LIVE_EXECUTION_PROFILE", "FAST_RECYCLE"
).upper()
USE_ONLY_FAST_RECYCLE_BUCKET: bool = _get_bool("USE_ONLY_FAST_RECYCLE_BUCKET", True)
FAST_RECYCLE_TRANCHE: float = float(os.getenv("FAST_RECYCLE_TRANCHE", "50"))
FAST_RECYCLE_MAX_HOLD_MINUTES: int = int(
    os.getenv("FAST_RECYCLE_MAX_HOLD_MINUTES", "45")
)
FAST_RECYCLE_MAX_POSITION_DOLLARS: float = float(
    os.getenv("FAST_RECYCLE_MAX_POSITION_DOLLARS", "15.0")
)
FAST_RECYCLE_MAX_OPEN_POSITIONS: int = int(
    os.getenv("FAST_RECYCLE_MAX_OPEN_POSITIONS", "3")
)

# ── Position sizing ────────────────────────────────────────────────
MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
MAX_POSITION_DOLLARS: float = float(os.getenv("MAX_POSITION_DOLLARS", "30.0"))
MAX_POSITION_PCT_OF_BANKROLL: float = float(
    os.getenv("MAX_POSITION_PCT_OF_BANKROLL", "0.30")
)

# ── Capital safety thresholds ─────────────────────────────────────────────
MIN_REQUIRED_BUYING_POWER: float = float(os.getenv("MIN_REQUIRED_BUYING_POWER", "5.0"))
CAPITAL_RESERVE_BUFFER: float = float(os.getenv("CAPITAL_RESERVE_BUFFER", "2.0"))

# ── Exit rules ─────────────────────────────────────────────────────────────
TARGET_PROFIT_PCT: float = float(os.getenv("TARGET_PROFIT_PCT", "0.02"))   # 2 %
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "-0.015"))         # -1.5 %
MAX_HOLD_MINUTES: int = int(os.getenv("MAX_HOLD_MINUTES", "240"))          # 4 h
FORCE_SELL_END_OF_DAY: bool = _get_bool("FORCE_SELL_END_OF_DAY", True)
ALLOW_OVERNIGHT_HOLD: bool = _get_bool("ALLOW_OVERNIGHT_HOLD", False)
RECYCLE_EOD_FLATTEN_MINUTES_BEFORE: int = int(
    os.getenv("RECYCLE_EOD_FLATTEN_MINUTES_BEFORE", "15")
)

# ── Replacement / ranking ─────────────────────────────────────────────────
REPLACEMENT_RANK_THRESHOLD: float = float(os.getenv("REPLACEMENT_RANK_THRESHOLD", "0.15"))
PYRAMIDING_ENABLED: bool = _get_bool("PYRAMIDING_ENABLED", False)

# ── Recycle cycle interval ────────────────────────────────────────────────
RECYCLE_CYCLE_INTERVAL_SECONDS: int = int(os.getenv("RECYCLE_CYCLE_INTERVAL_SECONDS", "60"))

# ── Stale order management ────────────────────────────────────────────────
STALE_ORDER_TIMEOUT_SECONDS: int = int(os.getenv("STALE_ORDER_TIMEOUT_SECONDS", "120"))
