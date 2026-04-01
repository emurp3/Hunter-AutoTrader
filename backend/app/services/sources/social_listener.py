from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_SOCIAL_MAX_RESULTS,
    SOURCES_SOCIAL_QUERIES,
    SOURCES_SOCIAL_REDDIT_SUBREDDITS,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity


class SocialListenerAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_SOCIAL_MAX_RESULTS)

    def source_name(self) -> str:
        return "social_listener"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": SOURCES_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
        raw_items: list[dict[str, Any]] = []

        with httpx.Client(
            timeout=SOURCE_REQUEST_TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for subreddit in SOURCES_SOCIAL_REDDIT_SUBREDDITS:
                for query in SOURCES_SOCIAL_QUERIES:
                    url = (
                        f"https://www.reddit.com/r/{subreddit}/search.json"
                        f"?q={quote_plus(query)}&restrict_sr=1&sort=new&limit=4&raw_json=1"
                    )
                    try:
                        response = client.get(url)
                        response.raise_for_status()
                        payload = response.json()
                    except httpx.HTTPError:
                        continue
                    for child in payload.get("data", {}).get("children", []):
                        data = child.get("data", {})
                        data["_feed"] = "reddit"
                        data["_subreddit"] = subreddit
                        data["_query"] = query
                        raw_items.append(data)

            for query in SOURCES_SOCIAL_QUERIES:
                url = (
                    "https://hn.algolia.com/api/v1/search_by_date"
                    f"?query={quote_plus(query)}&tags=story&hitsPerPage=6"
                )
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPError:
                    continue
                for hit in payload.get("hits", []):
                    hit["_feed"] = "hackernews"
                    hit["_query"] = query
                    raw_items.append(hit)

            for query in SOURCES_SOCIAL_QUERIES:
                url = (
                    "https://api.stackexchange.com/2.3/search/advanced"
                    f"?order=desc&sort=creation&q={quote_plus(query)}&site=stackoverflow&pagesize=4"
                )
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPError:
                    continue
                for item in payload.get("items", []):
                    item["_feed"] = "stackexchange"
                    item["_query"] = query
                    raw_items.append(item)

        return raw_items

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        feed = raw.get("_feed")
        if feed == "reddit":
            return _normalize_reddit(raw)
        if feed == "hackernews":
            return _normalize_hn(raw)
        if feed == "stackexchange":
            return _normalize_stackexchange(raw)
        return None


def _normalize_reddit(raw: dict[str, Any]) -> SourceOpportunity | None:
    post_id = raw.get("id")
    title = (raw.get("title") or "").strip()
    body = (raw.get("selftext") or "").strip()
    if not post_id or not title:
        return None

    text = f"{title}\n{body}".lower()
    signal_type = _signal_type(text)
    if signal_type is None:
        return None

    category = _category(text)
    confidence = _confidence(text, comments=raw.get("num_comments", 0), score=raw.get("score", 0))
    estimated_profit = _estimated_profit(category, signal_type)
    permalink = raw.get("permalink") or ""
    source_url = f"https://www.reddit.com{permalink}" if permalink else "https://www.reddit.com/"
    created_utc = raw.get("created_utc")
    if created_utc:
        timestamp = datetime.fromtimestamp(float(created_utc), tz=timezone.utc).isoformat()
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    description = f"{title} [signal={signal_type}]"
    next_action = _next_action(signal_type, category)

    return SourceOpportunity(
        source_id=f"social:reddit:{raw.get('_subreddit', 'reddit')}:{post_id}",
        title=title,
        description=description,
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=next_action,
        origin_module="social_listener",
        category=category,
        lane="social_pain_point",
        source_url=source_url,
        timestamp=timestamp,
        source_name="reddit",
        signal_type=signal_type,
        metadata={
            "subreddit": raw.get("_subreddit"),
            "query": raw.get("_query"),
            "comments": raw.get("num_comments", 0),
            "score": raw.get("score", 0),
        },
    )


def _normalize_hn(raw: dict[str, Any]) -> SourceOpportunity | None:
    object_id = raw.get("objectID")
    title = (raw.get("title") or "").strip()
    body = (raw.get("story_text") or raw.get("comment_text") or "").strip()
    if not object_id or not title:
        return None

    text = f"{title}\n{body}".lower()
    signal_type = _signal_type(text)
    if signal_type is None:
        return None

    category = _category(text)
    confidence = _confidence(text, comments=raw.get("num_comments", 0), score=raw.get("points", 0))
    estimated_profit = _estimated_profit(category, signal_type)
    source_url = raw.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
    created_at = raw.get("created_at")
    try:
        timestamp = (
            datetime.fromisoformat(created_at.replace("Z", "+00:00")).isoformat()
            if created_at
            else datetime.now(timezone.utc).isoformat()
        )
    except ValueError:
        timestamp = datetime.now(timezone.utc).isoformat()

    return SourceOpportunity(
        source_id=f"social:hackernews:{object_id}",
        title=title,
        description=f"{title} [signal={signal_type}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=_next_action(signal_type, category),
        origin_module="social_listener",
        category=category,
        lane="social_pain_point",
        source_url=source_url,
        timestamp=timestamp,
        source_name="hackernews_discussions",
        signal_type=signal_type,
        metadata={
            "query": raw.get("_query"),
            "points": raw.get("points", 0),
            "comments": raw.get("num_comments", 0),
        },
    )


def _normalize_stackexchange(raw: dict[str, Any]) -> SourceOpportunity | None:
    question_id = raw.get("question_id")
    title = (raw.get("title") or "").strip()
    link = raw.get("link") or ""
    if not question_id or not title or not link:
        return None

    text = title.lower()
    signal_type = _signal_type(text)
    if signal_type is None:
        return None

    category = _category(text)
    confidence = _confidence(text, comments=raw.get("answer_count", 0), score=raw.get("score", 0))
    estimated_profit = _estimated_profit(category, signal_type)

    return SourceOpportunity(
        source_id=f"social:stackexchange:{question_id}",
        title=title,
        description=f"{title} [signal={signal_type}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=_next_action(signal_type, category),
        origin_module="social_listener",
        category=category,
        lane="social_pain_point",
        source_url=link,
        timestamp=datetime.fromtimestamp(raw.get("creation_date", 0), tz=timezone.utc).isoformat()
        if raw.get("creation_date")
        else datetime.now(timezone.utc).isoformat(),
        source_name="stackexchange",
        signal_type=signal_type,
        metadata={"query": raw.get("_query"), "answers": raw.get("answer_count", 0)},
    )


def _signal_type(text: str) -> str | None:
    if any(token in text for token in ("looking for", "need help", "need someone", "can anyone", "who can")):
        return "request_for_help"
    if any(token in text for token in ("frustrated", "hate that", "annoyed", "problem with", "broken", "issue with")):
        return "pain_point"
    if any(token in text for token in ("recommend", "what tool", "best way", "any service", "willing to pay")):
        return "buying_intent"
    if any(token in text for token in ("keep seeing", "everyone asks", "common problem", "recurring issue")):
        return "repeated_complaint"
    return None


def _category(text: str) -> str:
    if any(token in text for token in ("lead", "prospect", "appointment", "outreach")):
        return "lead-generation"
    if any(token in text for token in ("website", "seo", "landing page", "shopify")):
        return "web-services"
    if any(token in text for token in ("automation", "spreadsheet", "zapier", "crm", "workflow", "agent")):
        return "automation-service"
    if any(token in text for token in ("design", "video", "branding", "logo")):
        return "creative-service"
    return "service-opportunity"


def _confidence(text: str, *, comments: int, score: int) -> float:
    base = 0.52
    if "need help" in text or "looking for" in text or "who can" in text:
        base += 0.12
    if comments >= 5:
        base += 0.05
    if score >= 3:
        base += 0.05
    return round(min(base, 0.92), 2)


def _estimated_profit(category: str, signal_type: str) -> float:
    category_base = {
        "lead-generation": 260.0,
        "web-services": 320.0,
        "automation-service": 420.0,
        "creative-service": 240.0,
        "service-opportunity": 180.0,
    }
    signal_bonus = {
        "request_for_help": 90.0,
        "buying_intent": 70.0,
        "pain_point": 45.0,
        "repeated_complaint": 35.0,
    }
    return round(category_base.get(category, 180.0) + signal_bonus.get(signal_type, 0.0), 2)


def _next_action(signal_type: str, category: str) -> str:
    if signal_type == "request_for_help":
        return f"Reply or reach out with a concise {category} offer and proof of execution."
    if signal_type == "buying_intent":
        return f"Package a paid {category} offer and validate willingness-to-pay."
    if signal_type == "pain_point":
        return f"Draft a lightweight {category} solution and test outbound messaging."
    return f"Track repeated demand and turn it into a {category} service offer."
