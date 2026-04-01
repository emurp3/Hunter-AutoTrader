from __future__ import annotations

from app.services.sources.base import SourceAdapter, SourceOpportunity


class AffiliateScannerAdapter(SourceAdapter):
    def source_name(self) -> str:
        return "affiliate_scanner"

    def fetch_opportunities(self) -> list[dict]:
        return []

    def normalize(self, raw: dict) -> SourceOpportunity | None:
        return None

    def run(self) -> list[SourceOpportunity]:
        self._health.mark(
            status="blocked",
            live=False,
            count=0,
            notes="No structured public affiliate feed confirmed yet. Affpaying is reachable but only as brittle HTML.",
        )
        return []
