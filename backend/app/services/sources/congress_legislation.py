"""
CongressLegislationAdapter — monitors Congress.gov for bills,
amendments, and committee actions with business-opportunity signal.

Uses Congress.gov API (env: CONGRESS_API_KEY) with RSS fallback.
Distinct from CongressFeedAdapter (which tracks STOCK Act trades).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.sources.base import SourceAdapter, SourceOpportunity

logger = logging.getLogger(__name__)

CONGRESS_API_KEY = os.getenv("CONGRESS_API_KEY", "")
CONGRESS_API_BASE = "https://api.congress.gov/v3"
CONGRESS_RSS = "https://www.congress.gov/rss/most-viewed-bills.xml"
_TIMEOUT = 20
_UA = "HunterP2PEngine/1.0 PolicyScanner"

_OPPORTUNITY_KEYWORDS = {
    "infrastructure", "technology", "ai", "artificial intelligence",
    "defense", "veterans", "healthcare", "small business", "innovation",
    "grant", "appropriations", "funding", "invest", "energy", "manufacturing",
    "cybersecurity", "education", "workforce", "training", "contract",
}


class CongressLegislationAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "congress_legislation"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        if CONGRESS_API_KEY:
            try:
                return self._fetch_via_api()
            except Exception as exc:
                logger.warning("congress_legislation: API failed — %s", exc)
        try:
            return self._fetch_via_rss()
        except Exception as exc:
            logger.warning("congress_legislation: RSS failed — %s", exc)
            return []

    def _fetch_via_api(self) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "api_key": CONGRESS_API_KEY,
            "fromDateTime": since,
            "sort": "updateDate+desc",
            "limit": "20",
        }
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
            resp = client.get(f"{CONGRESS_API_BASE}/bill", params=params)
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            data = resp.json()
            bills = data.get("bills", [])
            return [
                {
                    "title": b.get("title", b.get("number", ""))[:300],
                    "url": b.get("url", "https://www.congress.gov"),
                    "summary": f"Bill {b.get('type', '')}{b.get('number', '')}: {b.get('title', '')[:200]}",
                    "bill_number": f"{b.get('type', '')}{b.get('number', '')}",
                    "published_at": None
                }
                for b in bills if b.get("title")
            ]

    def _fetch_via_rss(self) -> list[dict[str, Any]]:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
            resp = client.get(CONGRESS_RSS, follow_redirects=True)
            if resp.status_code != 200:
                return []
        items = []
        for raw in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
            title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", raw, re.DOTALL)
            link_m = re.search(r"<link>(.*?)</link>", raw)
            desc_m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>", raw, re.DOTALL)
            title = next((g for g in (title_m.groups() if title_m else []) if g), "").strip()
            link = (link_m.group(1) if link_m else "").strip()
            desc = re.sub(r"<[^>]+>", " ", next((g for g in (desc_m.groups() if desc_m else []) if g), "")).strip()
            if title and link:
                items.append({"title": title, "url": link, "summary": desc[:400] or title, "published_at": None})
        return items[:20]

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title", "").strip()
        url = raw.get("url", "").strip()
        if not title:
            return None

        title_lower = title.lower()
        matches = sum(1 for kw in _OPPORTUNITY_KEYWORDS if kw in title_lower)
        confidence = min(0.50 + (matches * 0.07), 0.88)

        source_id = hashlib.sha256(f"congress_leg|{title[:100]}".encode()).hexdigest()[:16]
        summary = raw.get("summary") or title

        return SourceOpportunity(
            source_id=source_id,
            title=title[:200],
            description=f"[Congress.gov] {summary[:400]}",
            estimated_profit=0.0,
            currency="USD",
            confidence=confidence,
            next_action="Policy-to-Profit Engine: identify legislative market opportunities",
            origin_module="policy_engine",
            category="Legislative",
            source_url=url or "https://www.congress.gov",
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="congress_legislation",
            signal_type="legislative_action",
            metadata={"bill_number": raw.get("bill_number", "")},
        )
