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
    SOURCES_GIG_MAX_RESULTS,
    SOURCES_GIG_QUERIES,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity


class GigScannerAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_GIG_MAX_RESULTS)

    def source_name(self) -> str:
        return "gig_scanner"

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
            remote_ok = client.get("https://remoteok.com/api")
            remote_ok.raise_for_status()
            for row in remote_ok.json():
                if not isinstance(row, dict) or not row.get("id") or not row.get("position"):
                    continue
                row["_feed"] = "remoteok"
                raw_items.append(row)

            remotive = client.get("https://remotive.com/api/remote-jobs")
            remotive.raise_for_status()
            for row in remotive.json().get("jobs", []):
                if not isinstance(row, dict) or not row.get("id") or not row.get("title"):
                    continue
                row["_feed"] = "remotive"
                raw_items.append(row)

            wwr = client.get("https://weworkremotely.com/remote-jobs.rss")
            wwr.raise_for_status()
            root = ET.fromstring(wwr.text)
            for item in root.findall(".//item"):
                raw_items.append(
                    {
                        "_feed": "weworkremotely",
                        "title": unescape((item.findtext("title") or "").strip()),
                        "link": (item.findtext("link") or "").strip(),
                        "pubDate": (item.findtext("pubDate") or "").strip(),
                        "description": unescape((item.findtext("description") or "").strip()),
                    }
                )

            for query in SOURCES_GIG_QUERIES:
                url = (
                    "https://hn.algolia.com/api/v1/search_by_date"
                    f"?tags=job,story&query={quote_plus(query)}&hitsPerPage=6"
                )
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                for hit in payload.get("hits", []):
                    hit["_feed"] = "hackernews_jobs"
                    hit["_query"] = query
                    raw_items.append(hit)

        return raw_items

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        feed = raw.get("_feed")
        if feed == "remoteok":
            return _normalize_remoteok(raw)
        if feed == "remotive":
            return _normalize_remotive(raw)
        if feed == "weworkremotely":
            return _normalize_wwr(raw)
        if feed == "hackernews_jobs":
            return _normalize_hn(raw)
        return None


def _normalize_remoteok(raw: dict[str, Any]) -> SourceOpportunity | None:
    text = " ".join(
        [
            str(raw.get("position") or ""),
            str(raw.get("company") or ""),
            " ".join(raw.get("tags") or []),
            str(raw.get("description") or ""),
        ]
    ).lower()
    if not _matches_gig_signal(text):
        return None

    slug = raw.get("slug")
    job_id = raw.get("id")
    if not slug or not job_id:
        return None

    category = _gig_category(text)
    urgency = _urgency(text)
    confidence = _confidence(text, urgency, remoteok=True)
    estimated_profit = _remoteok_value(raw)
    timestamp = _parse_date(raw.get("date"))
    source_url = f"https://remoteok.com/remote-jobs/{slug}"

    return SourceOpportunity(
        source_id=f"gig:remoteok:{job_id}",
        title=str(raw.get("position") or "").strip(),
        description=f"{raw.get('position')} at {raw.get('company')} [{category}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=f"Review scope, craft a fast {category} pitch, and respond while urgency is {urgency}.",
        origin_module="gig_scanner",
        category=category,
        lane="gig_contract",
        source_url=source_url,
        timestamp=timestamp,
        source_name="remoteok",
        signal_type=urgency,
        metadata={
            "company": raw.get("company"),
            "tags": raw.get("tags", []),
            "salary_min": raw.get("salary_min"),
            "salary_max": raw.get("salary_max"),
        },
    )


def _normalize_hn(raw: dict[str, Any]) -> SourceOpportunity | None:
    object_id = raw.get("objectID")
    title = (raw.get("title") or "").strip()
    body = (raw.get("story_text") or "").strip()
    if not object_id or not title:
        return None

    text = f"{title}\n{body}".lower()
    if not _matches_gig_signal(text):
        return None

    category = _gig_category(text)
    urgency = _urgency(text)
    confidence = _confidence(text, urgency, remoteok=False)
    estimated_profit = _estimated_value(title, body)
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
        source_id=f"gig:hackernews:{object_id}",
        title=title,
        description=title,
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=f"Review scope, craft a fast {category} pitch, and respond while urgency is {urgency}.",
        origin_module="gig_scanner",
        category=category,
        lane="gig_contract",
        source_url=source_url,
        timestamp=timestamp,
        source_name="hackernews_jobs",
        signal_type=urgency,
        metadata={"query": raw.get("_query")},
    )


def _normalize_remotive(raw: dict[str, Any]) -> SourceOpportunity | None:
    title = str(raw.get("title") or "").strip()
    company = str(raw.get("company_name") or "").strip()
    text = " ".join(
        [
            title,
            company,
            str(raw.get("candidate_required_location") or ""),
            str(raw.get("category") or ""),
            str(raw.get("description") or ""),
        ]
    ).lower()
    if not title or not raw.get("id") or not _matches_gig_signal(text):
        return None

    category = _gig_category(text)
    urgency = _urgency(text)
    confidence = _confidence(text, urgency, remoteok=True)
    estimated_profit = _estimated_value(title, str(raw.get("description") or ""))

    return SourceOpportunity(
        source_id=f"gig:remotive:{raw.get('id')}",
        title=title,
        description=f"{title} at {company} [{category}]",
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=f"Review scope, craft a fast {category} pitch, and respond while urgency is {urgency}.",
        origin_module="gig_scanner",
        category=category,
        lane="gig_contract",
        source_url=str(raw.get("url") or ""),
        timestamp=_parse_date(str(raw.get("publication_date") or "")),
        source_name="remotive",
        signal_type=urgency,
        metadata={"company": company, "job_type": raw.get("job_type")},
    )


def _normalize_wwr(raw: dict[str, Any]) -> SourceOpportunity | None:
    title = str(raw.get("title") or "").strip()
    link = str(raw.get("link") or "").strip()
    description = str(raw.get("description") or "")
    text = f"{title} {description}".lower()
    if not title or not link or not _matches_gig_signal(text):
        return None

    category = _gig_category(text)
    urgency = _urgency(text)
    confidence = _confidence(text, urgency, remoteok=False)
    estimated_profit = _estimated_value(title, description)

    return SourceOpportunity(
        source_id=f"gig:wwr:{hashlib.sha1(link.encode('utf-8')).hexdigest()[:12]}",
        title=title,
        description=title,
        estimated_profit=estimated_profit,
        currency="USD",
        confidence=confidence,
        next_action=f"Review scope, craft a fast {category} pitch, and respond while urgency is {urgency}.",
        origin_module="gig_scanner",
        category=category,
        lane="gig_contract",
        source_url=link,
        timestamp=_parse_date(raw.get("pubDate")),
        source_name="weworkremotely",
        signal_type=urgency,
        metadata={},
    )


def _matches_gig_signal(text: str) -> bool:
    return any(token in text for token in SOURCES_GIG_QUERIES) or any(
        token in text
        for token in (
            "contract",
            "freelance",
            "consultant",
            "consulting",
            "automation",
            "agent",
            "integration",
            "scraping",
            "growth",
            "marketing",
        )
    )


def _gig_category(text: str) -> str:
    if any(token in text for token in ("automation", "ai", "agent", "workflow", "python", "integration")):
        return "automation-contract"
    if any(token in text for token in ("sales", "lead", "growth", "marketing", "seo")):
        return "growth-contract"
    if any(token in text for token in ("design", "brand", "video", "content")):
        return "creative-contract"
    if any(token in text for token in ("scrape", "research", "data", "analyst")):
        return "research-contract"
    return "general-contract"


def _urgency(text: str) -> str:
    if any(token in text for token in ("urgent", "asap", "immediately", "this week", "right away")):
        return "high"
    if any(token in text for token in ("soon", "next week", "priority")):
        return "medium"
    return "normal"


def _confidence(text: str, urgency: str, *, remoteok: bool) -> float:
    base = 0.58 if remoteok else 0.54
    if "contract" in text or "freelance" in text or "consult" in text:
        base += 0.08
    if urgency == "high":
        base += 0.08
    elif urgency == "medium":
        base += 0.04
    return round(min(base, 0.9), 2)


def _remoteok_value(raw: dict[str, Any]) -> float:
    salary_min = float(raw.get("salary_min") or 0)
    salary_max = float(raw.get("salary_max") or 0)
    non_zero = [value for value in (salary_min, salary_max) if value > 0]
    if non_zero:
        # RemoteOK salary fields are typically annualized; use a conservative monthly equivalent.
        return round(min(non_zero) / 12.0, 2)
    title = str(raw.get("position") or "")
    description = str(raw.get("description") or "")
    return _estimated_value(title, description)


def _estimated_value(title: str, body: str) -> float:
    text = f"{title}\n{body}"
    cash_matches = re.findall(r"\$([0-9]{2,6})", text.replace(",", ""))
    if cash_matches:
        values = [float(value) for value in cash_matches]
        return round(max(values), 2)

    lower = text.lower()
    if any(token in lower for token in ("senior", "consultant", "fractional")):
        return 800.0
    if any(token in lower for token in ("automation", "python", "scrape", "integration")):
        return 550.0
    if any(token in lower for token in ("sales", "growth", "marketing")):
        return 450.0
    if any(token in lower for token in ("design", "video", "creative")):
        return 300.0
    return 250.0


def _parse_date(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat()
