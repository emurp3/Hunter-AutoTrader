from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database.config import get_session
from app.services import performance as perf_svc

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/summary")
def performance_summary(session: Session = Depends(get_session)):
    return perf_svc.get_performance_summary(session)


@router.get("/by-lane")
def performance_by_lane(session: Session = Depends(get_session)):
    return perf_svc.get_performance_by_lane(session)


@router.get("/by-category")
def performance_by_category(session: Session = Depends(get_session)):
    return perf_svc.get_performance_by_category(session)
