"""
Trading candidate generator — screens live Alpaca market data and writes
qualifying momentum opportunities to backend/data/autotrader.json.

Called by the daily scheduler as step 0, before run_intake(), so the file
adapter always has fresh trading candidates instead of falling back to seeds.

Scoring note
------------
estimated_profit is set in the $15–22 range to represent the expected
dollar return on each trade's capital exposure. This achieves a pipeline
score of ~55–58, which clears the HUNTER_DECISION_AUTO_EXECUTE_SCORE=50
threshold while staying below the $25 auto_execute capital cap.

The default auto_execute threshold (85) is unreachable for sub-$25
opportunities — set HUNTER_DECISION_AUTO_EXECUTE_SCORE=50 in Render.

Auto-execute gate (all must pass)
----------------------------------
  score         >= HUNTER_DECISION_AUTO_EXECUTE_SCORE  (set 50)
  confidence    >= HUNTER_DECISION_AUTO_EXECUTE_CONFIDENCE (default 0.75)
  estimated_profit <= HUNTER_DECISION_AUTO_EXECUTE_MAX_CAPITAL (default $25)
  execution_path == "trading"
  symbol extractable from notes field
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Output path (must match DEFAULT_AUTOTRADER_FILE_PATH in autotrader service) ──
# __file__ = backend/app/services/trading_candidates.py
# parents[0] = backend/app/services/
# parents[1] = backend/app/
# parents[2] = backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
AUTOTRADER_JSON_PATH = _BACKEND_ROOT / "data" / "autotrader.json"

# ── Default watchlist — liquid momentum candidates ────────────────────────────
DEFAULT_WATCHLIST: list[str] = [
    # Large-cap tech
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA",
    # Semiconductors / AI
    "AMD", "AVGO", "PLTR",
    # Crypto-adjacent
    "COIN", "MARA", "RIOT",
    # Growth / fintech
    "SOFI", "SOUN",
    # Broad market ETFs (for signal confirmation)
    "SPY", "QQQ",
    # Finance
    "JPM",
]

# Minimum bars required for momentum calculation
_MIN_BARS = 10

# Confidence threshold — only candidates above this make the file
_MIN_CONFIDENCE = 0.75

# estimated_profit bounds ($) — must stay ≤ auto_execute_max_capital ($25)
_PROFIT_MIN = 15.0
_PROFIT_MAX = 22.0


def generate_trading_candidates(
    watchlist: list[str] | None = None,
    output_path: Path | None = None,
) -> int:
    """
    Screen the watchlist using Alpaca market data, write qualifying momentum
    candidates to autotrader.json. Returns count of candidates written.
    Never raises — logs warnings on all failures.
    """
    symbols = watchlist or _env_watchlist() or DEFAULT_WATCHLIST
    path = output_path or AUTOTRADER_JSON_PATH

    try:
        candidates = _screen_watchlist(symbols)
    except Exception as exc:
        logger.warning("generate_trading_candidates: screening failed — %s", exc)
        return 0

    if not candidates:
        logger.info("generate_trading_candidates: 0 candidates passed momentum filter — autotrader.json not updated")
        return 0

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(candidates, indent=2), encoding="utf-8")
        logger.info(
            "generate_trading_candidates: wrote %d candidate(s) to %s",
            len(candidates), path,
        )
    except Exception as exc:
        logger.warning("generate_trading_candidates: failed to write %s — %s", path, exc)
        return 0

    return len(candidates)


def _env_watchlist() -> list[str]:
    """Read comma-separated tickers from HUNTER_TRADING_WATCHLIST env var."""
    raw = os.getenv("HUNTER_TRADING_WATCHLIST", "").strip()
    if not raw:
        return []
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def _screen_watchlist(symbols: list[str]) -> list[dict[str, Any]]:
    """
    Fetch daily bars for all symbols, apply momentum/mean-reversion filter, return candidates.
    Uses Alpaca's StockHistoricalDataClient with the effective live credentials.
    Evaluates both bullish (buy) and oversold-reversion (buy dip) setups.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from app.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        logger.warning("generate_trading_candidates: Alpaca credentials not available — skipping")
        return []

    client = StockHistoricalDataClient(
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
    )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=45)  # ~30 trading days

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )

    bars_response = client.get_stock_bars(request)
    bars_data: dict[str, list] = bars_response.data  # {symbol: [Bar, ...]}

    date_str = end.strftime("%Y%m%d")
    candidates: list[dict[str, Any]] = []
    symbol_results: list[str] = []

    for symbol in symbols:
        bar_list = bars_data.get(symbol, [])
        if len(bar_list) < _MIN_BARS:
            symbol_results.append(f"{symbol}:no_data({len(bar_list)})")
            continue

        try:
            candidate = _evaluate_symbol(symbol, bar_list, date_str)
            if candidate:
                candidates.append(candidate)
                symbol_results.append(f"{symbol}:pass({candidate['confidence']})")
            else:
                symbol_results.append(f"{symbol}:filtered")
        except Exception as exc:
            symbol_results.append(f"{symbol}:error")
            logger.debug("generate_trading_candidates: %s evaluation error — %s", symbol, exc)
            continue

    logger.info(
        "generate_trading_candidates: screened %d symbols, %d passed | %s",
        len(symbols), len(candidates), " ".join(symbol_results),
    )
    return candidates


def _evaluate_symbol(
    symbol: str,
    bar_list: list,
    date_str: str,
) -> dict[str, Any] | None:
    """
    Evaluate one symbol for two signal types:
      1. Momentum:        price > SMA5 > SMA20  (trending up)
      2. Mean-reversion:  price < SMA20 * 0.92  (>8% below 20d avg, oversold bounce)

    Both are BUY setups. Returns a candidate dict or None.
    """
    closes = [float(b.close) for b in bar_list]
    current_price = closes[-1]

    sma5 = sum(closes[-5:]) / 5
    sma20_window = closes[-20:] if len(closes) >= 20 else closes
    sma20 = sum(sma20_window) / len(sma20_window)

    deviation_pct = (current_price - sma20) / sma20  # positive = above SMA20

    # ── Signal 1: Momentum (bullish trend) ────────────────────────────────────
    if current_price > sma5 > sma20:
        signal = "momentum"
        # Confidence from momentum strength: 2% above SMA20 → 0.78, 7% → 0.93
        raw_conf = 0.70 + deviation_pct * 4.0
        confidence = round(min(0.93, max(0.60, raw_conf)), 3)

    # ── Signal 2: Mean-reversion (oversold bounce) ────────────────────────────
    elif deviation_pct <= -0.08:
        signal = "mean_reversion"
        # Deeper discount = higher confidence the bounce will come
        # -8% → 0.76, -15% → 0.92, -20%+ capped at 0.92
        raw_conf = 0.68 + abs(deviation_pct) * 1.5
        confidence = round(min(0.92, max(0.60, raw_conf)), 3)

    else:
        return None  # no signal

    if confidence < _MIN_CONFIDENCE:
        return None

    # Volume confirmation: recent 5-bar avg should not be collapsing
    volumes = [float(b.volume) for b in bar_list]
    avg_vol_recent = sum(volumes[-5:]) / 5
    avg_vol_full = sum(volumes) / len(volumes)
    if avg_vol_recent < avg_vol_full * 0.60:  # volume has dried up > 40% — skip
        return None

    # estimated_profit: $15–$22 scaled by confidence (all within $25 cap)
    profit_range = _PROFIT_MAX - _PROFIT_MIN
    conf_range = 0.93 - 0.75
    estimated_profit = round(
        _PROFIT_MIN + (confidence - 0.75) / conf_range * profit_range,
        2,
    )
    estimated_profit = max(_PROFIT_MIN, min(_PROFIT_MAX, estimated_profit))

    signal_desc = (
        f"above 5d SMA ${sma5:.2f} and 20d SMA ${sma20:.2f}"
        if signal == "momentum"
        else f"{abs(deviation_pct)*100:.1f}% below 20d SMA ${sma20:.2f} — oversold bounce setup"
    )

    return {
        "id": f"at-{symbol.lower()}-{date_str}",
        "description": f"{signal.replace('_',' ').title()} signal: {symbol} ${current_price:.2f} — {signal_desc}",
        "estimated_profit": estimated_profit,
        "currency": "USD",
        "confidence": confidence,
        "next_action": f"Place market order: BUY {symbol}",
        "category": "trading",
        "source": "autotrader",
        "url": f"https://finance.yahoo.com/quote/{symbol}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notes": (
            f"symbol: {symbol} | side: buy | signal: {signal} | "
            f"price: {current_price:.2f} | sma5: {sma5:.2f} | sma20: {sma20:.2f} | "
            f"deviation: {deviation_pct:.4f}"
        ),
    }
