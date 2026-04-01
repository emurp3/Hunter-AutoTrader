from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_GITHUB_ISSUE_QUERIES,
    SOURCES_GITHUB_MAX_RESULTS,
    SOURCES_GITHUB_REPO_QUERIES,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity


class GitHubScannerAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_GITHUB_MAX_RESULTS)

    def source_name(self) -> str:
        return "github_scanner"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": SOURCES_USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repo_items: list[dict[str, Any]] = []
        issue_items: list[dict[str, Any]] = []

        with httpx.Client(
            timeout=SOURCE_REQUEST_TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for query in SOURCES_GITHUB_REPO_QUERIES:
                url = (
                    "https://api.github.com/search/repositories"
                    f"?q={quote_plus(query)}&sort=updated&order=desc&per_page=6"
                )
                response = client.get(url)
                response.raise_for_status()
                for item in response.json().get("items", []):
                    item["_feed"] = "repo"
                    item["_query"] = query
                    repo_items.append(item)

            for query in SOURCES_GITHUB_ISSUE_QUERIES:
                url = (
                    "https://api.github.com/search/issues"
                    f"?q={quote_plus(query)}&sort=updated&order=desc&per_page=6"
                )
                response = client.get(url)
                response.raise_for_status()
                for item in response.json().get("items", []):
                    item["_feed"] = "issue"
                    item["_query"] = query
                    issue_items.append(item)

        combined: list[dict[str, Any]] = []
        max_len = max(len(repo_items), len(issue_items))
        for idx in range(max_len):
            if idx < len(repo_items):
                combined.append(repo_items[idx])
            if idx < len(issue_items):
                combined.append(issue_items[idx])

        return combined

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        feed = raw.get("_feed")
        if feed == "repo":
            return _normalize_repo(raw)
        if feed == "issue":
            return _normalize_issue(raw)
        return None


def _normalize_repo(raw: dict[str, Any]) -> SourceOpportunity | None:
    full_name = raw.get("full_name")
    repo_url = raw.get("html_url")
    description = (raw.get("description") or "").strip()
    if not full_name or not repo_url:
        return None

    signal_type = _repo_signal_type(description.lower(), raw.get("stargazers_count", 0))
    category = _repo_category(full_name.lower(), description.lower())
    confidence = _repo_confidence(raw.get("stargazers_count", 0), raw.get("forks_count", 0))
    estimated_profit = _repo_value(category, raw.get("stargazers_count", 0))
    updated_at = raw.get("updated_at")
    timestamp = _parse_timestamp(updated_at)
    summary = description or f"{full_name} shows traction and monetizable implementation potential."

    return SourceOpportunity(
        source_id=f"github:repo:{full_name.lower()}",
        title=full_name,
        description=f"{summary} [signal={signal_type}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=_repo_next_action(signal_type, category),
        origin_module="github_scanner",
        category=category,
        lane="open_source_monetization",
        source_url=repo_url,
        timestamp=timestamp,
        source_name="github_repositories",
        signal_type=signal_type,
        metadata={
            "query": raw.get("_query"),
            "stars": raw.get("stargazers_count", 0),
            "forks": raw.get("forks_count", 0),
            "language": raw.get("language"),
        },
    )


def _normalize_issue(raw: dict[str, Any]) -> SourceOpportunity | None:
    issue_id = raw.get("id")
    title = (raw.get("title") or "").strip()
    html_url = raw.get("html_url")
    body = (raw.get("body") or "").strip()
    if not issue_id or not title or not html_url:
        return None

    text = f"{title}\n{body}".lower()
    signal_type = _issue_signal_type(text)
    category = _repo_category(str(raw.get("repository_url") or "").lower(), text)
    confidence = _issue_confidence(text, raw.get("comments", 0))
    estimated_profit = _issue_value(signal_type, category)

    return SourceOpportunity(
        source_id=f"github:issue:{issue_id}",
        title=title,
        description=f"{title} [signal={signal_type}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=_issue_next_action(signal_type, category),
        origin_module="github_scanner",
        category=category,
        lane="open_source_monetization",
        source_url=html_url,
        timestamp=_parse_timestamp(raw.get("updated_at")),
        source_name="github_issues",
        signal_type=signal_type,
        metadata={
            "query": raw.get("_query"),
            "comments": raw.get("comments", 0),
            "repository_url": raw.get("repository_url"),
        },
    )


def _repo_signal_type(description: str, stars: int) -> str:
    if stars >= 200:
        return "trend_signal"
    if any(token in description for token in ("open-source", "framework", "platform", "toolkit")):
        return "monetizable_repo"
    return "consulting_setup_demand"


def _issue_signal_type(text: str) -> str:
    if any(token in text for token in ("feature request", "missing", "support", "doesn't work", "bug")):
        return "feature_gap"
    if any(token in text for token in ("need help", "setup", "how do i", "how to", "implementation")):
        return "support_demand"
    return "consulting_setup_demand"


def _repo_category(name: str, text: str) -> str:
    combined = f"{name} {text}"
    if any(token in combined for token in ("agent", "autonomous", "workflow", "automation")):
        return "automation-platform"
    if any(token in combined for token in ("integration", "connector", "sync", "api")):
        return "integration-service"
    if any(token in combined for token in ("local business", "restaurant", "shopify", "commerce")):
        return "business-ops-tooling"
    if any(token in combined for token in ("ai", "llm", "rag", "chatbot")):
        return "ai-tooling"
    return "developer-tooling"


def _repo_confidence(stars: int, forks: int) -> float:
    base = 0.62
    if stars >= 100:
        base += 0.08
    if forks >= 20:
        base += 0.04
    return round(min(base, 0.9), 2)


def _issue_confidence(text: str, comments: int) -> float:
    base = 0.61
    if comments >= 2:
        base += 0.05
    if "need help" in text or "how do i" in text or "implementation" in text:
        base += 0.05
    return round(min(base, 0.88), 2)


def _repo_value(category: str, stars: int) -> float:
    base = {
        "automation-platform": 700.0,
        "integration-service": 620.0,
        "business-ops-tooling": 560.0,
        "ai-tooling": 680.0,
        "developer-tooling": 480.0,
    }.get(category, 480.0)
    if stars >= 500:
        base += 160.0
    elif stars >= 100:
        base += 80.0
    return round(base, 2)


def _issue_value(signal_type: str, category: str) -> float:
    base = {
        "feature_gap": 420.0,
        "support_demand": 360.0,
        "consulting_setup_demand": 440.0,
    }.get(signal_type, 360.0)
    category_bonus = {
        "automation-platform": 120.0,
        "integration-service": 100.0,
        "business-ops-tooling": 90.0,
        "ai-tooling": 110.0,
        "developer-tooling": 70.0,
    }.get(category, 70.0)
    return round(base + category_bonus, 2)


def _repo_next_action(signal_type: str, category: str) -> str:
    if signal_type == "trend_signal":
        return f"Package a fast {category} implementation offer around this fast-moving repo."
    if signal_type == "monetizable_repo":
        return f"Turn this repo into a paid {category} setup, support, or integration offer."
    return f"Offer implementation help, setup, and retained support for this {category} project."


def _issue_next_action(signal_type: str, category: str) -> str:
    if signal_type == "feature_gap":
        return f"Propose a paid fix or implementation sprint for this {category} gap."
    if signal_type == "support_demand":
        return f"Offer setup, debugging, or integration help for this {category} issue."
    return f"Offer scoped consulting and delivery support around this {category} request."


def _parse_timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()
