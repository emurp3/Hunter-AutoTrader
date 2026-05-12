"""SecEdgarAdapter for SEC Form 4 public insider disclosures."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Any
import httpx

logger = logging.getLogger(__name__)

class SecEdgarAdapter:
    def source_name(self) -> str: return "sec_form4"

    def fetch_recent(self, days_back: int = 14) -> list[dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        until = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            with httpx.Client(timeout=15, headers={"User-Agent": "Hunter/0.2 commander@hunter.ai"}) as client:
                resp = client.get(
                    "https://efts.sec.gov/LATEST/search-index",
                    params={"q": "", "forms": "4", "dateRange": "custom", "startdt": since, "enddt": until},
                )
                if resp.status_code != 200:
                    logger.warning("SecEdgar: %d", resp.status_code)
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("SecEdgar fetch error: %s", exc)
            return []
        hits = data.get("hits", {}).get("hits", [])
        return [self._normalise(h) for h in hits[:50] if self._normalise(h)]

    def _normalise(self, hit: dict) -> dict | None:
        try:
            src = hit.get("_source", {})
            raw = src.get("file_date") or src.get("period_of_report") or ""
            dt = None
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try: dt = datetime.strptime(raw[:10], fmt); break
                except Exception: pass
            return {
                "source": "sec_form4",
                "source_id": hit.get("_id", ""),
                "filer_name": src.get("entity_name") or "Unknown Insider",
                "filer_type": "insider",
                "committee": None, "ticker": "", "asset_type": "stock",
                "action": "buy", "amount_low": None, "amount_high": None,
                "amount_midpoint": None, "trade_date": dt, "disclosed_at": dt,
                "latency_hours": 0.0, "raw_json": str(src)[:400],
            }
        except Exception: return None
