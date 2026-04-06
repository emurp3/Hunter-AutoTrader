import logging
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlmodel import Session, select

from app.database.config import engine, get_session
from app.models.income_source import IncomeSource
from app.models.decision import OpportunityDecision
from app.services.autotrader import get_intake_state, run_intake
from app.services.source_acquisition import get_latest_results, get_source_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/autotrader", tags=["autotrader"])

# ── Background intake guard ────────────────────────────────────────────────────
# Prevents concurrent intake runs (e.g. manual trigger racing the scheduler).
_intake_lock = threading.Lock()
_intake_running = False


def _run_intake_background() -> None:
    """
    Opens its own DB session (never reuses a request session) and runs the full
    intake pipeline. Called as a BackgroundTask — response has already been sent.
    """
    global _intake_running
    try:
        with Session(engine) as session:
            result = run_intake(session)
        if result.aborted:
            logger.error(
                "background intake aborted — reason=%s details=%s",
                result.abort_reason,
                result.error_details,
            )
        else:
            logger.info(
                "background intake complete — scanned=%d inserted=%d updated=%d skipped=%d errors=%d",
                result.scanned,
                result.inserted,
                result.updated,
                result.skipped,
                result.errors,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("background intake raised an unexpected error: %s", exc)
    finally:
        with _intake_lock:
            _intake_running = False


@router.get("/status")
def autotrader_status() -> dict:
    state = get_intake_state()

    return {
        "source_configured": state.source_configured,
        "intake_running": _intake_running,
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


@router.post("/generate-candidates", status_code=200)
def trigger_generate_candidates() -> dict:
    """
    Manually run the trading candidate generator. Screens the Alpaca watchlist,
    applies momentum filter, and writes qualifying entries to autotrader.json.
    Returns count of candidates written and the output path.
    Idempotent — safe to call multiple times. Overwrites previous file.
    """
    from app.services.trading_candidates import AUTOTRADER_JSON_PATH, generate_trading_candidates
    count = generate_trading_candidates()
    return {
        "status": "ok",
        "candidates_written": count,
        "output_path": str(AUTOTRADER_JSON_PATH),
        "message": (
            f"{count} trading candidate(s) written to autotrader.json. "
            "Run POST /autotrader/run-intake to ingest them."
            if count > 0
            else "No candidates passed the momentum filter. autotrader.json not updated."
        ),
    }


@router.post("/run-intake", status_code=202)
def trigger_intake(background_tasks: BackgroundTasks) -> dict:
    """
    Queue the AutoTrader intake pipeline. Returns 202 immediately.
    Actual work runs in a background thread with its own DB session.
    Poll GET /autotrader/status for progress and results.
    """
    global _intake_running
    with _intake_lock:
        if _intake_running:
            return {
                "status": "already_running",
                "message": "Intake is already in progress. Poll /autotrader/status for updates.",
            }
        _intake_running = True

    background_tasks.add_task(_run_intake_background)
    return {
        "status": "accepted",
        "message": "Intake queued. Running in background. Poll /autotrader/status for results.",
    }


@router.get("/opportunities")
def autotrader_opportunities(
    limit: int = Query(default=50, ge=1, le=200),
    band: str | None = Query(default=None, description="Filter by priority band: elite, high, medium, low"),
    session: Session = Depends(get_session),
) -> dict:
    """
    Live-scored opportunities from all ingestion sources, ordered by score descending.
    Combines AutoTrader intake sources and direct source acquisition results.
    """
    stmt = select(IncomeSource).order_by(IncomeSource.score.desc()).limit(limit)
    records = session.exec(stmt).all()

    if band:
        records = [r for r in records if r.priority_band == band]

    # Bulk-fetch decisions for these source_ids
    source_ids = [r.source_id for r in records]
    decision_map: dict[str, OpportunityDecision] = {}
    if source_ids:
        decisions = session.exec(
            select(OpportunityDecision).where(OpportunityDecision.source_id.in_(source_ids))
        ).all()
        decision_map = {d.source_id: d for d in decisions}

    in_memory = get_latest_results()

    def _decision_summary(d: OpportunityDecision | None) -> dict | None:
        if not d:
            return None
        return {
            "action_state": d.action_state,
            "execution_path": d.execution_path,
            "execution_ready": d.execution_ready,
            "approval_required": d.approval_required,
            "blocked_by": d.blocked_by,
            "capital_recommendation": d.capital_recommendation,
        }

    return {
        "total": len(records),
        "source_type": get_intake_state().last_source_type or "none",
        "last_scan_at": get_intake_state().last_scan_at.isoformat() if get_intake_state().last_scan_at else None,
        "opportunities": [
            {
                "source_id": r.source_id,
                "description": r.description,
                "score": r.score,
                "priority_band": r.priority_band,
                "estimated_profit": r.estimated_profit,
                "confidence": r.confidence,
                "category": r.category,
                "status": r.status.value,
                "origin_module": r.origin_module,
                "next_action": r.next_action,
                "date_found": r.date_found.isoformat() if r.date_found else None,
                "decision": _decision_summary(decision_map.get(r.source_id)),
            }
            for r in records
        ],
        "in_memory_count": len(in_memory),
        "sources_status": get_source_status().get("sources", {}),
    }
