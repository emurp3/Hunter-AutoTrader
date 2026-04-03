"""
Apollo.io lead intelligence adapter.

Provides people search, company enrichment, and contact data.
All calls are gated on APOLLO_API_KEY being set.

Docs: https://apolloio.github.io/apollo-api-docs/
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import APOLLO_API_KEY, APOLLO_BASE_URL

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class ApolloAdapter:
    """Thin adapter for Apollo.io REST API."""

    def __init__(self, api_key: str, base_url: str = APOLLO_BASE_URL):
        if not api_key:
            raise EnvironmentError(
                "APOLLO_API_KEY is not set. Add it to .env to enable lead intelligence."
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self._api_key,
        }

    def search_people(
        self,
        *,
        q_keywords: Optional[str] = None,
        titles: Optional[list[str]] = None,
        organization_domains: Optional[list[str]] = None,
        page: int = 1,
        per_page: int = 10,
    ) -> dict:
        """Search for people matching criteria. Returns raw Apollo response."""
        payload = {
            "api_key": self._api_key,
            "page": page,
            "per_page": per_page,
        }
        if q_keywords:
            payload["q_keywords"] = q_keywords
        if titles:
            payload["person_titles"] = titles
        if organization_domains:
            payload["organization_domains"] = organization_domains

        resp = httpx.post(
            f"{self._base_url}/mixed_people/search",
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def enrich_person(self, *, email: str) -> dict:
        """Enrich a single contact by email address."""
        resp = httpx.post(
            f"{self._base_url}/people/match",
            json={"api_key": self._api_key, "email": email},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def enrich_organization(self, *, domain: str) -> dict:
        """Enrich a company by domain."""
        resp = httpx.post(
            f"{self._base_url}/organizations/enrich",
            json={"api_key": self._api_key, "domain": domain},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def connectivity_check(self) -> dict:
        """Verify Apollo API key is accepted."""
        try:
            result = self.search_people(q_keywords="test", per_page=1)
            return {
                "connected": True,
                "provider": "apollo",
                "people_in_response": len(result.get("people", [])),
            }
        except Exception as exc:
            return {
                "connected": False,
                "provider": "apollo",
                "error": f"{exc.__class__.__name__}: {exc}",
            }


def get_apollo_adapter() -> ApolloAdapter:
    if not APOLLO_API_KEY:
        raise EnvironmentError(
            "APOLLO_API_KEY is not set. Set it in .env to enable Apollo lead intelligence."
        )
    return ApolloAdapter(api_key=APOLLO_API_KEY, base_url=APOLLO_BASE_URL)
