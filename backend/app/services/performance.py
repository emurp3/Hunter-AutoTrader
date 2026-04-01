from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlmodel import Session, select

from app.models.alert import AlertPriority, AlertType
from app.models.execution_outcome import ExecutionOutcome
from app.models.income_source import IncomeSource
from app.services import alerts as alert_svc
from app.services import events as event_svc
from app.models.event import EventType


def get_performance_summary(session: Session) -> dict:
    outcomes = _get_outcomes(session)
    completed = [o for o in outcomes if o.execution_state == "completed"]
    failed = [o for o in outcomes if o.execution_state in ("failed", "canceled")]
    total_return = round(sum(o.actual_return or 0.0 for o in completed), 2)

    lane_rows = get_performance_by_lane(session)["lanes"]
    category_rows = get_performance_by_category(session)["categories"]

    best_lane = lane_rows[0] if lane_rows else None
    weakest_lane = sorted(lane_rows, key=lambda row: (row["roi_pct"], row["success_rate"]))[0] if lane_rows else None

    _raise_performance_alerts(session, lane_rows, category_rows)

    return {
        "outcomes_recorded": len(outcomes),
        "completed_executions": len(completed),
        "failed_executions": len(failed),
        "success_rate": round(len(completed) / len(outcomes), 3) if outcomes else None,
        "total_actual_return": total_return,
        "best_lane": best_lane,
        "weakest_lane": weakest_lane,
        "average_return_per_opportunity_type": [
            {"category": row["category"], "average_return": row["average_return"]}
            for row in category_rows
        ],
    }


def get_performance_by_lane(session: Session) -> dict:
    outcomes = _get_outcomes(session)
    grouped: dict[str, list[ExecutionOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.lane or "unknown"].append(outcome)

    rows = []
    for lane, lane_outcomes in grouped.items():
        rows.append(_aggregate_group(lane_outcomes, key_name="lane", key_value=lane))

    rows.sort(key=lambda row: (row["roi_pct"], row["success_rate"], row["count"]), reverse=True)
    return {"lanes": rows}


def get_performance_by_category(session: Session) -> dict:
    outcomes = _get_outcomes(session)
    grouped: dict[str, list[ExecutionOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.category or "unclassified"].append(outcome)

    rows = []
    for category, category_outcomes in grouped.items():
        rows.append(_aggregate_group(category_outcomes, key_name="category", key_value=category))

    rows.sort(key=lambda row: (row["roi_pct"], row["success_rate"], row["count"]), reverse=True)
    return {"categories": rows}


def get_feedback_adjustment(source: IncomeSource, session: Session) -> dict:
    lane = _infer_lane(source)
    category = source.category or "unclassified"

    lane_stats = _aggregate_group(
        [o for o in _get_outcomes(session) if (o.lane or "unknown") == lane],
        key_name="lane",
        key_value=lane,
    )
    category_stats = _aggregate_group(
        [o for o in _get_outcomes(session) if (o.category or "unclassified") == category],
        key_name="category",
        key_value=category,
    )

    adjustment = 0.0
    reasons: list[str] = []

    if lane_stats["count"] >= 2:
        if lane_stats["success_rate"] >= 0.7 and lane_stats["roi_pct"] > 0:
            adjustment += 4.0
            reasons.append(f"lane {lane} is performing well")
        elif lane_stats["success_rate"] <= 0.35 or lane_stats["roi_pct"] < 0:
            adjustment -= 4.0
            reasons.append(f"lane {lane} is underperforming")

    if category_stats["count"] >= 2:
        if category_stats["success_rate"] >= 0.7 and category_stats["roi_pct"] > 0:
            adjustment += 2.0
            reasons.append(f"category {category} is returning well")
        elif category_stats["success_rate"] <= 0.35 or category_stats["roi_pct"] < 0:
            adjustment -= 2.0
            reasons.append(f"category {category} is showing failure patterns")

    return {
        "adjustment": round(adjustment, 2),
        "lane": lane,
        "category": category,
        "reasons": reasons,
        "lane_stats": lane_stats,
        "category_stats": category_stats,
    }


def _get_outcomes(session: Session) -> list[ExecutionOutcome]:
    return list(
        session.exec(select(ExecutionOutcome).order_by(ExecutionOutcome.recorded_at.desc())).all()
    )


def _aggregate_group(outcomes: list[ExecutionOutcome], *, key_name: str, key_value: str) -> dict:
    count = len(outcomes)
    successes = [o for o in outcomes if o.execution_state == "completed"]
    failures = [o for o in outcomes if o.execution_state in ("failed", "canceled")]
    total_return = round(sum(o.actual_return or 0.0 for o in successes), 2)
    total_count = len(successes) + len(failures)
    average_return = round(total_return / len(successes), 2) if successes else 0.0

    allocated_basis = round(sum(o.allocated_amount or 0.0 for o in outcomes), 2)
    roi_pct = round(((total_return - allocated_basis) / allocated_basis) * 100, 2) if allocated_basis else 0.0

    return {
        key_name: key_value,
        "count": count,
        "completed": len(successes),
        "failed": len(failures),
        "success_rate": round(len(successes) / total_count, 3) if total_count else 0.0,
        "total_return": total_return,
        "average_return": average_return,
        "roi_pct": roi_pct,
        "sample_source_ids": [o.source_id for o in outcomes[:5]],
    }


def _raise_performance_alerts(session: Session, lane_rows: list[dict], category_rows: list[dict]) -> None:
    for row in lane_rows:
        if row["count"] < 2:
            continue
        if row["success_rate"] >= 0.7 and row["roi_pct"] > 0:
            _safe_raise_alert(
                session,
                AlertType.high_performing_lane,
                f"High-performing lane - {row['lane']}",
                f"Lane {row['lane']} is returning {row['roi_pct']}% ROI with success rate {row['success_rate']:.0%}.",
            )
        elif row["success_rate"] <= 0.35 or row["roi_pct"] < 0:
            _safe_raise_alert(
                session,
                AlertType.underperforming_lane,
                f"Underperforming lane - {row['lane']}",
                f"Lane {row['lane']} is underperforming with ROI {row['roi_pct']}% and success rate {row['success_rate']:.0%}.",
            )

    for row in category_rows:
        if row["failed"] >= 2 and row["success_rate"] <= 0.35:
            _safe_raise_alert(
                session,
                AlertType.repeated_failure_pattern,
                f"Repeated failure pattern - {row['category']}",
                f"Category {row['category']} has {row['failed']} failed outcomes and only {row['success_rate']:.0%} success.",
            )

    event_svc.log_event(
        "performance",
        EventType.performance_updated,
        session,
        summary="Performance analytics refreshed",
        metadata={"lanes": len(lane_rows), "categories": len(category_rows)},
    )


def _safe_raise_alert(session: Session, alert_type: str, title: str, body: str) -> None:
    existing = [
        alert
        for alert in alert_svc.get_active_alerts(session)
        if alert.alert_type == alert_type and alert.title == title
    ]
    if existing:
        return
    alert_svc.raise_alert(
        alert_type=alert_type,
        title=title,
        body=body,
        session=session,
        priority=AlertPriority.medium,
        source_id=None,
    )


def _infer_lane(source: IncomeSource) -> str:
    if source.notes and "Lane:" in source.notes:
        try:
            return source.notes.split("Lane:", 1)[1].splitlines()[0].strip() or (source.origin_module or "unknown")
        except Exception:
            return source.origin_module or "unknown"
    return source.origin_module or "unknown"
