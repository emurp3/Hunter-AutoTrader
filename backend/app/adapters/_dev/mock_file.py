"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  DEV-ONLY — QUARANTINED — NOT USED IN PRODUCTION                           ║
║                                                                              ║
║  This file is retained for reference only.                                  ║
║  It is NOT imported by any active runtime code.                             ║
║  Do not re-enable it as a fallback or default adapter.                      ║
║  Hunter operates on real AutoTrader sources only.                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

Original MockFileAdapter — read synthetic findings from a local JSON file.
Retired when real-source-only contract was enforced.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent.parent / "mock_data" / "autotrader_findings.json"


class MockFileAdapter:
    """
    QUARANTINED — DEV REFERENCE ONLY.
    Do not instantiate in production code.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH

    def fetch_findings(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            logger.warning(
                "MockFileAdapter: findings file not found at %s — returning empty list",
                self._path,
            )
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("MockFileAdapter: failed to read %s — %s", self._path, exc)
            return []
        if not isinstance(data, list):
            return []
        return data
