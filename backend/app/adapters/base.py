"""
Abstract adapter protocol for field module integrations.

Any class that implements `fetch_findings() -> list[dict]` satisfies this
protocol — no explicit inheritance required. This keeps concrete adapters
loosely coupled to the interface.

Expected shape of each returned finding dict (AutoTrader canonical format):

  {
    "id":                     str    — unique finding identifier within the module
    "description":            str    — human-readable opportunity description
    "estimated_monthly_return": float  — estimated USD monthly profit (≥ 0)
    "currency":               str    — ISO currency code, default "USD"
    "confidence":             float  — signal confidence 0.0–1.0
    "category":               str    — broad category ("trading", "software", etc.)
    "signal_date":            str    — ISO date string "YYYY-MM-DD"
    "suggested_action":       str | None
    "notes":                  str | None
    "raw_payload":            dict | None  — full original payload for audit
  }

Fields not present will be set to None / sensible defaults during normalization.
Adapters MUST populate at least: id, description, estimated_monthly_return, signal_date.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AutoTraderAdapter(Protocol):
    """
    Protocol satisfied by any object with a fetch_findings() method.
    Concrete adapters do not need to inherit from this class.
    """

    def fetch_findings(self) -> list[dict[str, Any]]:
        """
        Fetch raw findings from the data source.
        Returns a list of finding dicts matching the canonical format above.
        Must not raise on empty results — return [] instead.
        """
        ...
