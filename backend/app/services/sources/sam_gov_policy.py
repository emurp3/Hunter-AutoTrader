"""
SamGovAdapter — Government contracting opportunities from SAM.gov.

Uses USASpending.gov public API as primary source (no auth required),
with SAM.gov API as enhancement when key is configured.
Captures RFPs, RFIs, and procurement announcements.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.sources.base import SourceAdapter, SourceOpportunity

logger = logging.getLogger(__name__)

SAM_API_KEY = os.getenv("SAM_GOV_API_KEY", "")
SAM_API_BASE = "https://api.sam.gov/opportunities/v2/search"
SAM_PUBLIC_URL = "https://sam.gov/search/?index=opp&sort=-modifiedDate&page=1&is_active=true"
_TIMEOUT = 20
_UA = "HunterP2PEngine/1.0 PolicyScanner"

_TARGET_KEYWORDS = [
    "information technology", "project management", "consulting",
    "healthcare", "veterans", "artificial intelligence", "cybersecurity",
    "data analytics", "software", "training", "education", "management",
]


class SamGovAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "sam_gov"

    def _fetch_via_api(self) -> list[dict[str, Any]]:
        """Use SAM.gov API if key is configured."""
        params = {
            "api_key": SAM_API_KEY,
            "ptype": "o,p,k,r",
            "limit": "25",
            "offset": "0",
            "status": "active",
            "postedFrom": (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%m/%d/%Y"),
            "postedTo": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
            "keywords": "information technology OR project management OR consulting OR veterans",
        }
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
            resp = client.get(SAM_API_BASE, params=params)
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            data = resp.json()
            return data.get("opportunitiesData", []) or []

    def _fetch_usaspending(self) -> list[dict[str, Any]]:
        """Fetch recent prime awards from USASpending as a proxy for active contracts."""
        url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
        body = {
            "filters": {
                "award_type_codes": ["A", "B", "C", "D"],
                "time_period": [
                    {
                        "start_date": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d"),
                        "end_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    }
                ],
                "keywords": ["information technology", "consulting", "project management"]
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Description"],
            "sort": "Award Amount",
            "order": "desc",
            "limit": 15,
            "page": 1
        }
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA, "Content-Type": "application/json"}) as client:
                resp = client.post(url, json=body)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    items = []
                    for r in results:
                        if not r.get("Description"):
                            continue
                        amount = r.get("Award Amount", 0) or 0
                        agency = r.get("Awarding Agency", "")
                        recip = r.get("Recipient Name", "")
                        desc = r.get("Description", "")[:100]
                        items.append({
                            "title": f"{desc} — {agency}"[:200],
                            "url": f"https://usaspending.gov/award/{r.get('Award ID', '')}",
                            "summary": f"Award: ${amount:,.0f} | Agency: {agency} | Recipient: {recip}",
                            "published_at": None,
                            "source": "usaspending"
                        })
                    return items
        except Exception as exc:
            logger.debug("sam_gov: usaspending fallback failed — %s", exc)
        return []

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        try:
            raw: list[dict[str, Any]] = []
            if SAM_API_KEY:
                raw = self._fetch_via_api()
            else:
                raw = self._fetch_usaspending()

            items: list[dict[str, Any]] = []
            for r in raw:
                if isinstance(r, dict) and r.get("title"):
                    items.append(r)
                elif isinstance(r, dict):
                    t = r.get("title", "") or r.get("opportunityTitle", "")
                    u = r.get("uiLink", "") or r.get("opportunity_url", "") or SAM_PUBLIC_URL
                    s = r.get("description", "") or r.get("synopsis", "") or t
                    if t:
                        items.append({"title": t[:300], "url": u, "summary": str(s)[:500], "published_at": None})
            logger.info("sam_gov: %d opportunities fetched", len(items))
            return items
        except Exception as exc:
            logger.warning("sam_gov: fetch failed — %s", exc)
            return []

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title", "").strip()
        url = raw.get("url", "").strip()
        if not title:
            return None

        confidence = 0.75
        title_lower = title.lower()
        if any(kw in title_lower for kw in _TARGET_KEYWORDS):
            confidence = min(confidence + 0.10, 0.90)

        source_id = hashlib.sha256(f"sam_gov|{title[:100]}".encode()).hexdigest()[:16]
        summary = raw.get("summary") or title

        return SourceOpportunity(
            source_id=source_id,
            title=title[:200],
            description=f"[Government Contract — SAM.gov] {summary[:400]}",
            estimated_profit=5000.0,
            currency="USD",
            confidence=confidence,
            next_action="Review solicitation, assess fit, prepare bid or subcontracting strategy",
            origin_module="policy_engine",
            category="Government Contracting",
            source_url=url or SAM_PUBLIC_URL,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="sam_gov",
            signal_type="government_contract",
            metadata={},
        )
