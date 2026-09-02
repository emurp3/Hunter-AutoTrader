"""
Grants/contracts discovery via the public grants.gov Search2 API.

This replaces a permanent stub that always returned "blocked — no clean
public procurement API/feed confirmed yet." grants.gov's Search2 and
fetchOpportunity endpoints are public, require no API key, and cover the
exact category (federal grants — including cybersecurity/AI programs)
that this scanner previously found nothing in.

Two-step fetch per run:
  1. POST /v1/api/search2 for each configured keyword — cheap, returns
     hitCount + a page of summary hits (id, title, agency, dates).
  2. For the top N most-recently-posted unique hits across all keywords,
     POST /v1/api/fetchOpportunity to get award ceiling/floor and number
     of awards, so estimated_profit reflects real dollar figures instead
     of a guess. Capped by SOURCES_RFP_DETAIL_FETCH_LIMIT to keep each
     scan run fast.

Confidence is deliberately conservative: federal grants are competitive,
multi-applicant awards, not something a next_action can promise. See
_estimate_confidence() for the (documented, heuristic) rationale.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_RFP_DETAIL_FETCH_LIMIT,
    SOURCES_RFP_MAX_RESULTS,
    SOURCES_RFP_QUERIES,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity

_SEARCH_URL = "https://api.grants.gov/v1/api/search2"
_DETAIL_URL = "https://api.grants.gov/v1/api/fetchOpportunity"


class RfpScannerAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_RFP_MAX_RESULTS)
        self._details_by_id: dict[str, dict[str, Any]] = {}

    def source_name(self) -> str:
        return "rfp_scanner"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        headers = {"User-Agent": SOURCES_USER_AGENT, "Content-Type": "application/json"}
        raw_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        with httpx.Client(timeout=SOURCE_REQUEST_TIMEOUT_SECONDS, headers=headers) as client:
            for query in SOURCES_RFP_QUERIES:
                try:
                    resp = client.post(
                        _SEARCH_URL,
                        json={"keyword": query, "rows": 8, "oppStatuses": "forecasted|posted"},
                    )
                    resp.raise_for_status()
                    hits = resp.json().get("data", {}).get("oppHits", [])
                except Exception:
                    continue

                for hit in hits:
                    opp_id = str(hit.get("id") or "")
                    if not opp_id or opp_id in seen_ids:
                        continue
                    seen_ids.add(opp_id)
                    hit["_query"] = query
                    raw_items.append(hit)

            # Sort by open/posting date descending (freshest first) and fetch
            # detail (award amounts) only for the top slice — this is the
            # expensive call, one HTTP round-trip per opportunity.
            raw_items.sort(key=lambda h: h.get("openDate", ""), reverse=True)
            for hit in raw_items[:SOURCES_RFP_DETAIL_FETCH_LIMIT]:
                opp_id = str(hit.get("id"))
                try:
                    detail_resp = client.post(_DETAIL_URL, json={"opportunityId": opp_id})
                    detail_resp.raise_for_status()
                    synopsis = detail_resp.json().get("data", {}).get("synopsis", {})
                    self._details_by_id[opp_id] = synopsis
                except Exception:
                    continue

        return raw_items

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        opp_id = str(raw.get("id") or "")
        title = (raw.get("title") or "").strip()
        if not opp_id or not title:
            return None

        agency = raw.get("agency") or raw.get("agencyCode") or "Unknown agency"
        close_date = raw.get("closeDate") or "no stated deadline"
        query = raw.get("_query", "")

        detail = self._details_by_id.get(opp_id, {})
        award_ceiling = _to_float(detail.get("awardCeiling"))
        award_floor = _to_float(detail.get("awardFloor"))
        num_awards = _to_int(detail.get("numberOfAwards"))

        if award_ceiling:
            estimated_profit = award_ceiling
            amount_note = f"award range ${award_floor:,.0f}\u2013${award_ceiling:,.0f}" if award_floor else f"award ceiling ${award_ceiling:,.0f}"
        else:
            # No detail fetched for this hit (outside the detail-fetch limit)
            # — use a conservative placeholder so it still surfaces for a
            # human/Claude to evaluate, rather than being silently dropped.
            estimated_profit = 25_000.0
            amount_note = "award amount not yet fetched — check listing"

        confidence = _estimate_confidence(num_awards=num_awards)

        next_action = (
            f"Review solicitation on grants.gov (agency: {agency}, {amount_note}, "
            f"{num_awards or '?'} award(s), closes {close_date}). If eligible, assess "
            f"whether a proposal is realistic given deadline and applicant type before committing time."
        )

        return SourceOpportunity(
            source_id=f"grants:{opp_id}",
            title=title,
            description=f"{title} — {agency} (matched query: {query})",
            estimated_profit=round(estimated_profit, 2),
            currency="USD",
            confidence=confidence,
            next_action=next_action,
            origin_module="rfp_scanner",
            category="grant",
            lane="grants_contracts",
            source_url=f"https://www.grants.gov/search-results-detail/{opp_id}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            source_name="grants.gov",
            signal_type="federal_grant",
            metadata={
                "agency": agency,
                "close_date": close_date,
                "award_ceiling": award_ceiling,
                "award_floor": award_floor,
                "number_of_awards": num_awards,
                "matched_query": query,
            },
        )

    def run(self) -> list[SourceOpportunity]:
        if not self._enabled:
            self._health.mark(status="disabled", live=False, notes="Adapter disabled by configuration.")
            return []

        raw_items = self.fetch_opportunities()
        normalized: list[SourceOpportunity] = []
        for raw in raw_items[: self._max_records]:
            item = self.normalize(raw)
            if item:
                normalized.append(item)

        live = len(normalized) > 0
        status = "ok" if live else "degraded"
        notes = None if live else "grants.gov returned no matching opportunities for the configured queries."
        self._health.mark(status=status, live=live, count=len(normalized), notes=notes)
        return normalized


def _estimate_confidence(*, num_awards: int | None) -> float:
    """
    Conservative, documented heuristic for federal grant win probability.
    grants.gov doesn't publish applicant counts, so this can't be a real
    probability estimate -- it's a floor to prevent Hunter from either
    treating grants as free money (confidence=1.0) or completely ignoring
    them (confidence=0.0). More awards available per solicitation modestly
    raises the floor; a single-award solicitation is treated as a long shot.
    """
    if not num_awards or num_awards <= 1:
        return 0.05
    if num_awards <= 5:
        return 0.08
    if num_awards <= 20:
        return 0.10
    return 0.12


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
