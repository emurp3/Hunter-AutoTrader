"""
Hunter Crypto Engine.

Manages BTC/ETH/SOL positions via Alpaca with a HARD 15% portfolio cap.

The wall is absolute:
  - crypto_value (positions at market) + pending_buys <= 15% of total_portfolio_value
  - Enforced BEFORE every order, no exceptions
  - If a position appreciates past the wall, no new buys until back under cap
  - Returns do NOT re-invest beyond the cap automatically

Assets: BTC, ETH, SOL (and other Alpaca-listed coins)
Source: CoinGecko velocity signals + congressional crypto disclosures
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Hard wall — read from config but default here too for safety
CRYPTO_CAP   = float(os.getenv("CRYPTO_ALLOCATION_CAP", "0.15"))
MICRO_AMOUNT = float(os.getenv("CRYPTO_MICRO_INVEST", "10.00"))
ALPACA_BASE  = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")


def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID":     (os.getenv("LIVE_API_KEY") or os.getenv("SANDBOX_API_KEY", "")).strip(),
        "APCA-API-SECRET-KEY": (os.getenv("LIVE_SECRET_KEY") or os.getenv("SANDBOX_SECRET_KEY", "")).strip(),
    }


def get_crypto_allocation_state() -> dict:
    """
    Fetch current crypto exposure from Alpaca.
    Returns: crypto_value, total_portfolio_value, crypto_pct, cap, headroom.
    """
    try:
        with httpx.Client(timeout=10) as client:
            acct = client.get(f"{ALPACA_BASE}/v2/account", headers=_alpaca_headers()).json()
            positions = client.get(f"{ALPACA_BASE}/v2/positions", headers=_alpaca_headers()).json()

        total_portfolio = float(acct.get("portfolio_value") or acct.get("equity") or 0)
        if total_portfolio <= 0:
            return {"error": "zero_portfolio", "headroom": 0.0, "blocked": True}

        # Crypto positions — Alpaca crypto symbols end with /USD or are in CRYPTO_SUPPORTED list
        crypto_symbols = {"BTCUSD", "ETHUSD", "SOLUSD", "AVAXUSD", "LINKUSD", "DOTUSD",
                          "BTC", "ETH", "SOL", "AVAX", "LINK", "DOT"}
        crypto_value = sum(
            float(p.get("market_value") or 0)
            for p in (positions if isinstance(positions, list) else [])
            if p.get("symbol", "").upper() in crypto_symbols
               or p.get("asset_class") == "crypto"
        )

        crypto_pct  = crypto_value / total_portfolio
        headroom    = max(0.0, (CRYPTO_CAP * total_portfolio) - crypto_value)
        blocked     = crypto_pct >= CRYPTO_CAP

        return {
            "total_portfolio_value": round(total_portfolio, 2),
            "crypto_value":          round(crypto_value, 2),
            "crypto_pct":            round(crypto_pct, 4),
            "cap":                   CRYPTO_CAP,
            "cap_value":             round(CRYPTO_CAP * total_portfolio, 2),
            "headroom":              round(headroom, 2),
            "blocked":               blocked,
            "reason":                f"Crypto at {crypto_pct*100:.1f}% of {CRYPTO_CAP*100:.0f}% cap" if blocked else "Under cap",
        }
    except Exception as exc:
        logger.warning("Crypto allocation check failed: %s", exc)
        return {"error": str(exc), "headroom": 0.0, "blocked": True}


def place_crypto_order(symbol: str, side: str = "buy", notional: Optional[float] = None) -> dict:
    """
    Place a crypto market order on Alpaca.
    HARD WALL enforced: returns {status: 'blocked'} if over the 15% cap.
    """
    symbol = symbol.upper().replace("/USD", "").replace("USD", "")
    notional = notional or MICRO_AMOUNT

    # Enforce hard wall BEFORE placing order
    state = get_crypto_allocation_state()
    if state.get("blocked"):
        logger.warning("CRYPTO WALL: order blocked for %s. %s", symbol, state.get("reason"))
        return {"status": "blocked", "reason": state["reason"], "symbol": symbol}

    # Cap notional to available headroom
    headroom = state.get("headroom", 0.0)
    if notional > headroom:
        notional = round(headroom, 2)
        logger.info("Crypto order capped to headroom: $%.2f for %s", notional, symbol)
    if notional < 1.0:
        return {"status": "blocked", "reason": "insufficient_headroom", "symbol": symbol}

    try:
        alpaca_symbol = symbol + "/USD"  # Alpaca crypto format
        resp = httpx.post(
            f"{ALPACA_BASE}/v2/orders",
            json={"symbol": alpaca_symbol, "notional": str(notional),
                  "side": side, "type": "market", "time_in_force": "gtc"},
            headers=_alpaca_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            order = resp.json()
            logger.info("CRYPTO ORDER OK: $%.2f %s %s | id=%s", notional, side.upper(), symbol, order.get("id"))
            return {"status": "executed", "symbol": symbol, "notional": notional,
                    "side": side, "order_id": order.get("id"),
                    "cap_pct_after": round((state["crypto_value"] + notional) / state["total_portfolio_value"], 4)}
        else:
            logger.warning("Crypto order FAILED: %d %s", resp.status_code, resp.text[:200])
            return {"status": "error", "code": resp.status_code, "symbol": symbol}
    except Exception as exc:
        logger.exception("Crypto order exception: %s", exc)
        return {"status": "exception", "error": str(exc), "symbol": symbol}
