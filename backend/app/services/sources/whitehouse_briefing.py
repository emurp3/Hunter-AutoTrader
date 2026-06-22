"""
WhitehouseBriefingAdapter — Briefing Room source for the Policy-to-Profit Engine.

Monitors policy announcements, press releases, and executive communications
before they become formal EOs or procurement opportunities.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.services.sources.base import SourceAdapter, SourceOpportunity

logger = logging.getLogger(__name__)

RSS_URLS = [
    "https://www.whitehouse.gov/briefing-room/press-releases/feed/",
    "https://www.whitehouse.gov/briefing-room/statements-releases/feed/",
    "https://www.whitehouse.gov/briefing-room/feed/",
]
_TIMEOUT = 15
_UA = "HunterP2PEngine/1.0 PolicyScanner"

# Keywords indicating high-value policy announcements
_HIGH_VALUE_KEYWORDS = {
    "executive order", "proclamation", "memorandum", "contract", "procurement",
    "technology", "ai", "artificial intelligence", "defense", "healthcare",
    "infrastructure", "manufacturing", "energy", "veterans", "small business",
    "grant", "fund", "invest", "initiative", "program", "agency",
}


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _parse_rss(xml: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        title_m = re.search(r"<title><![CDATA[(.*?)]]></title>|<title>(.*?)</title>", raw, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>", raw)
        desc_m = re.search(r"<description><![CDATA[(.*?)]]></description>|<description>(.*?)</description>", raw, re.DOTALL)
        date_m = re.search(r"<pubDate>(.*?)</pubDate>", raw)

        title = next((g for g in (title_m.groups() if title_m else []) if g), "").strip()
        link = (link_m.group(1) if link_m else "").strip()
        desc = _strip_tags(next((g for g in (desc_m.groups() if desc_m else []) if g), "")).strip()
        pub_raw = (date_m.group(1) if date_m else None)

        pub_dt: datetime | None = None
        if pub_raw:
            try:
                pub_dt = parsedate_to_datetime(pub_raw.strip())
            except Exception:
                pass

        if title and link:
            items.append({"title": title, "url": link, "summary": desc or title, "published_at": pub_dt})
    return items


class WhitehouseBriefingAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "whitehouse_briefing"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        all_items: list[dict[str, Any]] = []
        for rss_url in RSS_URLS:
            try:
                with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                    resp = client.get(rss_url, follow_redirects=True)
                    if resp.status_code == 200:
                        items = _parse_rss(resp.text)
                        all_items.extend(items)
                        if items:
                            break
            except Exception as exc:
                logger.debug("whitehouse_briefing: %s failed — %s", rss_url, exc)

        # Deduplicate by URL
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in all_items:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
        return unique[:25]

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title", "").strip()
        url = raw.get("url", "").strip()
        if not title or not url:
            return None

        # Score relevance to boost confidence
        title_lower = title.lower()
        matches = sum(1 for kw in _HIGH_VALUE_KEYWORDS if kw in title_lower)
        confidence = min(0.5 + (matches * 0.05), 0.85)

        source_id = hashlib.sha256(f"wh_briefing|{url}".encode()).hexdigest()[:16]
        summary = raw.get("summary") or title

        return SourceOpportunity(
            source_id=source_id,
            title=title,
            description=f"[White House Briefing] {summary[:400]}",
            estimated_profit=0.0,
            currency="USD",
            confidence=confidence,
            next_action="Policy-to-Profit Engine: analyze for early-stage opportunity signals",
            origin_module="policy_engine",
            category="Government",
            source_url=url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="whitehouse_briefing",
            signal_type="policy_announcement",
            metadata={"published_at": raw.get("published_at").isoformat() if raw.get("published_at") else None},
        )
