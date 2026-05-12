"""
CongressFeedAdapter — fetches Congressional trading disclosures.

Uses Capitol Trades public API (free tier, no auth required for basic access).
Fallback to QuiverQuant if QUIVER_QUANT_API_KEY is set.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta
from typing import Any
import httpx

logger = logging.getLogger(__name__)

CAPITOL_TRADES_BASE = "https://api.capitoltrades.com/v1"
QUIVER_BASE = "https://api.quiverquant.com/beta"
QUIVER_KEY = os.getenv("QUIVER_QUANT_API_KEY", "")


class CongressFeedAdapter:
    """Fetches recent Congressional trading disclosures."""

    def source_name(self) -> str:
        return "congress"

    def fetch_recent(self, days_back: int = 30) -> list[dict[str, Any]]:
        """Return normalised signal dicts from recent Congressional trades."""
        if QUIVER_KEY:
            return self._fetch_quiver(days_back)
        return self._fetch_capitol_trades(days_back)

    def _fetch_capitol_trades(self, days_back: int) -> list[dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            with httpx.Client(timeout=12) as client:
                resp = client.get(
                    f"{CAPITOL_TRADES_BASE}/trades",
                    params={"pageSize": 100, "since": since},
                    headers={"User-Agent": "Hunter/0.2 (+https://hunter.onrender.com)"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("CongressFeedAdapter: Capitol Trades fetch failed: %s", exc)
            return []

        normalised = []
        for trade in data.get("data", []):
            try:
                normalised.append(self._normalise_capitol(trade))
            except Exception as exc:
                logger.debug("CongressFeedAdapter: normalise error: %s", exc)
        return normalised

    def _fetch_quiver(self, days_back: int) -> list[dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            with httpx.Client(timeout=12) as client:
                resp = client.get(
                    f"{QUIVER_BASE}/bulk/congresstrading",
                    headers={
                        "Authorization": f"Token {QUIVER_KEY}",
                        "User-Agent": "Hunter/0.2",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("CongressFeedAdapter: QuiverQuant fetch failed: %s", exc)
            return []

        normalised = []
        for trade in data:
            try:
                trade_dt = datetime.strptime(trade.get("TransactionDate", ""), "%Y-%m-%d")
                if (datetime.utcnow() - trade_dt).days <= days_back:
                    normalised.append(self._normalise_quiver(trade))
            except Exception:
                pass
        return normalised

    def _normalise_capitol(self, t: dict) -> dict:
        politician = t.get("politician", {})
        trade_date_raw = t.get("txDate") or t.get("publishedAt")
        disclosed_raw = t.get("pubDate") or t.get("publishedAt")

        def parse_dt(s):
            if not s:
                return None
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s[:26], fmt)
                except Exception:
                    pass
            return None

        trade_dt = parse_dt(trade_date_raw)
        disclosed_dt = parse_dt(disclosed_raw)
        latency = None
        if trade_dt and disclosed_dt:
            latency = (disclosed_dt - trade_dt).total_seconds() / 3600

        amount_low = t.get("reportingGap", {}).get("lower") or 0
        amount_high = t.get("reportingGap", {}).get("upper") or 0

        return {
            "source": "congress_" + (politician.get("chamber", "house").lower()),
            "source_id": str(t.get("id", "")),
            "filer_name": politician.get("name") or politician.get("fullName", "Unknown"),
            "filer_type": politician.get("chamber", "unknown").lower(),
            "committee": None,
            "ticker": (t.get("issuer", {}).get("ticker") or "").upper(),
            "asset_type": t.get("assetType", "stock").lower(),
            "action": (t.get("type", "buy") or "buy").lower(),
            "amount_low": float(amount_low) if amount_low else None,
            "amount_high": float(amount_high) if amount_high else None,
            "amount_midpoint": ((float(amount_low or 0) + float(amount_high or 0)) / 2) or None,
            "trade_date": trade_dt,
            "disclosed_at": disclosed_dt,
            "latency_hours": latency,
            "raw_json": str(t),
        }

    def _normalise_quiver(self, t: dict) -> dict:
        trade_dt = None
        disclosed_dt = None
        try:
            trade_dt = datetime.strptime(t.get("TransactionDate", ""), "%Y-%m-%d")
            disclosed_dt = datetime.strptime(t.get("DisclosureDate", ""), "%Y-%m-%d")
        except Exception:
            pass

        latency = None
        if trade_dt and disclosed_dt:
            latency = (disclosed_dt - trade_dt).total_seconds() / 3600

        amount_raw = t.get("Range", "") or ""
        amount_low = amount_high = None
        if "-" in amount_raw:
            parts = [p.strip().replace("$", "").replace(",", "") for p in amount_raw.split("-")]
            try:
                amount_low = float(parts[0].replace("K", "000").replace("M", "000000"))
                amount_high = float(parts[1].replace("K", "000").replace("M", "000000"))
            except Exception:
                pass

        chamber = str(t.get("Chamber", "house")).lower()
        return {
            "source": f"congress_{chamber}",
            "source_id": f"qv-{t.get('ID', '')}",
            "filer_name": t.get("Representative") or t.get("Senator") or "Unknown",
            "filer_type": chamber,
            "committee": None,
            "ticker": (t.get("Ticker") or "").upper(),
            "asset_type": (t.get("Asset", "stock") or "stock").lower(),
            "action": (t.get("Transaction", "buy") or "buy").lower(),
            "amount_low": amount_low,
            "amount_high": amount_high,
            "amount_midpoint": ((amount_low or 0) + (amount_high or 0)) / 2 or None,
            "trade_date": trade_dt,
            "disclosed_at": disclosed_dt,
            "latency_hours": latency,
            "raw_json": str(t),
        }
