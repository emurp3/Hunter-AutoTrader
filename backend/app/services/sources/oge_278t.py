"""
Oge278TAdapter — Executive Branch OGE Form 278T Periodic Transaction Reports.

Source: U.S. Office of Government Ethics Electronic Filing and Tracking System (EFTS)
Public URL: https://efts.usethinkbox.com/EFTS/search
No auth required — all filings are public record under EIGA.

Form 278T is the executive branch equivalent of STOCK Act Form 8:
- Filed within 30-45 days of any transaction > $1,000
- Required for: President, VP, all EO schedule-C and SES appointees
- Covers: stocks, bonds, mutual funds, real estate, other investments
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta
from typing import Any
import httpx

logger = logging.getLogger(__name__)

OGE_EFTS_BASE = "https://efts.usethinkbox.com"
REQUEST_TIMEOUT = 20

# Key executive branch officials to track via 278T
TRACKED_OFFICIALS = {
    "Trump, Donald J.": "President",
    "Trump, Donald":    "President",
    "Vance, James D.":  "Vice President",
    "Vance, JD":        "Vice President",
    "Bessent, Scott":   "Secretary of Treasury",
    "Lutnick, Howard":  "Secretary of Commerce",
    "Rubio, Marco":     "Secretary of State",
    "Burgum, Doug":     "Secretary of Interior",
    "Wright, Chris":    "Secretary of Energy",
    "Kennedy, Robert F.": "Secretary of HHS",
    "Navarro, Peter":   "Trade Advisor",
    "Musk, Elon":       "DOGE / Special Advisor",
    "Gabbard, Tulsi":   "DNI",
    "Ratcliffe, John":  "CIA Director",
    "Homan, Tom":       "Border Czar",
    "Lutnick, Howard": "Commerce",
}

ASSET_TYPE_MAP = {
    "stock": "stock",
    "equity": "stock",
    "mutual fund": "etf",
    "etf": "etf",
    "bond": "bond",
    "treasury": "bond",
    "cryptocurrency": "crypto",
    "crypto": "crypto",
    "real estate": "real_estate",
}

AMOUNT_RANGES = [
    ("over $50,000,000", 50_000_000, 100_000_000),
    ("$25,000,001-$50,000,000", 25_000_001, 50_000_000),
    ("$5,000,001-$25,000,000", 5_000_001, 25_000_000),
    ("$1,000,001-$5,000,000", 1_000_001, 5_000_000),
    ("$500,001-$1,000,000", 500_001, 1_000_000),
    ("$250,001-$500,000", 250_001, 500_000),
    ("$100,001-$250,000", 100_001, 250_000),
    ("$50,001-$100,000", 50_001, 100_000),
    ("$15,001-$50,000", 15_001, 50_000),
    ("$1,001-$15,000", 1_001, 15_000),
]


def _parse_oge_date(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime((raw or "").strip()[:20], fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _parse_amount(raw: str) -> tuple[float | None, float | None, float | None]:
    """Parse OGE amount ranges — e.g. '$1,001-$15,000', 'Over $50,000,000'."""
    cleaned = re.sub(r"[$,\s]", "", (raw or "")).lower()
    over_m = re.search(r"over(\d+)", cleaned)
    if over_m:
        lo = float(over_m.group(1))
        return lo, lo * 2.0, lo * 1.5
    # check known OGE ranges first (exact)
    raw_lower = (raw or "").lower().strip()
    for label, lo, hi in AMOUNT_RANGES:
        if label.lower() in raw_lower:
            return float(lo), float(hi), float((lo + hi) / 2)
    # generic X-Y pattern
    r_m = re.search(r"(\d+)-(\d+)", cleaned)
    if r_m:
        lo, hi = float(r_m.group(1)), float(r_m.group(2))
        return lo, hi, (lo + hi) / 2
    s_m = re.search(r"(\d{4,})", cleaned)
    if s_m:
        val = float(s_m.group(1))
        return val, val, val
    return None, None, None


def _infer_asset_type(description: str) -> str:
    desc = (description or "").lower()
    for keyword, atype in ASSET_TYPE_MAP.items():
        if keyword in desc:
            return atype
    return "stock"


class Oge278TAdapter:
    """
    Fetches OGE Form 278T Periodic Transaction Reports for senior
    executive branch officials. Plugs into Hunter's signal engine
    alongside CongressFeedAdapter and SecEdgarAdapter.
    """

    def source_name(self) -> str:
        return "oge_278t"

    def fetch_recent(self, days_back: int = 45) -> list[dict[str, Any]]:
        since = datetime.utcnow() - timedelta(days=days_back)
        results = self._fetch_efts(since)
        logger.info("Oge278T: %d records ingested", len(results))
        return results

    # ── OGE EFTS (primary) ────────────────────────────────────────────────

    def _fetch_efts(self, since: datetime) -> list[dict[str, Any]]:
        """Query OGE's public EFTS for 278T filings. No auth required."""
        from_date = since.strftime("%Y-%m-%d")
        to_date   = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.get(
                    f"{OGE_EFTS_BASE}/EFTS/search",
                    params={
                        "query":       "278T",
                        "report_type": "278T",
                        "dateRange":   "custom",
                        "fromDate":    from_date,
                        "toDate":      to_date,
                        "size":        100,
                    },
                    headers={"User-Agent": "Hunter-AutoTrader/1.0 public-record-research"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Oge278T: EFTS HTTP %d: %s", exc.response.status_code, exc)
            return []
        except Exception as exc:
            logger.warning("Oge278T: EFTS fetch failed: %s", exc)
            return []

        hits = (data.get("hits") or {}).get("hits") or []
        results = []
        for hit in hits:
            row = self._normalize(hit)
            if row:
                results.append(row)
        return results

    def _normalize(self, hit: dict) -> dict | None:
        try:
            src = hit.get("_source") or hit
            last  = src.get("last_name", "") or ""
            first = src.get("first_name", "") or ""
            filer_name = (
                src.get("name")
                or src.get("filer_name")
                or (f"{last}, {first}".strip(", "))
            ).strip()

            position = "Executive Branch Official"
            for key, role in TRACKED_OFFICIALS.items():
                if key.lower() in filer_name.lower():
                    position = role
                    break

            txn_date_raw  = src.get("transaction_date") or src.get("date_received") or ""
            disc_date_raw = src.get("date_received") or txn_date_raw
            txn_date  = _parse_oge_date(txn_date_raw)
            disc_date = _parse_oge_date(disc_date_raw)

            latency_hours = 0.0
            if txn_date and disc_date:
                latency_hours = max(0.0, (disc_date - txn_date).total_seconds() / 3600)
            elif txn_date:
                latency_hours = (datetime.utcnow() - txn_date).total_seconds() / 3600

            raw_amount = src.get("amount") or src.get("value") or ""
            lo, hi, mid = _parse_amount(str(raw_amount))

            ticker_raw = (
                src.get("ticker") or src.get("symbol") or src.get("asset_ticker") or ""
            )
            ticker = re.sub(r":.*", "", ticker_raw).strip().upper()
            if ticker.upper() in ("N/A", "NONE", "", "-"):
                ticker = ""

            action_raw = str(src.get("transaction_type") or src.get("type") or "purchase").lower()
            action = "sell" if any(w in action_raw for w in ("sale", "sell", "sold")) else "buy"

            asset_desc = src.get("asset_description") or src.get("asset_name") or ""
            asset_type = _infer_asset_type(asset_desc)

            source_id = (
                src.get("id")
                or hit.get("_id")
                or f"{filer_name}|{ticker}|{txn_date_raw}"
            )

            return {
                "source":          "oge_278t",
                "source_id":       str(source_id),
                "filer_name":      filer_name,
                "filer_type":      "executive",
                "committee":       position,
                "ticker":          ticker,
                "asset_type":      asset_type,
                "action":          action,
                "amount_low":      lo,
                "amount_high":     hi,
                "amount_midpoint": mid,
                "trade_date":      txn_date,
                "disclosed_at":    disc_date,
                "latency_hours":   latency_hours,
                "raw_json":        str(src)[:400],
            }
        except Exception as exc:
            logger.debug("Oge278T: normalize error: %s", exc)
            return None
