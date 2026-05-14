"""
CryptoSignalAdapter — CoinGecko price velocity signals.

Fetches BTC/ETH/SOL price data from CoinGecko (free, no auth).
Generates signals based on 24h price velocity and volume surge.

Routing logic:
  velocity >  15%  in 24h  AND  volume > 2x  7d avg → mirror (buy)
  velocity < -15%  in 24h                            → reject (sell signal, too late)
  velocity  5-15%  in 24h  AND  volume elevated       → partial_mirror
  otherwise                                           → watchlist
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
import httpx

logger = logging.getLogger(__name__)

COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
CRYPTO_TARGETS  = {
    "bitcoin":  {"symbol": "BTC", "alpaca": "BTC"},
    "ethereum": {"symbol": "ETH", "alpaca": "ETH"},
    "solana":   {"symbol": "SOL", "alpaca": "SOL"},
    "avalanche-2": {"symbol": "AVAX", "alpaca": "AVAX"},
    "chainlink":   {"symbol": "LINK", "alpaca": "LINK"},
}


class CryptoSignalAdapter:
    def source_name(self) -> str:
        return "crypto_coingecko"

    def fetch_recent(self, days_back: int = 1) -> list[dict[str, Any]]:
        ids = ",".join(CRYPTO_TARGETS.keys())
        try:
            with httpx.Client(timeout=15, headers={"User-Agent": "Hunter/0.2 emurp3@gmail.com"}) as client:
                resp = client.get(
                    f"{COINGECKO_BASE}/coins/markets",
                    params={"vs_currency": "usd", "ids": ids,
                            "price_change_percentage": "24h,7d",
                            "order": "market_cap_desc"},
                )
                if resp.status_code != 200:
                    logger.warning("CoinGecko: HTTP %d", resp.status_code)
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("CoinGecko fetch error: %s", exc)
            return []

        signals = []
        for coin in data:
            coin_id = coin.get("id", "")
            meta = CRYPTO_TARGETS.get(coin_id, {})
            if not meta:
                continue

            pct_24h  = float(coin.get("price_change_percentage_24h") or 0)
            pct_7d   = float(coin.get("price_change_percentage_7d_in_currency") or 0)
            volume   = float(coin.get("total_volume") or 0)
            mkt_cap  = float(coin.get("market_cap") or 1)
            price    = float(coin.get("current_price") or 0)
            vol_ratio = volume / mkt_cap  # normalized volume

            # Determine action + routing
            action = "buy"
            if pct_24h > 15 and vol_ratio > 0.08:
                decision = "mirror"         # strong momentum + volume
            elif 5 <= pct_24h <= 15 and vol_ratio > 0.04:
                decision = "partial_mirror"  # moderate momentum
            elif pct_24h < -15:
                decision = "reject"          # crash — too late
                action   = "sell"
            else:
                decision = "watchlist"

            now = datetime.now(timezone.utc)
            signals.append({
                "source":       "crypto_coingecko",
                "source_id":    f"{coin_id}|{now.strftime('%Y-%m-%d')}",
                "filer_name":   f"{coin.get('name')} | CoinGecko",
                "filer_type":   "crypto_market",
                "committee":    f"24h: {pct_24h:+.1f}% | 7d: {pct_7d:+.1f}%",
                "ticker":       meta["symbol"],
                "asset_type":   "crypto",
                "action":       action,
                "amount_low":   None,
                "amount_high":  None,
                "amount_midpoint": None,
                "trade_date":   now,
                "disclosed_at": now,
                "latency_hours": 0.0,
                "raw_json":     str({"pct_24h": pct_24h, "pct_7d": pct_7d, "vol_ratio": round(vol_ratio,4), "price": price})[:400],
                # Attach routing hint for signal_engine
                "_pre_decision":  decision,
            })

        logger.info("CoinGecko: %d crypto signals generated", len(signals))
        return signals
