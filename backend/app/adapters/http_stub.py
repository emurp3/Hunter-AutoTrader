"""
HTTP adapter — calls a live AutoTrader HTTP endpoint.

Requires AUTOTRADER_HTTP_URL to be set. Activate by setting
AUTOTRADER_SOURCE_TYPE=http in .env.

Failure contract (hard fail, no silent fallback):
  - URL not configured     → raises AutoTraderSourceError
  - httpx not installed    → raises AutoTraderSourceError
  - Non-2xx HTTP response  → raises AutoTraderSourceError
  - Response not a list    → raises AutoTraderSourceError

Authentication: set AUTOTRADER_HTTP_API_KEY in .env; sent as Bearer token.

Dependency: add `httpx` to requirements.txt before using this adapter.
"""

import logging
from typing import Any

from app.adapters.file_adapter import AutoTraderSourceError

logger = logging.getLogger(__name__)


class HttpAdapter:
    """
    Fetches findings from a live AutoTrader HTTP endpoint.
    Raises AutoTraderSourceError on any configuration or network failure.
    """

    def __init__(self, base_url: str | None, api_key: str | None = None) -> None:
        if not base_url or not base_url.strip():
            raise AutoTraderSourceError(
                "AUTOTRADER_HTTP_URL is not set. "
                "Set it to the base URL of your AutoTrader service."
            )
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key

    def fetch_findings(self) -> list[dict[str, Any]]:
        try:
            import httpx
        except ImportError as exc:
            raise AutoTraderSourceError(
                "HttpAdapter requires httpx. Add it to requirements.txt and reinstall."
            ) from exc

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/findings"
        logger.info("HttpAdapter: fetching findings from %s", url)

        try:
            response = httpx.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AutoTraderSourceError(
                f"AutoTrader HTTP endpoint returned {exc.response.status_code}: {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise AutoTraderSourceError(
                f"AutoTrader HTTP endpoint unreachable: {url} — {exc}"
            ) from exc

        data = response.json()
        if not isinstance(data, list):
            raise AutoTraderSourceError(
                f"AutoTrader HTTP endpoint must return a JSON array. "
                f"Got {type(data).__name__} from {url}."
            )

        logger.info("HttpAdapter: received %d findings", len(data))
        return data
