from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_DIGITAL_MAX_RESULTS,
    SOURCES_DIGITAL_QUERIES,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity


class DigitalProductGapAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_DIGITAL_MAX_RESULTS)

    def source_name(self) -> str:
        return "digital_product_scanner"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        headers = {"User-Agent": SOURCES_USER_AGENT, "Accept": "application/json"}
        raw_items: list[dict[str, Any]] = []

        with httpx.Client(timeout=SOURCE_REQUEST_TIMEOUT_SECONDS, headers=headers, follow_redirects=True) as client:
            for query in SOURCES_DIGITAL_QUERIES:
                stack_url = (
                    "https://api.stackexchange.com/2.3/search/advanced"
                    f"?order=desc&sort=creation&q={quote_plus(query)}&site=stackoverflow&pagesize=5"
                )
                stack = client.get(stack_url)
                stack.raise_for_status()
                for item in stack.json().get("items", []):
                    item["_feed"] = "stackexchange"
                    item["_query"] = query
                    raw_items.append(item)

                hn_url = (
                    "https://hn.algolia.com/api/v1/search_by_date"
                    f"?query={quote_plus(query)}&tags=story&hitsPerPage=4"
                )
                hn = client.get(hn_url)
                hn.raise_for_status()
                for hit in hn.json().get("hits", []):
                    hit["_feed"] = "hackernews"
                    hit["_query"] = query
                    raw_items.append(hit)

        return raw_items

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        if raw.get("_feed") == "stackexchange":
            return _normalize_stackexchange(raw)
        if raw.get("_feed") == "hackernews":
            return _normalize_hn(raw)
        return None


def _normalize_stackexchange(raw: dict[str, Any]) -> SourceOpportunity | None:
    question_id = raw.get("question_id")
    title = str(raw.get("title") or "").strip()
    link = str(raw.get("link") or "").strip()
    if not question_id or not title or not link:
        return None

    text = title.lower()
    category = _category(text)
    signal_type = _signal_type(text)
    confidence = _confidence(raw.get("answer_count", 0), raw.get("score", 0))
    estimated_profit = _estimated_profit(category)

    return SourceOpportunity(
        source_id=f"digital:stackexchange:{question_id}",
        title=title,
        description=f"{title} [signal={signal_type}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=_next_action(category),
        origin_module="digital_product_scanner",
        category=category,
        lane="digital_product_gap",
        source_url=link,
        timestamp=_parse_epoch(raw.get("creation_date")),
        source_name="stackexchange",
        signal_type=signal_type,
        metadata={"query": raw.get("_query"), "answers": raw.get("answer_count", 0)},
    )


def _normalize_hn(raw: dict[str, Any]) -> SourceOpportunity | None:
    object_id = raw.get("objectID")
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("story_text") or "").strip()
    if not object_id or not title:
        return None

    text = f"{title} {body}".lower()
    if not any(token in text for token in ("template", "dashboard", "prompt", "tracker", "kit", "form")):
        return None

    category = _category(text)
    signal_type = _signal_type(text)
    confidence = _confidence(raw.get("num_comments", 0), raw.get("points", 0))
    estimated_profit = _estimated_profit(category)

    return SourceOpportunity(
        source_id=f"digital:hackernews:{object_id}",
        title=title,
        description=f"{title} [signal={signal_type}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=_next_action(category),
        origin_module="digital_product_scanner",
        category=category,
        lane="digital_product_gap",
        source_url=raw.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
        timestamp=_parse_iso(raw.get("created_at")),
        source_name="hackernews_discussions",
        signal_type=signal_type,
        metadata={"query": raw.get("_query"), "comments": raw.get("num_comments", 0)},
    )


def _category(text: str) -> str:
    if any(token in text for token in ("church", "ministry", "sermon")):
        return "church-digital-kit"
    if any(token in text for token in ("artist", "press kit", "music", "epk")):
        return "artist-promo-kit"
    if any(token in text for token in ("patient", "healthcare", "clinic")):
        return "healthcare-template"
    if any(token in text for token in ("dashboard", "spreadsheet", "tracker")):
        return "dashboard-template"
    if any(token in text for token in ("prompt", "agent", "workflow")):
        return "automation-prompt-pack"
    return "digital-product-opportunity"


def _signal_type(text: str) -> str:
    if any(token in text for token in ("template", "kit", "dashboard", "tracker", "form")):
        return "template_demand"
    if any(token in text for token in ("prompt", "workflow", "agent")):
        return "prompt_demand"
    return "digital_gap"


def _confidence(answer_count: int, score: int) -> float:
    base = 0.57
    if answer_count == 0:
        base += 0.07
    if score >= 1:
        base += 0.04
    return round(min(base, 0.82), 2)


def _estimated_profit(category: str) -> float:
    return {
        "church-digital-kit": 140.0,
        "artist-promo-kit": 180.0,
        "healthcare-template": 240.0,
        "dashboard-template": 220.0,
        "automation-prompt-pack": 260.0,
        "digital-product-opportunity": 160.0,
    }.get(category, 160.0)


def _next_action(category: str) -> str:
    return f"Package a lightweight {category} offer and validate demand with a fast landing page or outreach test."


def _parse_epoch(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()
