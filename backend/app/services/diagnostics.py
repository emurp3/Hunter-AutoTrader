from __future__ import annotations

from collections import Counter, deque
from datetime import date, datetime, timedelta, timezone
import threading
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models.action_packet import ActionPacket, ExecutionState, PacketStatus
from app.models.budget import AllocationStatus, BudgetAllocation
from app.models.task import Task, TaskAttempt, TaskStatus

_MAX_ERROR_MESSAGE = 280
_MAX_RECENT_ERRORS = 40
_SUPPORTED_TASK_TYPES = {
    "digital_product_launch",
    "service_outreach",
    "marketplace_listing",
}


class _DiagnosticsStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._components: dict[str, dict[str, Any]] = {}
        self._recent_errors: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT_ERRORS)

    def record_success(
        self,
        component: str,
        *,
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._lock:
            existing = dict(self._components.get(component, {}))
            existing.update(
                {
                    "status": status,
                    "last_success_at": now,
                    "affected_component": component,
                    "metadata": _json_safe(metadata or {}),
                }
            )
            self._components[component] = existing
            return dict(existing)

    def record_error(
        self,
        component: str,
        exc: Exception | str,
        *,
        status: str = "error",
        metadata: dict[str, Any] | None = None,
        affected_component: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        message, error_type = summarize_exception(exc)
        payload = {
            "status": status,
            "last_error_at": now,
            "error_message": message,
            "error_type": error_type,
            "affected_component": affected_component or component,
            "metadata": _json_safe(metadata or {}),
        }
        with self._lock:
            existing = dict(self._components.get(component, {}))
            existing.update(payload)
            self._components[component] = existing
            self._recent_errors.appendleft(dict(payload))
            return dict(existing)

    def snapshot(self, component: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._components.get(component, {}))

    def recent_errors(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in list(self._recent_errors)[: max(1, limit)]]


_store = _DiagnosticsStore()


def summarize_exception(exc: Exception | str) -> tuple[str, str]:
    if isinstance(exc, str):
        message = exc.strip()
        error_type = "RuntimeError"
    else:
        message = str(exc).strip()
        error_type = exc.__class__.__name__
    if not message:
        message = error_type
    message = _sanitize_message(message)
    if len(message) > _MAX_ERROR_MESSAGE:
        message = f"{message[:_MAX_ERROR_MESSAGE - 3]}..."
    return message, error_type


def record_success(
    component: str,
    *,
    status: str = "ok",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _store.record_success(component, status=status, metadata=metadata)


def record_error(
    component: str,
    exc: Exception | str,
    *,
    status: str = "error",
    metadata: dict[str, Any] | None = None,
    affected_component: str | None = None,
) -> dict[str, Any]:
    return _store.record_error(
        component,
        exc,
        status=status,
        metadata=metadata,
        affected_component=affected_component,
    )


def snapshot(component: str) -> dict[str, Any]:
    return _store.snapshot(component)


def recent_errors(limit: int = 10) -> list[dict[str, Any]]:
    return _store.recent_errors(limit=limit)


def capital_source_details(capital_state: dict[str, Any] | None) -> dict[str, Any]:
    if not capital_state:
        return {
            "ui_source": "unavailable",
            "broker_state_label": "no payload",
            "capital_state_source_label": "unavailable",
        }

    if capital_state.get("broker_sync_success"):
        return {
            "ui_source": "broker",
            "broker_state_label": "broker truth",
            "capital_state_source_label": "broker",
        }

    return {
        "ui_source": "fallback",
        "broker_state_label": "fallback/internal ledger",
        "capital_state_source_label": "fallback/internal_ledger",
    }


def planning_source_details(budget_payload: dict[str, Any] | None) -> dict[str, Any]:
    budget_cycle = None
    if budget_payload:
        candidate = budget_payload.get("budget")
        budget_cycle = candidate if isinstance(candidate, dict) else budget_payload

    status = budget_cycle.get("status") if isinstance(budget_cycle, dict) else None
    planning_status = status or "no_open_budget"
    return {
        "planning_state_source_label": "budget_cycle_open" if planning_status == "open" else planning_status,
        "planning_says_no_open_budget": planning_status != "open",
        "planning_status": planning_status,
    }


def build_capital_metadata(
    *,
    budget_payload: dict[str, Any] | None,
    capital_state: dict[str, Any] | None,
    readiness_budget_open: bool,
) -> dict[str, Any]:
    source_details = capital_source_details(capital_state)
    planning_details = planning_source_details(budget_payload)
    planning_says_no_open_budget = planning_details["planning_says_no_open_budget"]
    return {
        **source_details,
        **planning_details,
        "readiness_budget_open": readiness_budget_open,
        "states_inconsistent": planning_says_no_open_budget == readiness_budget_open,
        "broker_sync_success": bool(capital_state.get("broker_sync_success")) if capital_state else False,
        "last_broker_sync_at": capital_state.get("last_broker_sync_at") if capital_state else None,
        "broker_sync_error": capital_state.get("broker_sync_error") if capital_state else None,
    }


def get_task_type_summary(session: Session) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    tasks = session.exec(select(Task).where(Task.created_at >= cutoff)).all()
    unsupported = [task for task in tasks if task.task_type not in _SUPPORTED_TASK_TYPES]
    active_statuses = {
        TaskStatus.created,
        TaskStatus.dispatched,
        TaskStatus.executing,
        TaskStatus.retrying,
    }
    active_unsupported = [task for task in unsupported if task.status in active_statuses]
    counts = Counter(task.task_type for task in unsupported)
    active_counts = Counter(task.task_type for task in active_unsupported)
    latest = None
    for task in sorted(
        active_unsupported,
        key=lambda item: item.created_at,
        reverse=True,
    ):
        latest = task
        break
    latest_historical = None
    for task in sorted(
        unsupported,
        key=lambda item: item.escalated_at or item.failed_at or item.created_at,
        reverse=True,
    ):
        latest_historical = task
        break

    return {
        "status": "error" if active_unsupported else "ok",
        "last_success_at": _utc_now_iso(),
        "last_error_at": latest.escalated_at.isoformat() if latest and latest.escalated_at else (
            latest.failed_at.isoformat() if latest and latest.failed_at else (
                latest.created_at.isoformat() if latest else None
            )
        ),
        "error_message": f"Unsupported task_type: {latest.task_type}" if latest else None,
        "error_type": "UnsupportedTaskType" if latest else None,
        "affected_component": "execution.task_types",
        "active_unsupported_task_count": len(active_unsupported),
        "active_unsupported_task_types": sorted(active_counts.keys()),
        "unsupported_task_count_24h": len(unsupported),
        "unsupported_task_types": sorted(counts.keys()),
        "unsupported_task_type_counts": dict(sorted(counts.items())),
        "latest_historical_unsupported_task_type": latest_historical.task_type if latest_historical else None,
    }


def get_execution_metrics(session: Session) -> dict[str, Any]:
    packets = session.exec(select(ActionPacket)).all()
    allocations = session.exec(select(BudgetAllocation)).all()
    attempts = session.exec(select(TaskAttempt)).all()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    planned_items = sum(1 for packet in packets if packet.execution_state == ExecutionState.planned)
    funded_items = sum(
        1 for allocation in allocations if allocation.status in (AllocationStatus.planned, AllocationStatus.active)
    )
    executable_items = sum(1 for packet in packets if packet.status == PacketStatus.ready)
    active_executions = sum(
        1 for packet in packets if packet.execution_state in (ExecutionState.active, ExecutionState.in_progress)
    )
    completed_executions = sum(1 for packet in packets if packet.execution_state == ExecutionState.completed)
    failed_executions = sum(
        1 for packet in packets if packet.execution_state in (ExecutionState.failed, ExecutionState.canceled)
    )

    latest_attempt = None
    for attempt in sorted(
        attempts,
        key=lambda item: item.completed_at or item.started_at,
        reverse=True,
    ):
        if attempt.status in {"failed", "escalated"}:
            latest_attempt = attempt
            break

    task_summary = get_task_type_summary(session)

    return {
        "status": "ok",
        "last_success_at": _utc_now_iso(),
        "last_error_at": latest_attempt.completed_at.isoformat() if latest_attempt and latest_attempt.completed_at else None,
        "error_message": _sanitize_message(
            latest_attempt.error_text or latest_attempt.summary_reason or latest_attempt.error_message or ""
        ) if latest_attempt else None,
        "error_type": "ExecutionPipelineError" if latest_attempt else None,
        "affected_component": "execution.status",
        "planned_items": planned_items,
        "funded_items": funded_items,
        "executable_items": executable_items,
        "active_executions": active_executions,
        "completed_executions": completed_executions,
        "failed_executions": failed_executions,
        "unsupported_task_count_24h": task_summary["unsupported_task_count_24h"],
        "unsupported_task_types": task_summary["unsupported_task_types"],
        "unsupported_task_type_counts": task_summary["unsupported_task_type_counts"],
        "recent_task_failures_24h": sum(
            1
            for attempt in attempts
            if attempt.completed_at
            and attempt.completed_at >= cutoff
            and attempt.status in {"failed", "escalated"}
        ),
    }


def get_component_health_summary() -> dict[str, Any]:
    budget_current = snapshot("budget.current")
    capital_state = snapshot("budget.capital_state")
    execution_state = snapshot("execution.status")
    broker_sync = snapshot("broker.sync")
    return {
        "status": "ok",
        "last_success_at": _utc_now_iso(),
        "last_error_at": _latest_timestamp(
            budget_current.get("last_error_at"),
            capital_state.get("last_error_at"),
            execution_state.get("last_error_at"),
            broker_sync.get("last_error_at"),
        ),
        "error_message": (
            capital_state.get("error_message")
            or budget_current.get("error_message")
            or execution_state.get("error_message")
            or broker_sync.get("error_message")
        ),
        "error_type": (
            capital_state.get("error_type")
            or budget_current.get("error_type")
            or execution_state.get("error_type")
            or broker_sync.get("error_type")
        ),
        "affected_component": "diag.health_summary",
        "components": {
            "budget_current": budget_current,
            "budget_capital_state": capital_state,
            "execution_status": execution_state,
            "broker_sync": broker_sync,
        },
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_timestamp(*values: Any) -> str | None:
    cleaned = [value for value in values if isinstance(value, str) and value]
    if not cleaned:
        return None
    return max(cleaned)


def _sanitize_message(message: str) -> str:
    text = " ".join(message.split())
    for token in ("api_key", "secret_key", "password", "token", "authorization", "cookie"):
        text = text.replace(token, f"{token[0]}***")
        text = text.replace(token.upper(), f"{token[0].upper()}***")
    return text


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        try:
            return _json_safe(
                {
                    key: item
                    for key, item in vars(value).items()
                    if not key.startswith("_")
                }
            )
        except Exception:
            return str(value)
    return str(value)
