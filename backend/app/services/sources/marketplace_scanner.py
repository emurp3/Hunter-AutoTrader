from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_MARKETPLACE_MAX_RESULTS,
    SOURCES_MARKETPLACE_QUERIES,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity


class MarketplaceScannerAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_MARKETPLACE_MAX_RESULTS)

    def source_name(self) -> str:
        return "marketplace_scanner"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        headers = {"User-Agent": SOURCES_USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"}
        raw_items: list[dict[str, Any]] = []

        with httpx.Client(timeout=SOURCE_REQUEST_TIMEOUT_SECONDS, headers=headers, follow_redirects=True) as client:
            for query in SOURCES_MARKETPLACE_QUERIES:
                url = (
                    "https://slickdeals.net/newsearch.php"
                    f"?q={quote_plus(query)}&searcharea=deals&searchin=first&rss=1"
                )
                response = client.get(url)
                response.raise_for_status()
                root = ET.fromstring(response.text)
                for item in root.findall(".//item"):
                    raw_items.append(
                        {
                            "query": query,
                            "title": unescape((item.findtext("title") or "").strip()),
                            "link": (item.findtext("link") or "").strip(),
                            "pubDate": (item.findtext("pubDate") or "").strip(),
                            "description": unescape((item.findtext("description") or "").strip()),
                        }
                    )

        return raw_items

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = raw.get("title") or ""
        link = raw.get("link") or ""
        if not title or not link:
            return None

        price = _extract_price(title, raw.get("description") or "")
        if price is None:
            return None

        query = raw.get("query") or "deal"
        category = _category(query, title.lower())
        estimated_profit = round(price * _markup_factor(category), 2)
        confidence = _confidence(title.lower(), estimated_profit)
        timestamp = _parse_timestamp(raw.get("pubDate"))
        next_action = (
            f"Validate resale comps for {category}, confirm shipping/tax impact, and move fast if spread holds."
        )
        listing_hash = hashlib.sha1(link.encode("utf-8")).hexdigest()[:12]

        return SourceOpportunity(
            source_id=f"marketplace:slickdeals:{listing_hash}",
            title=title,
            description=f"{title} [deal ${price:.0f}]",
            estimated_profit=estimated_profit,
            currency="USD",
            confidence=confidence,
            next_action=next_action,
        origin_module="marketplace_scanner",
        category=category,
        lane="arbitrage_deals",
        source_url=link,
            timestamp=timestamp,
            source_name="slickdeals_rss",
            signal_type="arbitrage",
            metadata={"query": query, "deal_price": price},
        )


def _extract_price(title: str, description: str) -> float | None:
    text = f"{title} {description}"
    match = re.search(r"\$([0-9][0-9,]*(?:\.[0-9]{2})?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _category(query: str, text: str) -> str:
    combined = f"{query} {text}"
    if any(token in combined for token in ("dyson", "vacuum", "airwrap")):
        return "home-goods-flip"
    if any(token in combined for token in ("lego", "pokemon", "nintendo")):
        return "collectible-flip"
    if any(token in combined for token in ("canon", "sony", "camera", "gopro")):
        return "camera-flip"
    if any(token in combined for token in ("dewalt", "milwaukee", "makita", "tool")):
        return "tool-flip"
    if any(token in combined for token in ("apple", "ipad", "iphone", "laptop")):
        return "electronics-flip"
    return "general-flip"


def _markup_factor(category: str) -> float:
    return {
        "home-goods-flip": 0.16,
        "collectible-flip": 0.22,
        "camera-flip": 0.18,
        "tool-flip": 0.19,
        "electronics-flip": 0.14,
        "general-flip": 0.12,
    }.get(category, 0.12)


def _confidence(text: str, estimated_profit: float) -> float:
    base = 0.57
    if any(token in text for token in ("clearance", "best buy", "bundle", "gift card")):
        base += 0.04
    if estimated_profit >= 40:
        base += 0.05
    return round(min(base, 0.82), 2)


def _parse_timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(value, fmt).astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat()
