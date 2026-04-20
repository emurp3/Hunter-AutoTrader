from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database.config import get_session
from app.services import budget as budget_svc
from app.services import diagnostics as diag_svc
from app.services.execution import get_execution_status

router = APIRouter(prefix="/diag", tags=["diag"])


@router.get("/capital-status")
def capital_status(session: Session = Depends(get_session)) -> dict:
    budget_current = diag_svc.snapshot("budget.current")
    capital_state = diag_svc.snapshot("budget.capital_state")
    broker_sync = diag_svc.snapshot("broker.sync")
    open_budget = budget_svc.get_open_budget(session)
    current_meta = budget_current.get("metadata", {})
    capital_meta = capital_state.get("metadata", {})
    source_meta = capital_meta or current_meta
    return {
        "status": capital_state.get("status") or budget_current.get("status") or "unknown",
        "last_success_at": capital_state.get("last_success_at") or budget_current.get("last_success_at"),
        "last_error_at": capital_state.get("last_error_at") or budget_current.get("last_error_at") or broker_sync.get("last_error_at"),
        "error_message": capital_state.get("error_message") or budget_current.get("error_message") or broker_sync.get("error_message"),
        "error_type": capital_state.get("error_type") or budget_current.get("error_type") or broker_sync.get("error_type"),
        "affected_component": "diag.capital_status",
        "budget_current_endpoint_status": budget_current.get("status") or "unknown",
        "budget_capital_state_endpoint_status": capital_state.get("status") or "unknown",
        "last_successful_broker_sync_at": broker_sync.get("last_success_at") or source_meta.get("last_broker_sync_at"),
        "last_failed_broker_sync_at": broker_sync.get("last_error_at"),
        "last_capital_payload_error": capital_state.get("error_message") or budget_current.get("error_message"),
        "broker_state_mode": source_meta.get("broker_state_label", "no payload"),
        "ui_source": source_meta.get("ui_source", "unavailable"),
        "capital_state_source_label": source_meta.get("capital_state_source_label", "unavailable"),
        "planning_state_source_label": source_meta.get("planning_state_source_label", "unknown"),
        "planning_says_no_open_budget": source_meta.get("planning_says_no_open_budget", not bool(open_budget)),
        "readiness_says_budget_open": source_meta.get("readiness_budget_open", bool(open_budget)),
        "states_inconsistent": source_meta.get("states_inconsistent", False),
    }


@router.get("/execution-status")
def execution_status_diag(session: Session = Depends(get_session)) -> dict:
    try:
        payload = get_execution_status(session)
        diag_svc.record_success(
            "execution.status",
            metadata={
                "active": payload.get("counts", {}).get("active", 0),
                "completed": payload.get("counts", {}).get("completed", 0),
                "failed": payload.get("counts", {}).get("failed", 0),
            },
        )
    except Exception as exc:
        diag_svc.record_error("execution.status", exc, affected_component="execution.status")
    metrics = diag_svc.get_execution_metrics(session)
    state = diag_svc.snapshot("execution.status")
    metrics["status"] = state.get("status") or metrics["status"]
    metrics["last_success_at"] = state.get("last_success_at") or metrics["last_success_at"]
    metrics["last_error_at"] = state.get("last_error_at") or metrics["last_error_at"]
    metrics["error_message"] = state.get("error_message") or metrics["error_message"]
    metrics["error_type"] = state.get("error_type") or metrics["error_type"]
    return metrics


@router.get("/recent-errors")
def recent_errors(limit: int = 10) -> dict:
    errors = diag_svc.recent_errors(limit=limit)
    latest = errors[0] if errors else {}
    return {
        "status": "ok",
        "last_success_at": None,
        "last_error_at": latest.get("last_error_at"),
        "error_message": latest.get("error_message"),
        "error_type": latest.get("error_type"),
        "affected_component": latest.get("affected_component"),
        "errors": errors,
    }


@router.get("/task-type-summary")
def task_type_summary(session: Session = Depends(get_session)) -> dict:
    return diag_svc.get_task_type_summary(session)


@router.get("/health-summary")
def health_summary(session: Session = Depends(get_session)) -> dict:
    capital = capital_status(session)
    execution = execution_status_diag(session)
    summary = diag_svc.get_component_health_summary()
    summary["capital"] = capital
    summary["execution"] = execution
    summary["recent_errors"] = diag_svc.recent_errors(limit=5)
    return summary
