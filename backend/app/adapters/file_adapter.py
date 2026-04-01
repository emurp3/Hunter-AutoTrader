"""
Real file adapter — reads AutoTrader findings from a real AutoTrader JSON export.

This adapter requires AUTOTRADER_FILE_PATH to be explicitly configured and
pointing at a file produced by the real AutoTrader module.

Failure contract (hard fail, no silent fallback):
  - Path not configured  → raises AutoTraderSourceError
  - File does not exist  → raises AutoTraderSourceError
  - File unreadable      → raises AutoTraderSourceError
  - File is not a JSON array → raises AutoTraderSourceError

None of the above conditions return an empty list. They all raise so the
caller (run_intake) records an aborted scan rather than a successful empty one.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AutoTraderSourceError(RuntimeError):
    """Raised when the real AutoTrader source is missing, unreachable, or invalid."""


class RealFileAdapter:
    """
    Reads findings from a real AutoTrader JSON export file.

    The file must:
    - Exist at the configured path
    - Contain a JSON array at the top level
    - Each element must be a dict (individual findings are validated downstream)
    """

    def __init__(self, path: str | None) -> None:
        if not path or not path.strip():
            raise AutoTraderSourceError(
                "AUTOTRADER_FILE_PATH is not set. "
                "Set it to the path of your AutoTrader JSON export."
            )
        self._path = Path(path.strip())

    def fetch_findings(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            raise AutoTraderSourceError(
                f"AutoTrader findings file not found: {self._path}. "
                "Ensure AutoTrader has exported findings to this path before the scan runs."
            )

        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AutoTraderSourceError(
                f"AutoTrader findings file could not be read: {self._path} — {exc}"
            ) from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise AutoTraderSourceError(
                f"AutoTrader findings file contains invalid JSON: {self._path} — {exc}"
            ) from exc

        if not isinstance(data, list):
            raise AutoTraderSourceError(
                f"AutoTrader findings file must contain a JSON array. "
                f"Got {type(data).__name__} in {self._path}."
            )

        logger.info(
            "RealFileAdapter: loaded %d findings from %s", len(data), self._path
        )
        return data
