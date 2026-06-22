"""
Policy-to-Profit Router — API endpoints for the Policy Intelligence Center dashboard.

Endpoints:
  GET  /policy/dashboard        — summary metrics + top opportunities
  GET  /policy/events           — paginated list of detected events
  GET  /policy/events/{id}      — single event with full LLM analysis
  POST /policy/scan             — trigger immediate P2P scan (background-safe)
  GET  /policy/opportunities    — all policy-sourced IncomeSource records
  GET  /policy/health           — source adapter health status
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlmodel import Session, select, col

from app.database.config import get_session
from app.models.income_source import IncomeSource, SourceStatus
from app.models.policy_event import PolicyEvent
from app.services.policy_engine import (
    get_dashboard_metrics,
    get_source_health,
    run_policy_scan,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/policy", tags=["policy"])

_last_scan_result: dict | None = None
_scan_running = False


# ── Dashboard ─────────────────────────────────────────────────────────────────
@router.get("/dashboard")
def get_dashboard(session: Session = Depends(get_session)):
    """
    Policy-to-Profit Intelligence Center — summary dashboard.
    Returns metrics, top opportunities, and source health.
    """
    metrics = get_dashboard_metrics(session)
    health = get_source_health()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title": "Policy-to-Profit Intelligence Center",
        "metrics": metrics,
        "source_health": health,
        "last_scan": _last_scan_result,
    }


# ── Events ────────────────────────────────────────────────────────────────────
@router.get("/events")
def list_events(
    source: Optional[str] = None,
    processed: Optional[bool] = None,
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    """List detected policy events, newest first."""
    query = select(PolicyEvent).order_by(col(PolicyEvent.detected_at).desc())

    if source:
        query = query.where(PolicyEvent.source_name == source)
    if processed is not None:
        query = query.where(PolicyEvent.processed == processed)

    events = session.exec(query.offset(offset).limit(limit)).all()

    return {
        "total": len(events),
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "id": e.id,
                "source_name": e.source_name,
                "title": e.title,
                "summary": e.summary[:200],
                "source_url": e.source_url,
                "detected_at": e.detected_at.isoformat() if e.detected_at else None,
                "published_at": e.published_at.isoformat() if e.published_at else None,
                "processed": e.processed,
                "opportunities_generated": e.opportunities_generated,
                "affected_industries": json.loads(e.affected_industries) if e.affected_industries else [],
                "opportunity_categories": json.loads(e.opportunity_categories) if e.opportunity_categories else [],
            }
            for e in events
        ],
    }


@router.get("/events/{event_id}")
def get_event(event_id: int, session: Session = Depends(get_session)):
    """Get a single event with full LLM analysis."""
    event = session.get(PolicyEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    analysis = None
    if event.llm_analysis:
        try:
            analysis = json.loads(event.llm_analysis)
        except json.JSONDecodeError:
            analysis = {"error": "Could not parse analysis"}

    # Get linked opportunities
    linked_opps = session.exec(
        select(IncomeSource).where(
            IncomeSource.origin_module == "policy_engine",
            IncomeSource.notes.contains(f"Policy event ID: {event.id}")  # type: ignore
        )
    ).all()

    return {
        "id": event.id,
        "source_name": event.source_name,
        "source_url": event.source_url,
        "title": event.title,
        "summary": event.summary,
        "detected_at": event.detected_at.isoformat() if event.detected_at else None,
        "published_at": event.published_at.isoformat() if event.published_at else None,
        "processed": event.processed,
        "processing_error": event.processing_error,
        "opportunities_generated": event.opportunities_generated,
        "affected_industries": json.loads(event.affected_industries) if event.affected_industries else [],
        "opportunity_categories": json.loads(event.opportunity_categories) if event.opportunity_categories else [],
        "analysis": analysis,
        "linked_opportunities": [
            {
                "id": s.id,
                "source_id": s.source_id,
                "title": s.title,
                "description": s.description[:300],
                "score": s.score,
                "priority_band": s.priority_band,
                "estimated_profit": s.estimated_profit,
                "category": s.category,
                "status": s.status,
                "next_action": s.next_action,
            }
            for s in linked_opps
        ],
    }


# ── Scan ─────────────────────────────────────────────────────────────────────
@router.post("/scan")
def trigger_scan(background_tasks: BackgroundTasks):
    """
    Trigger an immediate Policy-to-Profit scan.
    Runs in the background; check /policy/dashboard for results.
    """
    global _scan_running
    if _scan_running:
        return {"status": "already_running", "message": "A scan is already in progress."}

    def _bg_scan():
        global _last_scan_result, _scan_running
        _scan_running = True
        try:
            result = run_policy_scan()
            _last_scan_result = result
            logger.info("policy_engine: background scan complete — %s", result)
        except Exception as exc:
            logger.error("policy_engine: background scan failed — %s", exc)
            _last_scan_result = {"error": str(exc), "scanned_at": datetime.now(timezone.utc).isoformat()}
        finally:
            _scan_running = False

    background_tasks.add_task(_bg_scan)
    return {
        "status": "started",
        "message": "Policy-to-Profit scan started. Check /policy/dashboard for results.",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Opportunities ─────────────────────────────────────────────────────────────
@router.get("/opportunities")
def list_policy_opportunities(
    priority: Optional[str] = None,
    category: Optional[str] = None,
    min_score: float = Query(default=0.0),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    """
    List all policy-engine-generated revenue opportunities.
    Filtered and sorted by Hunter score descending.
    """
    query = select(IncomeSource).where(
        IncomeSource.origin_module == "policy_engine",
        IncomeSource.status.notin_([SourceStatus.rejected, SourceStatus.exhausted])  # type: ignore
    )

    if priority:
        query = query.where(IncomeSource.priority_band == priority)
    if category:
        query = query.where(IncomeSource.category.contains(category))  # type: ignore
    if min_score > 0:
        query = query.where(IncomeSource.score >= min_score)

    query = query.order_by(col(IncomeSource.score).desc())
    sources = session.exec(query.offset(offset).limit(limit)).all()

    return {
        "total": len(sources),
        "offset": offset,
        "limit": limit,
        "opportunities": [
            {
                "id": s.id,
                "source_id": s.source_id,
                "title": s.title,
                "description": s.description[:400],
                "score": s.score,
                "priority_band": s.priority_band,
                "estimated_profit": s.estimated_profit,
                "category": s.category,
                "status": s.status,
                "next_action": s.next_action,
                "confidence": s.confidence,
                "date_found": s.date_found.isoformat() if s.date_found else None,
            }
            for s in sources
        ],
    }


# ── Health ────────────────────────────────────────────────────────────────────
@router.get("/health")
def check_source_health():
    """Check health status of all P2P source adapters."""
    health = get_source_health()
    live_count = sum(1 for h in health if h.get("live"))
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_sources": len(health),
        "live_sources": live_count,
        "sources": health,
    }


# ── Breaking Actions (top new unprocessed) ─────────────────────────────────────
@router.get("/breaking")
def get_breaking_actions(
    limit: int = Query(default=10, le=50),
    session: Session = Depends(get_session),
):
    """Get the most recent unprocessed events — the 'breaking' feed."""
    events = session.exec(
        select(PolicyEvent)
        .order_by(col(PolicyEvent.detected_at).desc())
        .limit(limit)
    ).all()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "breaking_actions": [
            {
                "id": e.id,
                "source": e.source_name,
                "title": e.title,
                "summary": e.summary[:300],
                "url": e.source_url,
                "detected_at": e.detected_at.isoformat() if e.detected_at else None,
                "processed": e.processed,
                "opportunities": e.opportunities_generated,
            }
            for e in events
        ],
    }
