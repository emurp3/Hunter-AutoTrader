from fastapi import APIRouter

from app.services.scheduler import build_weekly_report_now

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/weekly")
def get_weekly_report() -> dict:
    """Generate and return the weekly report on demand."""
    return build_weekly_report_now()
