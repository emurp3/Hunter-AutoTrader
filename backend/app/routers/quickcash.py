"""
Quick-Cash Board router.
"/quickcash" endpoints.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from app.database.config import get_session
from app.services.quickcash import get_quick_cash_board
from app.auth.jwt import get_current_user
from app.auth.models import UserInDB

router = APIRouter(prefix="/quickcash", tags=["quickcash"])


@router.get("/board")
def quick_cash_board(
    limit: int = Query(default=50, ge=1, le=100),
    lane: str | None = Query(default=None, description="Filter by lane: trading, signal_copy, forge"),
    session: Session = Depends(get_session),
    _: UserInDB = Depends(get_current_user),
) -> dict:
    """
    Ranked cross-lane opportunity board.
    Sorted by: (1/days_to_cash) * expected_revenue * confidence / effort.
    """
    board = get_quick_cash_board(session, limit=limit * 3)  # fetch extra to filter
    if lane:
        board["board"] = [x for x in board["board"] if x["lane"] == lane]
    board["board"] = board["board"][:limit]
    board["total"] = len(board["board"])
    return board
