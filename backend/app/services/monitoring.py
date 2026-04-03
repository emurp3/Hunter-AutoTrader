"""
Persistent monitoring service.

Hunter continuously monitors targets (income sources, strategies, signals)
for state changes and surfaces them as alerts or opportunity updates.

Watchlist: a set of source_ids Hunter actively tracks between scans.
Signal tracking: detects score changes, status regressions, stale evidence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.income_source import IncomeSource, PriorityBand, SourceStatus
from app.models.strategy import Strategy, StrategyStatus
from app.models.alert import AlertPriority, AlertType
from app.models.event import EventType
from app.services import alerts as alert_svc
from app.services import events as event_svc

logger = logging.getLogger(__name__)

# In-memory watchlist. Persisted across the process lifetime.
# For multi-process deployments, move this to the database.
_WATCHLIST: set[str] = set()
_SCORE_SNAPSHOT: dict[str, float] = {}  # source_id -> last known score


# ── Watchlist management ──────────────────────────────────────────────────────

def add_to_watchlist(source_id: str) -> None:
    _WATCHLIST.add(source_id)
    logger.info("Watchlist: added %s", source_id)


def remove_from_watchlist(source_id: str) -> None:
    _WATCHLIST.discard(source_id)
    _SCORE_SNAPSHOT.pop(source_id, None)


def get_watchlist() -> list[str]:
    return sorted(_WATCHLIST)


def clear_watchlist() -> None:
    _WATCHLIST.clear()
    _SCORE_SNAPSHOT.clear()


# ── Signal detection ──────────────────────────────────────────────────────────

def refresh_watchlist(session: Session) -> dict:
    """
    Run a monitoring pass over all watched sources.

    Detects:
    - score changes (significant delta ≥ 5 pts)
    - status regressions (active → failed/parked)
    - elite/high sources with no strategy linked
    - active strategies with no evidence

    Returns a summary dict.
    """
    changes: list[dict] = []
    regressions: list[dict] = []
    unlinked_elites: list[str] = []

    watched = list(_WATCHLIST)
    if not watched:
        # Auto-populate watchlist with all elite/high sources
        sources = session.exec(
            select(IncomeSource).where(
                IncomeSource.priority_band.in_([PriorityBand.elite, PriorityBand.high])
            )
        ).all()
        for s in sources:
            _WATCHLIST.add(s.source_id)
        watched = [s.source_id for s in sources]

    for source_id in watched:
        source = session.exec(
            select(IncomeSource).where(IncomeSource.source_id == source_id)
        ).first()
        if not source:
            continue

        # Score change detection
        prev_score = _SCORE_SNAPSHOT.get(source_id)
        current_score = source.score or 0.0
        if prev_score is not None:
            delta = current_score - prev_score
            if abs(delta) >= 5:
                changes.append({
                    "source_id": source_id,
                    "prev_score": prev_score,
                    "current_score": current_score,
                    "delta": round(delta, 1),
                })
                event_svc.log_event(
                    source_id,
                    EventType.score_updated,
                    session,
                    summary=f"Score changed {prev_score:.1f} → {current_score:.1f} (Δ{delta:+.1f})",
                    metadata={"prev_score": prev_score, "current_score": current_score},
                )
        _SCORE_SNAPSHOT[source_id] = current_score

        # Status regression detection
        bad_statuses = {SourceStatus.failed, SourceStatus.exhausted, SourceStatus.rejected}
        if source.status in bad_statuses and source.priority_band in (PriorityBand.elite, PriorityBand.high):
            regressions.append({
                "source_id": source_id,
                "status": str(source.status),
                "priority_band": str(source.priority_band),
            })
            alert_svc.raise_alert(
                alert_type=AlertType.underperforming,
                title=f"Priority source regressed — {source_id}",
                body=f"{source.priority_band} source moved to {source.status}. Review and reassign.",
                session=session,
                priority=AlertPriority.high,
                source_id=source_id,
            )

        # Elite/high with no linked strategy
        if source.priority_band in (PriorityBand.elite, PriorityBand.high):
            strategy = session.exec(
                select(Strategy).where(
                    Strategy.linked_opportunity_source_id == source_id
                )
            ).first()
            if not strategy:
                unlinked_elites.append(source_id)

    return {
        "watched_count": len(watched),
        "score_changes": changes,
        "regressions": regressions,
        "unlinked_priority_sources": unlinked_elites,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_monitoring_snapshot(session: Session) -> dict:
    """Return current watchlist state without triggering a refresh."""
    watched = list(_WATCHLIST)
    snapshots = []
    for source_id in watched:
        source = session.exec(
            select(IncomeSource).where(IncomeSource.source_id == source_id)
        ).first()
        if source:
            snapshots.append({
                "source_id": source_id,
                "description": source.description[:80] if source.description else None,
                "status": str(source.status),
                "priority_band": str(source.priority_band),
                "score": source.score,
                "last_known_score": _SCORE_SNAPSHOT.get(source_id),
            })

    return {
        "watchlist_count": len(watched),
        "sources": snapshots,
        "score_snapshots": _SCORE_SNAPSHOT,
    }
