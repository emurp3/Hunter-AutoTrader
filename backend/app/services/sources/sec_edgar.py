"""SecEdgarAdapter — SEC Form 4 public insider disclosures.

Primary data source for Signal Copy Engine after Capitol Trades API went offline.
Fetches from SEC EDGAR full-text search (no auth required) and resolves
tickers via the SEC company_tickers.json CIK map.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta
from typing import Any
import httpx

logger = logging.getLogger(__name__)

_CIK_RE = re.compile(r"\(CIK\s+(\d+)\)", re.IGNORECASE)


class SecEdgarAdapter:
    def source_name(self) -> str:
        return "sec_form4"

    def _load_ticker_map(self, client: httpx.Client) -> dict[int, str]:
        """Build CIK -> ticker map from SEC company_tickers.json."""
        try:
            resp = client.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers={"User-Agent": "Hunter/0.2 emurp3@gmail.com"},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    int(v["cik_str"]): v["ticker"]
                    for v in data.values()
                    if "cik_str" in v and "ticker" in v
                }
        except Exception as exc:
            logger.warning("SecEdgar: ticker map load failed: %s", exc)
        return {}

    def fetch_recent(self, days_back: int = 14) -> list[dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        until = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            with httpx.Client(
                timeout=15,
                headers={"User-Agent": "Hunter/0.2 emurp3@gmail.com"},
            ) as client:
                ticker_map = self._load_ticker_map(client)
                resp = client.get(
                    "https://efts.sec.gov/LATEST/search-index",
                    params={
                        "q": "",
                        "forms": "4",
                        "dateRange": "custom",
                        "startdt": since,
                        "enddt": until,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("SecEdgar: HTTP %d from EFTS", resp.status_code)
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("SecEdgar fetch error: %s", exc)
            return []

        hits = data.get("hits", {}).get("hits", [])
        results = []
        for h in hits[:50]:
            row = self._normalise(h, ticker_map)
            if row:
                results.append(row)
        return results

    def _normalise(self, hit: dict, ticker_map: dict[int, str]) -> dict | None:
        try:
            src = hit.get("_source", {})

            # Resolve ticker from display_names via CIK lookup
            ticker = ""
            filer_name = "Unknown Insider"
            company_name = ""
            display_names = src.get("display_names", [])
            for i, dn in enumerate(display_names):
                clean = _CIK_RE.sub("", dn).strip()
                m = _CIK_RE.search(dn)
                cik = int(m.group(1)) if m else None
                if i == 0:
                    filer_name = clean or "Unknown Insider"
                else:
                    company_name = clean
                    if cik and cik in ticker_map:
                        ticker = ticker_map[cik]

            # Parse filing date
            raw_date = src.get("file_date") or src.get("period_of_report") or src.get("period_ending") or ""
            dt = None
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    dt = datetime.strptime(str(raw_date)[:10], fmt)
                    break
                except Exception:
                    pass

            latency_hours = 0.0
            if dt:
                latency_hours = max(0.0, (datetime.utcnow() - dt).total_seconds() / 3600)

            return {
                "source": "sec_form4",
                "source_id": hit.get("_id", ""),
                "filer_name": filer_name,
                "filer_type": "insider",
                "committee": company_name or None,
                "ticker": ticker,
                "asset_type": "stock",
                # Form 4 transaction type requires XML parse; default buy for now
                "action": "buy",
                "amount_low": None,
                "amount_high": None,
                "amount_midpoint": None,
                "trade_date": dt,
                "disclosed_at": dt,
                "latency_hours": latency_hours,
                "raw_json": str(src)[:400],
            }
        except Exception as exc:
            logger.debug("SecEdgar normalise error: %s", exc)
            return None
