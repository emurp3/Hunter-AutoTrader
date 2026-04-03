"""
Common Room community signal intelligence adapter.

Surfaces buying signals, community engagement, and member activity
from Slack, Discord, GitHub, Reddit, and other monitored communities.

Docs: https://docs.commonroom.io/api
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import COMMONROOM_API_KEY, COMMONROOM_BASE_URL

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class CommonRoomAdapter:
    """Thin adapter for Common Room REST API."""

    def __init__(self, api_key: str, base_url: str = COMMONROOM_BASE_URL):
        if not api_key:
            raise EnvironmentError(
                "COMMONROOM_API_KEY is not set. Add it to .env to enable community signals."
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def get_members(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        segment_id: Optional[str] = None,
    ) -> dict:
        """List community members, optionally filtered by segment."""
        params: dict = {"limit": limit, "offset": offset}
        if segment_id:
            params["segmentId"] = segment_id

        resp = httpx.get(
            f"{self._base_url}/members",
            headers=self._headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_signals(self, *, limit: int = 25) -> dict:
        """Return recent buying / engagement signals."""
        resp = httpx.get(
            f"{self._base_url}/activities",
            headers=self._headers(),
            params={"limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_segments(self) -> dict:
        """List all defined community segments."""
        resp = httpx.get(
            f"{self._base_url}/segments",
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def connectivity_check(self) -> dict:
        """Verify Common Room API key is accepted."""
        try:
            result = self.get_segments()
            return {
                "connected": True,
                "provider": "commonroom",
                "segments_found": len(result) if isinstance(result, list) else 0,
            }
        except Exception as exc:
            return {
                "connected": False,
                "provider": "commonroom",
                "error": f"{exc.__class__.__name__}: {exc}",
            }


def get_commonroom_adapter() -> CommonRoomAdapter:
    if not COMMONROOM_API_KEY:
        raise EnvironmentError(
            "COMMONROOM_API_KEY is not set. Set it in .env to enable community signal intelligence."
        )
    return CommonRoomAdapter(api_key=COMMONROOM_API_KEY, base_url=COMMONROOM_BASE_URL)
