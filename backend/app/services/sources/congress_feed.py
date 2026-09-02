"""
CongressFeedAdapter — Congressional STOCK Act trade disclosures.

Primary source: Tracefour (env: TRACEFOUR_API_KEY) — free tier, no paid
subscription. Requires a free account/key from https://tracefour.com/settings
(60 requests/hour). Iterates the same VIP-relevant member list signal_engine.py
already watches, via GET /v1/congress/{member-slug}.

Optional paid fallbacks, only used if their keys happen to be set (neither
is required, neither is recommended given the standing rule not to spend
more on Hunter than it's generating):
  - Politician Trade Tracker API via RapidAPI (env: RAPIDAPI_POLITICIAN_KEY)
  - QuiverQuant (env: QUIVER_QUANT_API_KEY)

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

TRACEFOUR_API_KEY = os.getenv("TRACEFOUR_API_KEY", "")
TRACEFOUR_BASE = "https://tracefour.com"
# Same politicians signal_engine.py's VIP_WATCHLIST already tracks (STOCK Act
# / "congress" source entries) -- no point pulling a broader feed than the
# set of names Hunter actually acts on.
TRACEFOUR_MEMBER_SLUGS = [
    "nancy-pelosi",
    "matt-gaetz",
    "dan-crenshaw",
    "michael-mccaul",
    "mitch-mcconnell",
    "chuck-schumer",
    "marco-rubio",
    "elizabeth-warren",
    "mark-warner",
    "tommy-tuberville",
    "josh-hawley",
    "pat-toomey",
]

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


def _parse_iso_date(raw: str) -> datetime | None:
    """Parse ISO 8601 timestamps (e.g. Tracefour's 'transactionDate'/'filedAt' fields)."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
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
        if TRACEFOUR_API_KEY:
            return self._fetch_tracefour()
        if RAPIDAPI_KEY:
            return self._fetch_rapidapi()
        if QUIVER_KEY:
            return self._fetch_quiver(days_back)
        logger.warning(
            "CongressFeed: no API key set. Set TRACEFOUR_API_KEY (free -- see "
            "https://tracefour.com/settings) or, if already paying for one, "
            "RAPIDAPI_POLITICIAN_KEY / QUIVER_QUANT_API_KEY."
        )
        return []

    # ── Tracefour (primary, free) ────────────────────────────────────────────

    def _fetch_tracefour(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        headers = {"Authorization": f"Bearer {TRACEFOUR_API_KEY}"}
        with httpx.Client(timeout=15, headers=headers) as client:
            for slug in TRACEFOUR_MEMBER_SLUGS:
                try:
                    resp = client.get(f"{TRACEFOUR_BASE}/v1/congress/{slug}")
                    if resp.status_code == 404:
                        continue  # member has no filings on record, not an error
                    resp.raise_for_status()
                    envelope = resp.json()
                except Exception as exc:
                    logger.warning("CongressFeed: Tracefour fetch failed for %s: %s", slug, exc)
                    continue

                trades = envelope.get("data") or []
                if isinstance(trades, dict):
                    # Some tracker-style responses nest the list under a key
                    # (e.g. {"member": {...}, "trades": [...]})
                    trades = trades.get("trades") or trades.get("transactions") or []
                for r in trades:
                    row = self._normalise_tracefour(r, member_slug=slug)
                    if row:
                        results.append(row)

        logger.info("CongressFeed: Tracefour returned %d records across %d members", len(results), len(TRACEFOUR_MEMBER_SLUGS))
        return results

    def _normalise_tracefour(self, r: dict, *, member_slug: str) -> dict | None:
        try:
            # Field names confirmed against a live Tracefour response
            # (2026-09-02) -- these are NOT guesses from the OpenAPI spec,
            # which doesn't document per-field schemas.
            raw_txn_date = r.get("transactionDate") or ""
            raw_disclosure_date = r.get("disclosureDate") or ""
            trade_date = _parse_iso_date(raw_txn_date) or _parse_trade_date(raw_txn_date)
            disclosure_date = _parse_iso_date(raw_disclosure_date) or _parse_trade_date(raw_disclosure_date)

            filer = r.get("memberName") or member_slug.replace("-", " ").title()
            ticker = _clean_ticker(r.get("ticker") or "")

            # Tracefour gives direct min/mid/max values -- prefer those over
            # parsing amountLabel ("$500,001 - $1,000,000"), which doesn't
            # match the existing _AMOUNT_MAP key format anyway.
            lo = r.get("amountMin")
            hi = r.get("amountMax")
            mid = r.get("amountMid")
            if mid is None and (lo is not None or hi is not None):
                mid = ((lo or 0) + (hi or lo or 0)) / 2
            lo = float(lo) if lo is not None else None
            hi = float(hi) if hi is not None else None
            mid = float(mid) if mid is not None else None

            action = str(r.get("type") or "buy").lower()
            if action.startswith("p") or "buy" in action:
                action = "buy"
            elif action.startswith("s") or "sell" in action:
                action = "sell"

            # True STOCK Act reporting latency: time between the transaction
            # and when it was actually disclosed, not "how old is this trade
            # now" (which is what the age-since-trade fallback used
            # elsewhere in this file computes, and is a different, less
            # useful signal -- a filer who takes the full 45-day STOCK Act
            # window to disclose is a meaningfully different signal than one
            # who discloses same-day, regardless of how old the record is
            # by the time Hunter happens to scan it).
            latency_hours = 0.0
            if trade_date and disclosure_date:
                latency_hours = max(0.0, (disclosure_date - trade_date).total_seconds() / 3600)

            return {
                "source": "congress",
                "source_id": r.get("filingId") or f"{filer}|{ticker}|{raw_txn_date}",
                "filer_name": filer,
                "filer_type": "politician",
                "committee": r.get("chamber") or r.get("party") or None,
                "ticker": ticker,
                "asset_type": "stock",
                "action": action if action in ("buy", "sell") else "buy",
                "amount_low": lo,
                "amount_high": hi,
                "amount_midpoint": mid,
                "trade_date": trade_date,
                "disclosed_at": disclosure_date or trade_date,
                "latency_hours": latency_hours,
                "raw_json": str(r)[:400],
            }
        except Exception as exc:
            logger.debug("CongressFeed: Tracefour normalise error: %s", exc)
            return None

    # ── RapidAPI (optional paid fallback) ────────────────────────────────────

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

    # ── QuiverQuant (optional paid fallback) ─────────────────────────────────

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
