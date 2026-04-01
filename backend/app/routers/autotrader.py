from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.database.config import get_session
from app.models.income_source import IncomeSource
from app.services.autotrader import get_intake_state, run_intake

router = APIRouter(prefix="/autotrader", tags=["autotrader"])


@router.get("/status")
def autotrader_status() -> dict:
    state = get_intake_state()

    return {
        "source_configured": state.source_configured,
        "config": {
            "source_type": state.last_source_type or "file",
            "file_path": state.live_data_path,
            "seed_path": state.fallback_path,
        },
        "last_scan_at": state.last_scan_at.isoformat() if state.last_scan_at else None,
        "last_scan_status": state.last_status,
        "last_scan_counts": {
            "scanned": state.last_scanned,
            "inserted": state.last_inserted,
            "updated": state.last_updated,
            "skipped": state.last_skipped,
            "errors": state.last_errors,
        },
        "source_reachable": state.source_reachable,
        "last_error": state.last_error,
        "live_data_status": state.live_data_status,
        "live_data_message": state.live_data_message,
        "live_data_updated_at": state.live_data_updated_at,
        "live_data_record_count": state.live_data_record_count,
        "stale_after_hours": state.stale_after_hours,
        "using_fallback": state.using_fallback,
        "fallback_reason": state.fallback_reason,
        "fallback_record_count": state.fallback_record_count,
        "current_data_mode": state.current_data_mode,
    }


@router.get("/intake-summary")
def intake_summary(session: Session = Depends(get_session)) -> dict:
    state = get_intake_state()
    sources = session.exec(
        select(IncomeSource)
        .where(IncomeSource.origin_module.in_(["autotrader", "autotrader_seed"]))
        .order_by(IncomeSource.score.desc())
    ).all()

    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_origin: dict[str, int] = {}
    for s in sources:
        by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
        if s.category:
            by_category[s.category] = by_category.get(s.category, 0) + 1
        origin = s.origin_module or "unknown"
        by_origin[origin] = by_origin.get(origin, 0) + 1

    total_estimated = sum(s.estimated_profit for s in sources)
    rated = [s for s in sources if s.confidence is not None]
    avg_confidence = round(sum(s.confidence for s in rated) / len(rated), 3) if rated else None

    return {
        "total_from_autotrader": len(sources),
        "total_estimated_monthly_profit": round(total_estimated, 2),
        "average_confidence": avg_confidence,
        "by_status": by_status,
        "by_category": by_category,
        "by_origin": by_origin,
        "current_data_mode": state.current_data_mode,
        "using_fallback": state.using_fallback,
        "live_data_status": state.live_data_status,
        "top_5_by_score": [
            {
                "source_id": s.source_id,
                "description": s.description,
                "score": s.score,
                "estimated_profit": s.estimated_profit,
                "confidence": s.confidence,
                "status": s.status.value,
            }
            for s in sources[:5]
        ],
    }


@router.post("/run-intake", status_code=200)
def trigger_intake(session: Session = Depends(get_session)) -> dict:
    """
    Manually trigger the AutoTrader intake pipeline.
    Returns full result including aborted/abort_reason if source is unavailable.
    """
    result = run_intake(session)
    return result.to_dict()
