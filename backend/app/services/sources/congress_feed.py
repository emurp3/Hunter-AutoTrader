"""
CongressFeedAdapter — Congressional STOCK Act trade disclosures.

Primary source: Politician Trade Tracker API via RapidAPI
  Host: politician-trade-tracker1.p.rapidapi.com
  Endpoint: GET /trades/latest
  Auth: X-RapidAPI-Key header (env: RAPIDAPI_POLITICIAN_KEY)

Fallback: QuiverQuant (env: QUIVER_QUANT_API_KEY)

Capitol Trades API (capitoltrades.com) went offline May 2026.
"""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime
from typing import Any
import httpx

logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.getenv("RAPIDAPI_POLITICIAN_KEY", "")
RAPIDAPI_HOST = "politician-trade-tracker1.p.rapidapi.com"
QUIVER_BASE = "https://api.quiverquant.com/beta"
QUIVER_KEY = os.getenv("QUIVER_QUANT_API_KEY", "")

_AMOUNT_MAP = {
    "1K-15K": (1_000, 15_000),
    "15K-50K": (15_000, 50_000),
    "50K-100K": (50_000, 100_000),
    "100K-250K": (100_000, 250_000),
    "250K-500K": (250_000, 500_000),
    "500K-1M": (500_000, 1_000_000),
    "1M-5M": (1_000_000, 5_000_000),
    "5M-25M": (5_000_000, 25_000_000),
    "25M-50M": (25_000_000, 50_000_000),
}


def _parse_amount(raw: str) -> tuple[float | None, float | None, float | None]:
    """Return (low, high, midpoint) from a range string like '1K-15K'."""
    key = re.sub(r"[$s]", "", (raw or "").upper()).replace(",", "")
    if key in _AMOUNT_MAP:
        lo, hi = _AMOUNT_MAP[key]
        return float(lo), float(hi), float((lo + hi) / 2)
    return None, None, None


def _parse_trade_date(raw: str) -> datetime | None:
    """Parse 'April 17, 2026' style dates."""
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _clean_ticker(raw: str) -> str:
    """Strip exchange suffix — 'AMT:US' -> 'AMT'. N/A -> ''."""
    t = (raw or "").split(":")[0].strip()
    return "" if t.upper() in ("N/A", "", "-") else t


class CongressFeedAdapter:
    """Fetches recent Congressional trading disclosures."""

    def source_name(self) -> str:
        return "congress"

    def fetch_recent(self, days_back: int = 30) -> list[dict[str, Any]]:
        if RAPIDAPI_KEY:
            return self._fetch_rapidapi()
        if QUIVER_KEY:
            return self._fetch_quiver(days_back)
        logger.warning("CongressFeed: no API key set. Set RAPIDAPI_POLITICIAN_KEY or QUIVER_QUANT_API_KEY.")
        return []

    # ── RapidAPI (primary) ────────────────────────────────────────────────────

    def _fetch_rapidapi(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    f"https://{RAPIDAPI_HOST}/trades/latest",
                    headers={
                        "X-RapidAPI-Key": RAPIDAPI_KEY,
                        "X-RapidAPI-Host": RAPIDAPI_HOST,
                    },
                )
                resp.raise_for_status()
                records = resp.json()
        except Exception as exc:
            logger.warning("CongressFeed: RapidAPI fetch failed: %s", exc)
            return []

        results = []
        for r in records:
            row = self._normalise_rapidapi(r)
            if row:
                results.append(row)
        logger.info("CongressFeed: RapidAPI returned %d records", len(results))
        return results

    def _normalise_rapidapi(self, r: dict) -> dict | None:
        try:
            trade_date = _parse_trade_date(r.get("trade_date", ""))
            latency_hours = float((r.get("days_until_disclosure") or 0)) * 24.0
            lo, hi, mid = _parse_amount(r.get("trade_amount", ""))
            ticker = _clean_ticker(r.get("ticker", ""))
            action = (r.get("trade_type") or "buy").lower()
            filer = r.get("name") or "Unknown Politician"
            committee_info = f"{r.get('chamber','')}/{r.get('party','')}/{r.get('state_abbreviation','')}"
            source_id = f"{filer}|{r.get('ticker','')}|{r.get('trade_date','')}"
            return {
                "source": "congress",
                "source_id": source_id,
                "filer_name": filer,
                "filer_type": "politician",
                "committee": committee_info,
                "ticker": ticker,
                "asset_type": "stock",
                "action": action if action in ("buy", "sell") else "buy",
                "amount_low": lo,
                "amount_high": hi,
                "amount_midpoint": mid,
                "trade_date": trade_date,
                "disclosed_at": trade_date,
                "latency_hours": latency_hours,
                "raw_json": str(r)[:400],
            }
        except Exception as exc:
            logger.debug("CongressFeed: normalise error: %s", exc)
            return None

    # ── QuiverQuant fallback ──────────────────────────────────────────────────

    def _fetch_quiver(self, days_back: int) -> list[dict[str, Any]]:
        since = (datetime.utcnow().__class__.utcnow() )
        try:
            with httpx.Client(timeout=12) as client:
                resp = client.get(
                    f"{QUIVER_BASE}/bulk/congresstrading",
                    headers={"Authorization": f"Token {QUIVER_KEY}", "User-Agent": "Hunter/0.2"},
                )
                resp.raise_for_status()
                records = resp.json()
        except Exception as exc:
            logger.warning("CongressFeed: QuiverQuant fetch failed: %s", exc)
            return []

        results = []
        for r in records[:100]:
            row = self._normalise_quiver(r)
            if row:
                results.append(row)
        return results

    def _normalise_quiver(self, r: dict) -> dict | None:
        try:
            raw_date = r.get("Date") or r.get("TransactionDate") or ""
            dt = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(raw_date[:10], fmt)
                    break
                except ValueError:
                    pass
            latency_hours = 0.0
            if dt:
                latency_hours = max(0.0, (datetime.utcnow() - dt).total_seconds() / 3600)
            lo, hi, mid = _parse_amount(r.get("Range", ""))
            return {
                "source": "congress",
                "source_id": f"{r.get('Representative','')}|{r.get('Ticker','')}|{raw_date}",
                "filer_name": r.get("Representative") or "Unknown",
                "filer_type": "politician",
                "committee": r.get("Party") or None,
                "ticker": (r.get("Ticker") or "").upper(),
                "asset_type": "stock",
                "action": "buy" if str(r.get("Transaction", "")).lower() == "purchase" else "sell",
                "amount_low": lo,
                "amount_high": hi,
                "amount_midpoint": mid,
                "trade_date": dt,
                "disclosed_at": dt,
                "latency_hours": latency_hours,
                "raw_json": str(r)[:400],
            }
        except Exception:
            return None
