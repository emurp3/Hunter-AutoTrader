"""
Persistent monitoring endpoints.

GET  /monitoring/watchlist          — current watchlist
POST /monitoring/watchlist/{id}     — add source to watchlist
DELETE /monitoring/watchlist/{id}   — remove source from watchlist
POST /monitoring/refresh            — run a monitoring pass (score changes, regressions)
GET  /monitoring/snapshot           — snapshot of watched sources without refresh
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database.config import get_session
from app.services import monitoring as monitor_svc

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/watchlist")
def get_watchlist():
    return {"watchlist": monitor_svc.get_watchlist(), "count": len(monitor_svc.get_watchlist())}


@router.post("/watchlist/{source_id}", status_code=201)
def add_to_watchlist(source_id: str):
    monitor_svc.add_to_watchlist(source_id)
    return {"added": source_id, "watchlist_count": len(monitor_svc.get_watchlist())}


@router.delete("/watchlist/{source_id}")
def remove_from_watchlist(source_id: str):
    monitor_svc.remove_from_watchlist(source_id)
    return {"removed": source_id}


@router.post("/refresh")
def run_monitoring_refresh(session: Session = Depends(get_session)):
    """Trigger a monitoring scan — detect score changes, regressions, unlinked sources."""
    return monitor_svc.refresh_watchlist(session)


@router.get("/snapshot")
def monitoring_snapshot(session: Session = Depends(get_session)):
    return monitor_svc.get_monitoring_snapshot(session)
