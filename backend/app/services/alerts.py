"""
Commander alerting service.

raise_alert()        — create a new alert
get_active_alerts()  — unacknowledged alerts, newest first
acknowledge_alert()  — mark alert as acknowledged
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.alert import Alert, AlertPriority, AlertType


def raise_alert(
    alert_type: str,
    title: str,
    body: str,
    session: Session,
    *,
    priority: str = AlertPriority.medium,
    source_id: Optional[str] = None,
) -> Alert:
    alert = Alert(
        alert_type=alert_type,
        priority=priority,
        title=title,
        body=body,
        source_id=source_id,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


def get_active_alerts(session: Session, limit: int = 100) -> list[Alert]:
    stmt = (
        select(Alert)
        .where(Alert.acknowledged == False)  # noqa: E712
        .order_by(Alert.created_at.desc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def get_all_alerts(session: Session, limit: int = 200) -> list[Alert]:
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    return list(session.exec(stmt).all())


def acknowledge_alert(alert_id: int, session: Session) -> Optional[Alert]:
    alert = session.get(Alert, alert_id)
    if not alert:
        return None
    alert.acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


def raise_elite_opportunity_alert(source_id: str, score: float, description: str, session: Session) -> Alert:
    return raise_alert(
        alert_type=AlertType.elite_opportunity,
        title=f"Elite Opportunity Detected — score {score}",
        body=f"Source {source_id}: {description}",
        session=session,
        priority=AlertPriority.critical,
        source_id=source_id,
    )


def raise_strategy_shortfall_alert(active_count: int, required: int, session: Session) -> Alert:
    return raise_alert(
        alert_type=AlertType.strategy_shortfall,
        title=f"Strategy Quota Shortfall — {active_count}/{required} active",
        body=f"Only {active_count} active strategies this week. Minimum required: {required}. Activate more candidates immediately.",
        session=session,
        priority=AlertPriority.high,
    )


def raise_source_discovery_shortfall_alert(found: int, required: int, week_start: str, session: Session) -> Alert:
    return raise_alert(
        alert_type=AlertType.source_discovery_shortfall,
        title=f"Source Discovery Shortfall — {found}/{required} sources this week",
        body=(
            f"Only {found} income source(s) identified in the week of {week_start}. "
            f"Minimum required: {required}. Expand AutoTrader scan or ingest additional sources manually."
        ),
        session=session,
        priority=AlertPriority.high,
    )


def raise_strategy_stale_alert(strategy_id: str, strategy_name: str, days_stale: int, session: Session) -> Alert:
    return raise_alert(
        alert_type=AlertType.strategy_stale,
        title=f"Stale Strategy — {strategy_name} ({strategy_id})",
        body=(
            f"Strategy '{strategy_name}' has been active for {days_stale}+ day(s) with no evidence of activity. "
            "Update evidence_of_activity or close this strategy."
        ),
        session=session,
        priority=AlertPriority.high,
    )
