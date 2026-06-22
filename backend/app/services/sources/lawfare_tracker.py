"""
LawfareAdapter — tracks Trump Administration litigation from Lawfare Media.

Detects injunctions, court challenges, and legal delays that affect
policy implementation and create consulting/compliance opportunities.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.sources.base import SourceAdapter, SourceOpportunity

logger = logging.getLogger(__name__)

TRACKER_URL = "https://www.lawfaremedia.org/projects-series/trials-of-the-trump-administration/tracking-trump-administration-litigation"
RSS_URL = "https://www.lawfaremedia.org/feed"
_TIMEOUT = 20
_UA = "HunterP2PEngine/1.0 PolicyScanner"

_BUSINESS_KEYWORDS = {
    "injunction", "stay", "block", "pause", "halt", "delay", "override",
    "compliance", "enforcement", "deferred", "settlement", "ruling",
    "court", "appeal", "circuit", "district",
}


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class LawfareAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "lawfare_litigation"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = client.get(RSS_URL, follow_redirects=True)
                if resp.status_code == 200 and "<item" in resp.text:
                    items = self._parse_rss(resp.text)
                    items = [
                        i for i in items
                        if any(kw in (i.get("title", "") + i.get("summary", "")).lower()
                               for kw in {"trump", "administration", "court", "lawsuit", "litigation", "injunction"})
                    ]
                    if items:
                        logger.info("lawfare: %d items from RSS", len(items))
                        return items[:15]
        except Exception as exc:
            logger.debug("lawfare: RSS failed — %s", exc)
        return self._scrape_tracker()

    def _parse_rss(self, xml: str) -> list[dict[str, Any]]:
        items = []
        for raw in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
            title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", raw, re.DOTALL)
            link_m = re.search(r"<link>(.*?)</link>", raw)
            desc_m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>", raw, re.DOTALL)
            title = next((g for g in (title_m.groups() if title_m else []) if g), "").strip()
            link = (link_m.group(1) if link_m else "").strip()
            desc = _strip_tags(next((g for g in (desc_m.groups() if desc_m else []) if g), "")).strip()
            if title and link:
                items.append({"title": title, "url": link, "summary": desc[:400] or title, "published_at": None})
        return items

    def _scrape_tracker(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = client.get(TRACKER_URL, follow_redirects=True)
                if resp.status_code != 200:
                    return []
                rows = re.findall(
                    r'<(?:td|li|p|div)[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]{20,300})</a>',
                    resp.text, re.IGNORECASE
                )
                items = []
                seen: set[str] = set()
                for href, text in rows[:30]:
                    text = text.strip()
                    url = href if href.startswith("http") else f"https://www.lawfaremedia.org{href}"
                    key = text[:60].lower()
                    if text and len(text) > 15 and key not in seen:
                        seen.add(key)
                        items.append({"title": text[:200], "url": url, "summary": text[:400], "published_at": None})
                logger.info("lawfare: scraped %d items from tracker page", len(items))
                return items
        except Exception as exc:
            logger.warning("lawfare: scrape failed — %s", exc)
            return []

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title", "").strip()
        url = raw.get("url", "").strip()
        if not title:
            return None

        title_lower = title.lower()
        matches = sum(1 for kw in _BUSINESS_KEYWORDS if kw in title_lower)
        confidence = min(0.55 + (matches * 0.05), 0.80)

        source_id = hashlib.sha256(f"lawfare|{title[:100]}".encode()).hexdigest()[:16]
        summary = raw.get("summary") or title

        return SourceOpportunity(
            source_id=source_id,
            title=title[:200],
            description=f"[Lawfare Litigation Tracker] {summary[:400]}",
            estimated_profit=0.0,
            currency="USD",
            confidence=confidence,
            next_action="Policy-to-Profit Engine: identify compliance risk and legal consulting opportunities",
            origin_module="policy_engine",
            category="Legal Intelligence",
            source_url=url or TRACKER_URL,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="lawfare_litigation",
            signal_type="litigation_event",
            metadata={},
        )
