"""
Tests for the morning brief (app.services.morning_brief) — the condensed
watcher digest Hunter proactively submits every morning, separate from
the full /reports/daily operational dump.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlmodel import Session, SQLModel, create_engine


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_morning_brief_has_required_top_level_fields():
    from app.services.morning_brief import build_morning_brief

    with _make_session() as session:
        brief = build_morning_brief(session)

    for field in ("report_type", "overall_status", "summary", "reasons", "capital",
                  "opportunities", "discovery_sources", "signal_engine", "alerts"):
        assert field in brief, f"morning brief missing expected field: {field}"

    assert brief["report_type"] == "morning_brief"
    assert brief["generated_by"] == "hunter"
    assert brief["overall_status"] in ("healthy", "needs_attention", "critical")


def test_empty_system_is_not_silently_reported_as_healthy():
    """A fresh DB with no open budget, no strategies, and a disconnected
    broker should NOT report 'healthy' -- that would defeat the point of
    a watcher report."""
    from app.services.morning_brief import build_morning_brief

    with _make_session() as session:
        brief = build_morning_brief(session)

    assert brief["overall_status"] != "healthy"
    assert len(brief["reasons"]) > 0


def test_summary_reflects_urgency_for_critical_status():
    from app.services.morning_brief import build_morning_brief

    with _make_session() as session:
        brief = build_morning_brief(session)

    if brief["overall_status"] == "critical":
        assert "URGENT" in brief["summary"]
