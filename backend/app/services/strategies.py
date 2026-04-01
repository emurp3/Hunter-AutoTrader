"""
Strategy management service.

activate_strategy()        — promote a candidate to activated/active
get_active_strategies()    — currently active strategies
check_quota()              — verify weekly minimum (default 10)
auto_promote_candidates()  — auto-activate highest-scoring candidates to meet quota
get_weekly_status()        — summary dict for reporting
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, func, select

from app.models.income_source import IncomeSource
from app.models.strategy import Strategy, StrategyCreate, StrategyStatus

WEEKLY_STRATEGY_MINIMUM = 10
WEEKLY_SOURCES_MINIMUM = 10


def create_strategy(data: StrategyCreate, session: Session) -> Strategy:
    strategy = Strategy(
        strategy_id=f"STR-{uuid.uuid4().hex[:8].upper()}",
        strategy_name=data.strategy_name,
        linked_opportunity_source_id=data.linked_opportunity_source_id,
        category=data.category,
        date_activated=date.today(),
        status=StrategyStatus.candidate,
        expected_return=data.expected_return,
        execution_path=data.execution_path,
        owner=data.owner,
    )
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy


def activate_strategy(strategy_id: str, session: Session) -> Optional[Strategy]:
    strategy = session.exec(select(Strategy).where(Strategy.strategy_id == strategy_id)).first()
    if not strategy:
        return None
    strategy.status = StrategyStatus.active
    strategy.date_activated = date.today()
    strategy.updated_at = datetime.now(timezone.utc)
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy


def get_active_strategies(session: Session) -> list[Strategy]:
    stmt = select(Strategy).where(Strategy.status == StrategyStatus.active).order_by(Strategy.date_activated.desc())
    return list(session.exec(stmt).all())


def get_candidates(session: Session) -> list[Strategy]:
    stmt = select(Strategy).where(Strategy.status == StrategyStatus.candidate).order_by(Strategy.expected_return.desc())
    return list(session.exec(stmt).all())


def check_quota(session: Session, minimum: int = WEEKLY_STRATEGY_MINIMUM) -> dict:
    active = get_active_strategies(session)
    shortfall = max(0, minimum - len(active))
    return {
        "active_count": len(active),
        "required": minimum,
        "shortfall": shortfall,
        "quota_met": shortfall == 0,
    }


def auto_promote_candidates(session: Session, minimum: int = WEEKLY_STRATEGY_MINIMUM) -> list[Strategy]:
    """Promote enough candidates to meet the weekly quota."""
    quota = check_quota(session, minimum)
    if quota["quota_met"]:
        return []

    candidates = get_candidates(session)
    promoted: list[Strategy] = []
    needed = quota["shortfall"]

    for candidate in candidates[:needed]:
        candidate.status = StrategyStatus.active
        candidate.date_activated = date.today()
        candidate.updated_at = datetime.now(timezone.utc)
        session.add(candidate)
        promoted.append(candidate)

    if promoted:
        session.commit()
        for s in promoted:
            session.refresh(s)

    return promoted


def check_source_discovery_quota(
    session: Session,
    minimum: int = WEEKLY_SOURCES_MINIMUM,
    week_start: Optional[date] = None,
) -> dict:
    """
    Count income sources with date_found in the current week.
    Returns quota status: found, required, shortfall, quota_met.
    """
    if week_start is None:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday

    week_end = week_start + timedelta(days=6)
    count = session.exec(
        select(func.count(IncomeSource.id)).where(
            IncomeSource.date_found >= week_start,
            IncomeSource.date_found <= week_end,
        )
    ).one()

    shortfall = max(0, minimum - count)
    return {
        "sources_found_this_week": count,
        "required": minimum,
        "shortfall": shortfall,
        "quota_met": shortfall == 0,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
    }


def flag_stale_active_strategies(session: Session, stale_after_days: int = 2) -> list[Strategy]:
    """
    Find active strategies with no evidence_of_activity that have been active
    for longer than stale_after_days. Mark them underperforming and return the list.
    """
    cutoff = date.today() - timedelta(days=stale_after_days)
    candidates = session.exec(
        select(Strategy).where(
            Strategy.status == StrategyStatus.active,
            Strategy.date_activated <= cutoff,
            Strategy.evidence_of_activity == None,  # noqa: E711
        )
    ).all()

    flagged = []
    for strategy in candidates:
        strategy.status = StrategyStatus.underperforming
        strategy.updated_at = datetime.now(timezone.utc)
        strategy.reason_for_continuation_or_termination = (
            f"Auto-flagged: no evidence_of_activity after {stale_after_days}d of activation."
        )
        session.add(strategy)
        flagged.append(strategy)

    if flagged:
        session.commit()
        for s in flagged:
            session.refresh(s)

    return flagged


def create_strategy_from_opportunity(source_id: str, session: Session) -> Strategy:
    """Create a candidate strategy pre-linked to an income source, using its data as defaults."""
    from app.models.income_source import IncomeSource
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    if not source:
        raise ValueError(f"Income source not found: {source_id}")

    # Check if a strategy already exists for this source
    existing = session.exec(
        select(Strategy).where(Strategy.linked_opportunity_source_id == source_id)
    ).first()
    if existing:
        return existing

    strategy = Strategy(
        strategy_id=f"STR-{uuid.uuid4().hex[:8].upper()}",
        strategy_name=source.description[:120],
        linked_opportunity_source_id=source_id,
        category=source.category or "unclassified",
        date_activated=date.today(),
        status=StrategyStatus.candidate,
        expected_return=source.estimated_profit,
        execution_path=source.next_action,
    )
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy


def activate_strategy_for_source(source_id: str, session: Session) -> Optional[Strategy]:
    """Activate the strategy linked to the given income source_id."""
    strategy = session.exec(
        select(Strategy).where(Strategy.linked_opportunity_source_id == source_id)
    ).first()
    if not strategy:
        return None
    strategy.status = StrategyStatus.active
    strategy.date_activated = date.today()
    strategy.updated_at = datetime.now(timezone.utc)
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy


def get_weekly_status(session: Session, minimum: int = WEEKLY_STRATEGY_MINIMUM) -> dict:
    quota = check_quota(session, minimum)
    active = get_active_strategies(session)
    candidates = get_candidates(session)

    total_expected = sum(s.expected_return or 0 for s in active)
    total_actual = sum(s.actual_return or 0 for s in active)

    # Strategies activated this week (date_activated in current week)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    activated_this_week = session.exec(
        select(Strategy).where(
            Strategy.date_activated >= week_start,
            Strategy.status.in_([StrategyStatus.active.value, StrategyStatus.activated.value]),
        )
    ).all()

    # Strategies retired this week (completed, failed, archived — updated this week)
    retired_statuses = [StrategyStatus.completed.value, StrategyStatus.failed.value, StrategyStatus.archived.value]
    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    retired_this_week = session.exec(
        select(Strategy).where(
            Strategy.status.in_(retired_statuses),
            Strategy.updated_at >= week_start_dt,
        )
    ).all()

    replacement_required = max(0, minimum - len(active))

    return {
        **quota,
        "candidates_available": len(candidates),
        "total_expected_return": round(total_expected, 2),
        "total_actual_return": round(total_actual, 2),
        "activated_this_week": len(activated_this_week),
        "retired_this_week": len(retired_this_week),
        "replacement_strategies_required": replacement_required,
        "quota_met": quota["quota_met"],
        "strategies": [
            {
                "strategy_id": s.strategy_id,
                "strategy_name": s.strategy_name,
                "category": s.category,
                "expected_return": s.expected_return,
                "actual_return": s.actual_return,
                "status": s.status,
                "evidence_of_activity": s.evidence_of_activity,
                "date_activated": s.date_activated.isoformat(),
            }
            for s in active
        ],
    }
