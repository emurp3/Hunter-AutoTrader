from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SourceHealth:
    name: str
    enabled: bool
    status: str = "idle"  # idle | ok | degraded | blocked | error | disabled
    live: bool = False
    last_run_at: str | None = None
    last_error: str | None = None
    opportunities_found: int = 0
    notes: str | None = None

    def mark(
        self,
        *,
        status: str,
        live: bool,
        count: int = 0,
        error: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.status = status
        self.live = live
        self.last_run_at = datetime.now(timezone.utc).isoformat()
        self.last_error = error
        self.opportunities_found = count
        self.notes = notes


@dataclass
class SourceOpportunity:
    source_id: str
    description: str
    estimated_profit: float
    currency: str
    confidence: float
    next_action: str
    origin_module: str
    category: str
    source_url: str
    timestamp: str
    lane: str = "general"
    title: str | None = None
    source_name: str | None = None
    signal_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "description": self.description,
            "estimated_profit": self.estimated_profit,
            "currency": self.currency,
            "confidence": self.confidence,
            "next_action": self.next_action,
            "origin_module": self.origin_module,
            "category": self.category,
            "lane": self.lane,
            "source_url": self.source_url,
            "timestamp": self.timestamp,
            "source_name": self.source_name,
            "signal_type": self.signal_type,
            "metadata": self.metadata,
        }


class SourceAdapter(ABC):
    def __init__(self, *, enabled: bool = True, max_records: int = 10) -> None:
        self._enabled = enabled
        self._max_records = max_records
        self._health = SourceHealth(name=self.source_name(), enabled=enabled)
        if not enabled:
            self._health.status = "disabled"
            self._health.notes = "Adapter disabled by configuration."

    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    def fetch_opportunities(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> SourceOpportunity | None: ...

    def health_status(self) -> dict[str, Any]:
        return {
            "name": self._health.name,
            "enabled": self._health.enabled,
            "status": self._health.status,
            "live": self._health.live,
            "last_run_at": self._health.last_run_at,
            "last_error": self._health.last_error,
            "opportunities_found": self._health.opportunities_found,
            "notes": self._health.notes,
        }

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
        notes = None if live else "No actionable opportunities found from this source."
        self._health.mark(status=status, live=live, count=len(normalized), notes=notes)
        return normalized
