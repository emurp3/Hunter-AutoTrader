from __future__ import annotations

from app.services.sources.base import SourceAdapter, SourceOpportunity


class RfpScannerAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "rfp_scanner"

    def fetch_opportunities(self) -> list[dict]:
        return []

    def normalize(self, raw: dict) -> SourceOpportunity | None:
        return None

    def run(self) -> list[SourceOpportunity]:
        self._health.mark(
            status="blocked",
            live=False,
            count=0,
            notes="No clean public procurement API/feed confirmed yet. Grants.gov GET returned 405 and SAM requires auth.",
        )
        return []
