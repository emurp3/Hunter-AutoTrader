"""
Regression tests for the 2026-09-02 policy_engine fixes:

1. Policy-engine-created opportunities now score through the same
   scoring.score_opportunity() engine as every other source, instead of
   a separate, incompatible LLM-weighted formula (_compute_hunter_score).
   That formula ranked policy opportunities on a scale nothing else in
   the system used, so a genuinely huge policy opportunity and a huge
   marketplace opportunity were never actually comparable.

2. The old _priority_band() helper could return PriorityBand.critical,
   which is not a valid PriorityBand value (low/medium/high/elite only).
   Any opportunity the LLM judged "Critical", or that scored >=75, threw
   an AttributeError and silently failed to save -- exactly the highest-
   value opportunities disappearing without a trace. Same bug existed a
   second time in get_dashboard_metrics() (backing /policy/dashboard),
   which crashed on every call.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.main  # noqa: F401 — registers all SQLModel tables (FK resolution needs the full set)

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.policy_event import PolicyEvent
from app.models.income_source import IncomeSource, PriorityBand
from app.services import policy_engine


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _fake_analysis(priority_level: str, revenue_low: float, revenue_high: float, confidence_factor: int = 6):
    return {
        "what_happened": "test event",
        "why_it_matters": "test reason",
        "affected_industries": [],
        "opportunity_categories": [],
        "opportunities": [
            {
                "title": f"{priority_level} test opportunity",
                "description": "test description",
                "opportunity_type": "grant",
                "priority_level": priority_level,
                "revenue_potential_low": revenue_low,
                "revenue_potential_high": revenue_high,
                "time_sensitivity_days": 14,
                "recommended_actions": ["Do the thing"],
                "score_factors": {"revenue_potential": 9, "confidence": confidence_factor},
                "profile_impacts": {},
            }
        ],
    }


def _make_event(session: Session, content_hash: str) -> PolicyEvent:
    event = PolicyEvent(
        content_hash=content_hash, source_name="whitehouse_actions",
        source_url="https://example.gov/x", title="Test action", summary="Test summary",
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def test_critical_priority_level_no_longer_crashes_and_creates_the_opportunity():
    """This is the core bug: priority_level='Critical' used to raise
    AttributeError inside _priority_band() and silently drop the
    opportunity -- exactly the ones the LLM flagged as most important."""
    with _make_session() as session:
        event = _make_event(session, "hash-critical")
        with patch("app.services.policy_engine._call_llm", return_value=_fake_analysis("Critical", 50_000, 500_000)):
            n_created = policy_engine._process_event(event, session)
            session.commit()

        assert n_created == 1
        created = session.exec(select(IncomeSource).where(IncomeSource.origin_module == "policy_engine")).all()
        assert len(created) == 1


def test_high_score_opportunity_also_no_longer_crashes():
    """Second trigger path for the same bug: score >= 75 with no explicit
    priority_level in the map also used to return PriorityBand.critical."""
    with _make_session() as session:
        event = _make_event(session, "hash-highscore")
        with patch("app.services.policy_engine._call_llm", return_value=_fake_analysis("Unmapped", 200_000, 800_000, confidence_factor=9)):
            n_created = policy_engine._process_event(event, session)
            session.commit()

        assert n_created == 1


def test_policy_opportunities_use_real_priority_bands_only():
    with _make_session() as session:
        event = _make_event(session, "hash-bands")
        with patch("app.services.policy_engine._call_llm", return_value=_fake_analysis("Critical", 300_000, 700_000)):
            policy_engine._process_event(event, session)
            session.commit()

        created = session.exec(select(IncomeSource).where(IncomeSource.origin_module == "policy_engine")).first()
        assert created.priority_band in (PriorityBand.low, PriorityBand.medium, PriorityBand.high, PriorityBand.elite)


def test_policy_opportunity_score_reflects_real_scoring_rationale():
    """Confirms the record is actually scored via scoring.score_opportunity()
    (has a real rationale string with EV/status/confidence breakdown) rather
    than the old flat LLM-weighted number with no rationale trail."""
    with _make_session() as session:
        event = _make_event(session, "hash-rationale")
        with patch("app.services.policy_engine._call_llm", return_value=_fake_analysis("High", 10_000, 20_000)):
            policy_engine._process_event(event, session)
            session.commit()

        created = session.exec(select(IncomeSource).where(IncomeSource.origin_module == "policy_engine")).first()
        assert created.score_rationale is not None
        assert "profit(EV=" in created.score_rationale


def test_title_is_preserved_instead_of_silently_dropped():
    """IncomeSource has no 'title' column -- the old code passed title= to
    the constructor anyway, which was silently discarded. Title is now
    folded into notes so it isn't lost."""
    with _make_session() as session:
        event = _make_event(session, "hash-title")
        with patch("app.services.policy_engine._call_llm", return_value=_fake_analysis("Medium", 1_000, 2_000)):
            policy_engine._process_event(event, session)
            session.commit()

        created = session.exec(select(IncomeSource).where(IncomeSource.origin_module == "policy_engine")).first()
        assert "Medium test opportunity" in created.notes


def test_dashboard_metrics_no_longer_crashes_on_priority_band_critical():
    with _make_session() as session:
        metrics = policy_engine.get_dashboard_metrics(session)
        assert "high_priority_opportunities" in metrics
