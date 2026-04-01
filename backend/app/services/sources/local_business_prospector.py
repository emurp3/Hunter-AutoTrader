from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_LOCAL_BBOX,
    SOURCES_LOCAL_BUSINESS_TYPES,
    SOURCES_LOCAL_MAX_RESULTS,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity


class LocalBusinessProspectorAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_LOCAL_MAX_RESULTS)

    def source_name(self) -> str:
        return "local_business_prospector"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        south, west, north, east = [part.strip() for part in SOURCES_LOCAL_BBOX.split(",")]
        clauses = "\n".join(
            [
                f'node["amenity"="{business_type}"]({south},{west},{north},{east});'
                for business_type in SOURCES_LOCAL_BUSINESS_TYPES
            ]
        )
        query = f"""
[out:json][timeout:25];
(
{clauses}
);
out tags 30;
""".strip()

        headers = {"User-Agent": SOURCES_USER_AGENT, "Accept": "application/json"}
        with httpx.Client(timeout=SOURCE_REQUEST_TIMEOUT_SECONDS, headers=headers, follow_redirects=True) as client:
            response = client.get("https://overpass-api.de/api/interpreter", params={"data": query})
            response.raise_for_status()
            return response.json().get("elements", [])

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        tags = raw.get("tags", {})
        business_type = tags.get("amenity")
        name = (tags.get("name") or "").strip()
        if not business_type or not name:
            return None

        website = tags.get("website") or tags.get("contact:website")
        phone = tags.get("phone") or tags.get("contact:phone")
        email = tags.get("email") or tags.get("contact:email")

        gap_reasons: list[str] = []
        if not website:
            gap_reasons.append("missing website")
        if not email:
            gap_reasons.append("missing email")
        if not phone:
            gap_reasons.append("missing phone")
        if not gap_reasons:
            return None

        category = _category(business_type)
        confidence = _confidence(gap_reasons)
        estimated_profit = _estimated_profit(category, gap_reasons)
        node_id = raw.get("id")
        if not node_id:
            return None

        source_url = f"https://www.openstreetmap.org/node/{node_id}"
        description = f"{name} shows public listing gaps: {', '.join(gap_reasons)}."

        return SourceOpportunity(
            source_id=f"local:osm:{business_type}:{node_id}",
            title=name,
            description=description,
            estimated_profit=estimated_profit,
            currency="USD",
            confidence=confidence,
            next_action=_next_action(category, gap_reasons),
            origin_module="local_business_prospector",
            category=category,
            lane="local_business_prospecting",
            source_url=source_url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source_name="openstreetmap_overpass",
            signal_type="prospect_gap",
            metadata={
                "amenity": business_type,
                "gap_reasons": gap_reasons,
                "address": tags.get("addr:full") or tags.get("addr:street"),
                "city": tags.get("addr:city"),
            },
        )


def _category(business_type: str) -> str:
    if business_type in {"dentist", "clinic"}:
        return "healthcare-implementation"
    if business_type == "church":
        return "church-ministry-support"
    if business_type == "music_school":
        return "artist-media-support"
    return "local-business-automation"


def _confidence(gap_reasons: list[str]) -> float:
    base = 0.58
    if "missing website" in gap_reasons:
        base += 0.08
    if len(gap_reasons) >= 2:
        base += 0.05
    return round(min(base, 0.85), 2)


def _estimated_profit(category: str, gap_reasons: list[str]) -> float:
    base = {
        "healthcare-implementation": 850.0,
        "church-ministry-support": 420.0,
        "artist-media-support": 360.0,
        "local-business-automation": 520.0,
    }.get(category, 400.0)
    if "missing website" in gap_reasons:
        base += 120.0
    if "missing email" in gap_reasons:
        base += 40.0
    return round(base, 2)


def _next_action(category: str, gap_reasons: list[str]) -> str:
    gap_summary = ", ".join(gap_reasons)
    return f"Prepare a short {category} outreach offer focused on {gap_summary} and a quick-win upgrade path."
