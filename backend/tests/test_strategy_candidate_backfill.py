"""
Regression test for the 2026-09-02 fix: an opportunity that existed
already, then got re-scored on a later scan and crossed into elite/high
for the first time, never created a candidate strategy -- only fresh
inserts did (process_new_opportunity). This is why Hunter could show
80 elite/high opportunities and 0 candidate strategies to promote from:
most real opportunities get touched by a later scan update, not scored
elite/high in the same pass they were first discovered.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.main  # noqa: F401 — registers all SQLModel tables

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.income_source import IncomeSource, SourceStatus
from app.models.strategy import Strategy
from app.services.source_acquisition import _persist_results
from app.services.sources.base import SourceOpportunity


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_opportunity(source_id: str, profit: float, confidence: float, description: str) -> SourceOpportunity:
    return SourceOpportunity(
        source_id=source_id,
        title=description,
        description=description,
        estimated_profit=profit,
        currency="USD",
        confidence=confidence,
        next_action="Do the thing",
        origin_module="gig_scanner",
        category="gig",
        source_url=f"https://example.com/{source_id}",
        timestamp="2026-09-02T00:00:00+00:00",
    )


def test_opportunity_that_crosses_into_high_on_a_later_update_creates_a_strategy():
    with _make_session() as session:
        # First pass: low-value opportunity, inserted at low/medium band.
        low_value = _make_opportunity("test-src-1", profit=50, confidence=0.3, description="Small low-value gig")
        _persist_results(session, [low_value])

        strategies_before = session.exec(select(Strategy)).all()
        assert len(strategies_before) == 0

        # Second pass: same source_id, but now with a much bigger profit —
        # simulates a re-scan updating the record and pushing it into
        # elite/high for the first time.
        upgraded = _make_opportunity("test-src-1", profit=50_000, confidence=0.6, description="Small low-value gig")
        _persist_results(session, [upgraded])

        record = session.exec(select(IncomeSource).where(IncomeSource.source_id == "test-src-1")).first()
        assert record.priority_band in ("elite", "high"), f"expected elite/high, got {record.priority_band}"

        strategies_after = session.exec(select(Strategy)).all()
        assert len(strategies_after) == 1, "update path should have created a candidate strategy"
        assert strategies_after[0].linked_opportunity_source_id == "test-src-1"
