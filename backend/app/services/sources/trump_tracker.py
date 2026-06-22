"""
TrumpTrackerAdapter — monitors https://www.trumpactiontracker.info/

Extracts implemented actions, announced plans, and executive initiatives
from the Trump Action Tracker and feeds them into the Policy-to-Profit Engine.
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

BASE_URL = "https://www.trumpactiontracker.info/"
_TIMEOUT = 20
_UA = "HunterP2PEngine/1.0 PolicyScanner"


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"s+", " ", text).strip()


def _extract_actions(html: str) -> list[dict[str, Any]]:
    """Extract action entries from Trump Action Tracker HTML."""
    items: list[dict[str, Any]] = []

    # Pattern 1: article/card elements with title + description
    cards = re.findall(
        r'(?:class="[^"]*(?:action|item|card|entry|post)[^"]*"[^>]*>)(.*?)(?=class="[^"]*(?:action|item|card|entry|post)|$)',
        html, re.DOTALL | re.IGNORECASE
    )

    # Pattern 2: h2/h3 headings as action titles
    headings = re.findall(
        r'<h[23][^>]*>s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>s*</h[23]>',
        html, re.IGNORECASE
    )
    for href, title in headings[:30]:
        title = title.strip()
        url = href if href.startswith('http') else f"https://www.trumpactiontracker.info{href}"
        if title and len(title) > 10:
            items.append({"title": title, "url": url, "summary": title, "published_at": None})

    # Pattern 3: list items with action descriptions
    if len(items) < 5:
        li_items = re.findall(r'<li[^>]*>s*<a[^>]+href="([^"]+)"[^>]*>([^<]{20,300})</a>', html, re.IGNORECASE)
        for href, text in li_items[:20]:
            text = text.strip()
            url = href if href.startswith('http') else f"https://www.trumpactiontracker.info{href}"
            if text and len(text) > 15:
                items.append({"title": text[:200], "url": url, "summary": text[:400], "published_at": None})

    # Deduplicate
    seen: set[str] = set()
    unique = []
    for item in items:
        key = item["title"][:80].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique[:20]


class TrumpTrackerAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "trump_tracker"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA, "Accept": "text/html"}) as client:
                resp = client.get(BASE_URL, follow_redirects=True)
                if resp.status_code != 200:
                    logger.warning("trump_tracker: HTTP %d from %s", resp.status_code, BASE_URL)
                    self._health.mark(status="degraded", live=False, notes=f"HTTP {resp.status_code}")
                    return []
                items = _extract_actions(resp.text)
                logger.info("trump_tracker: extracted %d actions", len(items))
                return items
        except Exception as exc:
            logger.warning("trump_tracker: fetch failed — %s", exc)
            self._health.mark(status="error", live=False, error=str(exc))
            return []

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title", "").strip()
        url = raw.get("url", "").strip()
        if not title:
            return None

        source_id = hashlib.sha256(f"trump_tracker|{title[:100]}".encode()).hexdigest()[:16]
        summary = raw.get("summary") or title

        return SourceOpportunity(
            source_id=source_id,
            title=title,
            description=f"[Trump Action Tracker] {summary[:400]}",
            estimated_profit=0.0,
            currency="USD",
            confidence=0.65,
            next_action="Policy-to-Profit Engine: extract revenue opportunities from this action",
            origin_module="policy_engine",
            category="Political Intelligence",
            source_url=url or BASE_URL,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="trump_tracker",
            signal_type="political_action",
            metadata={},
        )
