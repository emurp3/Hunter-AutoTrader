"""
Bounties/competitions discovery via DevPost's public hackathon API.

Distinct from github_scanner.py, which covers per-issue GitHub bounties.
This covers organized competitions/hackathons with cash prize pools —
often much larger single opportunities (five- and six-figure prize pools
are common) with a defined submission deadline, which the capability-fit
and speed-to-cash scoring factors can now weigh appropriately instead of
Hunter having no visibility into this category at all.

DevPost's hackathon listing endpoint is public and requires no API key.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import (
    SOURCE_REQUEST_TIMEOUT_SECONDS,
    SOURCES_BOUNTY_MAX_RESULTS,
    SOURCES_USER_AGENT,
)
from app.services.sources.base import SourceAdapter, SourceOpportunity

_DEVPOST_URL = "https://devpost.com/api/hackathons"
_PRIZE_AMOUNT_RE = re.compile(r"data-currency-value>([\d,]+)<")


class BountyScannerAdapter(SourceAdapter):
    def __init__(self, *, enabled: bool = True, max_records: int | None = None) -> None:
        super().__init__(enabled=enabled, max_records=max_records or SOURCES_BOUNTY_MAX_RESULTS)

    def source_name(self) -> str:
        return "bounty_scanner"

    def fetch_opportunities(self) -> list[dict[str, Any]]:
        headers = {"User-Agent": SOURCES_USER_AGENT, "Accept": "application/json"}
        with httpx.Client(timeout=SOURCE_REQUEST_TIMEOUT_SECONDS, headers=headers, follow_redirects=True) as client:
            resp = client.get(
                _DEVPOST_URL,
                params={"status[]": "open", "per_page": 20, "order_by": "prize-amount"},
            )
            resp.raise_for_status()
            return resp.json().get("hackathons", [])

    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None:
        title = (raw.get("title") or "").strip()
        hackathon_id = raw.get("id")
        url = raw.get("url")
        if not title or not hackathon_id or not url:
            return None

        prize_match = _PRIZE_AMOUNT_RE.search(raw.get("prize_amount") or "")
        total_prize = float(prize_match.group(1).replace(",", "")) if prize_match else 0.0
        if total_prize <= 0:
            return None  # no confirmed cash prize — not a real income opportunity

        cash_prize_count = max(1, (raw.get("prizes_counts") or {}).get("cash", 1))
        # Estimated take if EMurph wins ONE placement, not the whole pool —
        # a rough per-prize average, not the full advertised total.
        estimated_profit = round(total_prize / cash_prize_count, 2)

        registrations = raw.get("registrations_count") or 0
        time_left = raw.get("time_left_to_submission") or "deadline unclear"
        org = raw.get("organization_name") or "Unknown organizer"
        themes = ", ".join(t.get("name", "") for t in (raw.get("themes") or []) if t.get("name"))

        next_action = (
            f"Review rules at {url} — {cash_prize_count} cash prize(s) from a "
            f"${total_prize:,.0f} pool, {registrations:,} teams already registered, "
            f"{time_left}. Realistic only if a working submission is achievable solo "
            f"or with existing tooling before the deadline."
        )

        return SourceOpportunity(
            source_id=f"bounty:devpost:{hackathon_id}",
            title=title,
            description=f"{title} — {org} hackathon ({themes or 'general'})",
            estimated_profit=estimated_profit,
            currency="USD",
            confidence=_estimate_confidence(registrations=registrations, cash_prize_count=cash_prize_count),
            next_action=next_action,
            origin_module="bounty_scanner",
            category="bounty",
            lane="bounties_competitions",
            source_url=url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source_name="devpost",
            signal_type="hackathon_competition",
            metadata={
                "organization": org,
                "total_prize_pool": total_prize,
                "cash_prize_count": cash_prize_count,
                "registrations_count": registrations,
                "time_left_to_submission": time_left,
                "themes": themes,
            },
        )


def _estimate_confidence(*, registrations: int, cash_prize_count: int) -> float:
    """
    Conservative heuristic: more registered teams competing for the same
    prize count lowers realistic odds of placing. Not a measured
    probability — DevPost doesn't publish submission counts (which are
    typically much lower than registration counts), so this floors at a
    level that won't make hackathons look like free money.
    """
    if registrations <= 0:
        return 0.15
    ratio = cash_prize_count / max(registrations, 1)
    if ratio >= 0.05:
        return 0.15
    if ratio >= 0.01:
        return 0.08
    return 0.04
