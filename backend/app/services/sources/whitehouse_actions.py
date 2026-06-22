"""
WhitehouseActionsAdapter — Presidential Actions source for the Policy-to-Profit Engine.

Primary: RSS feed at https://www.whitehouse.gov/presidential-actions/feed/
Fallback: HTML scrape of the listing page

Captures Executive Orders, Memoranda, Proclamations, and other official directives.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.services.sources.base import SourceAdapter, SourceOpportunity

logger = logging.getLogger(__name__)

RSS_URL = "https://www.whitehouse.gov/presidential-actions/feed/"
FALLBACK_URL = "https://www.whitehouse.gov/presidential-actions/"
_TIMEOUT = 15
_UA = "HunterP2PEngine/1.0 PolicyScanner"


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _parse_rss(xml: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        title_m = re.search(r"<title><![CDATA[(.*?)]]></title>|<title>(.*?)</title>", raw, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>|<guid[^>]*>(.*?)</guid>", raw, re.DOTALL)
        desc_m = re.search(r"<description><![CDATA[(.*?)]]></description>|<description>(.*?)</description>", raw, re.DOTALL)
        date_m = re.search(r"<pubDate>(.*?)</pubDate>", raw, re.DOTALL)

        title = next((g for g in (title_m.groups() if title_m else []) if g), "").strip()
        link = next((g for g in (link_m.groups() if link_m else []) if g), "").strip()
        desc = _strip_tags(next((g for g in (desc_m.groups() if desc_m else []) if g), "")).strip()
        pub_raw = next((g for g in (date_m.groups() if date_m else []) if g), None)

        pub_dt: datetime | None = None
        if pub_raw:
            try:
                pub_dt = parsedate_to_datetime(pub_raw.strip())
            except Exception:
                pass

        if title and link:
            items.append({"title": title, "url": link, "summary": desc or title, "published_at": pub_dt})
    return items


class WhitehouseActionsAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "whitehouse_actions"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = client.get(RSS_URL, follow_redirects=True)
                if resp.status_code == 200 and "<rss" in resp.text[:500] or "<feed" in resp.text[:500]:
                    items = _parse_rss(resp.text)
                    if items:
                        self._health.notes = f"RSS OK — {len(items)} items"
                        return items
        except Exception as exc:
            logger.warning("whitehouse_actions: RSS failed — %s", exc)

        # Fallback: HTML scrape
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
                resp = client.get(FALLBACK_URL, follow_redirects=True)
                links = re.findall(
                    r'<a[^>]+href="(/presidential-actions/d{4}/d{2}/[^"]+)"[^>]*>([^<]+)</a>',
                    resp.text
                )
                items = [
                    {"title": title.strip(), "url": f"https://www.whitehouse.gov{href}", "summary": title.strip(), "published_at": None}
                    for href, title in links
                    if title.strip() and len(title.strip()) > 5
                ]
                return items[:20]
        except Exception as exc:
            logger.warning("whitehouse_actions: HTML fallback failed — %s", exc)
            return []

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title", "").strip()
        url = raw.get("url", "").strip()
        if not title or not url:
            return None
        summary = raw.get("summary") or title

        import hashlib
        from datetime import timezone
        source_id = hashlib.sha256(f"wh_actions|{url}".encode()).hexdigest()[:16]

        return SourceOpportunity(
            source_id=source_id,
            title=title,
            description=f"[White House Presidential Action] {summary[:400]}",
            estimated_profit=0.0,
            currency="USD",
            confidence=0.7,
            next_action="Policy-to-Profit Engine: analyze and generate revenue opportunities",
            origin_module="policy_engine",
            category="Government",
            source_url=url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lane="policy",
            source_name="whitehouse_actions",
            signal_type="executive_action",
            metadata={"published_at": raw.get("published_at").isoformat() if raw.get("published_at") else None},
        )
