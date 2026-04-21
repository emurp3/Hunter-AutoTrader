"""
Capital and allocation service for Hunter's rolling bankroll model.

In LIVE mode, capital-state fields (available_capital, committed_capital,
current_bankroll, funded_packets) are authoritative only when read from the
broker via broker_reconciliation.get_broker_capital_state().  The internal
ledger (WeeklyBudget / BudgetAllocation) is the source of truth for
realized_profit and historical records, but must NEVER be used to display
currently-available capital when the broker account has open positions.

Use get_broker_reconciled_capital_state() for any outward-facing capital
display in live mode.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.config import (
    ALPACA_ENABLED,
    APPROVAL_REQUIRED_OVER,
    BUDGET_STRICT_MODE,
    EXECUTION_MODE,
    CAPITAL_RESERVE_BUFFER,
    FAST_RECYCLE_TRANCHE,
    FLIP_TARGET_MULTIPLIER,
    LIVE_EXECUTION_PROFILE,
    MAX_ALLOCATION_PER_OPPORTUNITY,
    STRATEGY_MODE,
    USE_ONLY_FAST_RECYCLE_BUCKET,
    LIVE_EXECUTION_STRATEGY,
    WEEKLY_BUDGET,
)

_logger = logging.getLogger(__name__)
from app.models.action_packet import ActionPacket, PacketStatus
from app.models.budget import (
    AllocationCategory,
    AllocationStatus,
    BankrollLedgerEntry,
    BankrollLedgerEntryType,
    BudgetAllocation,
    BudgetOutcome,
    BudgetStatus,
    ManualCapitalInjectionCreate,
    WeeklyBudget,
)
from app.models.income_source import IncomeSource, SourceStatus

EVALUATION_DAYS = 30


def get_open_budget(session: Session) -> Optional[WeeklyBudget]:
    bankroll = session.exec(
        select(WeeklyBudget)
        .where(WeeklyBudget.status == BudgetStatus.open)
        .order_by(WeeklyBudget.evaluation_start_date.desc())
    ).first()
    if bankroll:
        sync_bankroll(session, bankroll)
    return bankroll


def ensure_bankroll(session: Session, starting_bankroll: Optional[float] = None, notes: Optional[str] = None) -> WeeklyBudget:
    existing = get_open_budget(session)
    if existing:
        return existing
    return open_weekly_budget(session, starting_bankroll, notes)


def get_allocations_for_budget(session: Session, weekly_budget_id: int) -> list[BudgetAllocation]:
    return list(
        session.exec(
            select(BudgetAllocation)
            .where(BudgetAllocation.weekly_budget_id == weekly_budget_id)
            .order_by(BudgetAllocation.created_at.desc())
        ).all()
    )


def get_active_allocation_for_source(session: Session, source_id: str) -> Optional[BudgetAllocation]:
    return session.exec(
        select(BudgetAllocation)
        .where(
            BudgetAllocation.source_id == source_id,
            BudgetAllocation.status.in_([AllocationStatus.planned, AllocationStatus.active]),
        )
        .order_by(BudgetAllocation.created_at.desc())
    ).first()


def get_outcomes_for_allocation(session: Session, allocation_id: int) -> list[BudgetOutcome]:
    return list(
        session.exec(
            select(BudgetOutcome).where(BudgetOutcome.allocation_id == allocation_id).order_by(BudgetOutcome.recorded_at.desc())
        ).all()
    )


def get_ledger_entries(session: Session, weekly_budget_id: int, limit: int = 100) -> list[BankrollLedgerEntry]:
    return list(
        session.exec(
            select(BankrollLedgerEntry)
            .where(BankrollLedgerEntry.weekly_budget_id == weekly_budget_id)
            .order_by(BankrollLedgerEntry.created_at.desc())
            .limit(limit)
        ).all()
    )


def open_weekly_budget(
    session: Session,
    starting_budget: Optional[float] = None,
    notes: Optional[str] = None,
) -> WeeklyBudget:
    existing = session.exec(select(WeeklyBudget).where(WeeklyBudget.status == BudgetStatus.open)).first()
    if existing:
        raise ValueError(
            f"An active bankroll already exists (id={existing.id}, evaluation_start={existing.evaluation_start_date})."
        )

    amount = starting_budget if starting_budget is not None else WEEKLY_BUDGET
    today = date.today()
    bankroll = WeeklyBudget(
        week_start_date=today,
        week_end_date=today + timedelta(days=EVALUATION_DAYS - 1),
        starting_budget=amount,
        remaining_budget=amount,
        realized_return=0.0,
        notes=notes or "Initial bankroll activated",
        starting_bankroll=amount,
        current_bankroll=amount,
        evaluation_start_date=today,
        evaluation_end_date=today + timedelta(days=EVALUATION_DAYS - 1),
        capital_match_eligible=False,
        capital_match_amount=0.0,
        manual_injection_total=0.0,
    )
    session.add(bankroll)
    session.commit()
    session.refresh(bankroll)
    return bankroll


def recalc_realized_return(session: Session, budget: WeeklyBudget) -> float:
    allocations = get_allocations_for_budget(session, budget.id)
    total = 0.0
    for allocation in allocations:
        total += sum(outcome.net_result for outcome in get_outcomes_for_allocation(session, allocation.id))
    return round(total, 2)


def recalc_committed_capital(session: Session, budget: WeeklyBudget) -> float:
    allocations = get_allocations_for_budget(session, budget.id)
    committed = sum(
        allocation.amount_allocated
        for allocation in allocations
        if allocation.status in (AllocationStatus.planned, AllocationStatus.active)
    )
    return round(committed, 2)


def recalc_unrealized_exposure(session: Session, budget: WeeklyBudget) -> float:
    allocations = get_allocations_for_budget(session, budget.id)
    exposure = sum(
        allocation.amount_allocated
        for allocation in allocations
        if allocation.status == AllocationStatus.active
    )
    return round(exposure, 2)


def recalc_current_bankroll(session: Session, budget: WeeklyBudget) -> float:
    realized_profit = recalc_realized_return(session, budget)
    return round(budget.starting_bankroll + (budget.manual_injection_total or 0.0) + realized_profit, 2)


def recalc_remaining(session: Session, budget: WeeklyBudget) -> float:
    current_bankroll = recalc_current_bankroll(session, budget)
    committed = recalc_committed_capital(session, budget)
    return round(max(0.0, current_bankroll - committed), 2)


def sync_bankroll(session: Session, budget: WeeklyBudget) -> WeeklyBudget:
    if budget.evaluation_start_date and (
        not budget.evaluation_end_date
        or (budget.evaluation_end_date - budget.evaluation_start_date).days < (EVALUATION_DAYS - 1)
    ):
        budget.evaluation_end_date = budget.evaluation_start_date + timedelta(days=EVALUATION_DAYS - 1)

    realized_profit = recalc_realized_return(session, budget)
    current_bankroll = round(budget.starting_bankroll + (budget.manual_injection_total or 0.0) + realized_profit, 2)
    committed_capital = recalc_committed_capital(session, budget)
    available_capital = round(max(0.0, current_bankroll - committed_capital), 2)

    budget.realized_return = realized_profit
    budget.current_bankroll = current_bankroll
    budget.remaining_budget = available_capital
    budget.starting_budget = budget.starting_bankroll
    budget.week_start_date = budget.evaluation_start_date
    budget.week_end_date = budget.evaluation_end_date

    review = _compute_month_end_review(budget)
    budget.capital_match_eligible = review["capital_match_eligible"]
    budget.capital_match_amount = review["recommended_match_amount"]

    session.add(budget)
    session.commit()
    session.refresh(budget)
    return budget


def sync_current_ledger_to_broker(
    session: Session,
    budget: WeeklyBudget,
    *,
    available_capital: float,
    current_bankroll: float,
) -> WeeklyBudget:
    """
    Persist the current broker-backed ledger snapshot without rewriting the
    historical seed/origin fields.

    Historical fields that remain immutable/origin-based:
      - starting_bankroll
      - starting_budget
      - evaluation_start_date / evaluation_end_date
      - realized_return

    Current/live-synced fields:
      - current_bankroll
      - remaining_budget
    """
    budget.current_bankroll = round(current_bankroll, 2)
    budget.remaining_budget = round(max(0.0, available_capital), 2)
    budget.starting_budget = budget.starting_bankroll
    budget.week_start_date = budget.evaluation_start_date
    budget.week_end_date = budget.evaluation_end_date
    session.add(budget)
    session.commit()
    session.refresh(budget)
    return budget


def is_flipped(budget: WeeklyBudget) -> bool:
    return budget.current_bankroll >= round(budget.starting_bankroll * FLIP_TARGET_MULTIPLIER, 2)


def next_budget_recommendation(session: Session, budget: WeeklyBudget) -> dict:
    review = get_month_end_review(session, budget)
    suggested = review["next_cycle_bankroll_if_matched"] if review["capital_match_eligible"] else review["ending_bankroll"]
    return {
        "suggested_budget": round(suggested, 2),
        "rationale": (
            "Commander match eligible based on positive bankroll growth."
            if review["capital_match_eligible"]
            else "No capital match recommended yet. Keep compounding current bankroll."
        ),
        "based_on_growth_pct": review["growth_pct"],
    }


def get_month_end_review(session: Session, budget: WeeklyBudget) -> dict:
    sync_bankroll(session, budget)
    return _compute_month_end_review(budget)


def inject_manual_capital(session: Session, payload: ManualCapitalInjectionCreate) -> dict:
    bankroll = ensure_bankroll(session)
    bankroll.manual_injection_total = round((bankroll.manual_injection_total or 0.0) + payload.amount, 2)
    session.add(bankroll)
    session.commit()
    sync_bankroll(session, bankroll)
    _log_bankroll_entry(
        session,
        bankroll,
        entry_type=BankrollLedgerEntryType.manual_injection,
        amount_delta_current=payload.amount,
        amount_delta_committed=0.0,
        amount_delta_available=payload.amount,
        notes=payload.notes or "Manual capital injection",
    )
    return {
        "injected": payload.amount,
        "current_bankroll": bankroll.current_bankroll,
        "available_capital": bankroll.remaining_budget,
    }


def recommend_allocation(source_id: str, session: Session) -> dict:
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    if not source:
        raise ValueError(f"Income source not found: {source_id}")

    bankroll = ensure_bankroll(session)
    existing = get_active_allocation_for_source(session, source_id)
    if existing:
        return {
            "source_id": source_id,
            "recommendation": None,
            "reason": "already_allocated",
            "existing_allocation_id": existing.id,
            "existing_amount": existing.amount_allocated,
        }

    available_capital = recalc_remaining(session, bankroll)
    if available_capital <= 0:
        return {"source_id": source_id, "recommendation": None, "reason": "no_available_capital"}

    estimated_profit = float(source.estimated_profit or 0.0)
    confidence = float(source.confidence or 0.0)
    score = float(source.score or 0.0)

    base_fraction = {"elite": 0.18, "high": 0.14, "medium": 0.09, "low": 0.05}.get(
        source.priority_band or "low", 0.05
    )
    confidence_multiplier = 0.7 + (confidence * 0.6)
    if score >= 85:
        score_multiplier = 1.15
    elif score >= 70:
        score_multiplier = 1.0
    elif score >= 55:
        score_multiplier = 0.85
    else:
        score_multiplier = 0.65

    recommended = round(estimated_profit * base_fraction * confidence_multiplier * score_multiplier, 2)
    cap = min(MAX_ALLOCATION_PER_OPPORTUNITY, round(available_capital * 0.35, 2))
    final = round(min(recommended, cap, available_capital), 2)
    if final <= 0:
        return {"source_id": source_id, "recommendation": None, "reason": "insufficient_signal_or_capital"}

    return {
        "source_id": source_id,
        "priority_band": source.priority_band,
        "estimated_profit": source.estimated_profit,
        "confidence": source.confidence,
        "score": source.score,
        "recommended_allocation": final,
        "available_capital": available_capital,
        "approval_required": final > APPROVAL_REQUIRED_OVER,
        "reason": (
            f"profit x base_fraction({base_fraction}) x confidence_multiplier({confidence_multiplier:.2f}) "
            f"x score_multiplier({score_multiplier:.2f}), capped at ${cap:.2f} per opportunity"
        ),
    }


def mark_budget_candidate(source_id: str, session: Session) -> IncomeSource:
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    if not source:
        raise ValueError(f"Income source not found: {source_id}")

    old_status = source.status
    source.status = SourceStatus.budgeted
    session.add(source)
    session.commit()
    session.refresh(source)

    from app.models.event import EventType
    from app.services import events as event_svc

    event_svc.log_event(
        source_id,
        EventType.budget_linked,
        session,
        old_state=old_status,
        new_state=SourceStatus.budgeted,
        summary="Marked as capital candidate",
    )
    return source


def refresh_budget_recommendations(session: Session) -> dict:
    packets = session.exec(
        select(ActionPacket).where(ActionPacket.status.in_((PacketStatus.ready, PacketStatus.draft)))
    ).all()
    updated = 0
    for packet in packets:
        rec = recommend_allocation(packet.source_id, session)
        packet.budget_recommendation = rec.get("recommended_allocation")
        packet.updated_at = datetime.now(timezone.utc)
        session.add(packet)
        updated += 1
    session.commit()
    return {"packets_updated": updated}


def auto_allocate_for_source(source_id: str, session: Session) -> Optional[dict]:
    try:
        rec = recommend_allocation(source_id, session)
    except ValueError:
        return None

    amount = rec.get("recommended_allocation", 0.0) or 0.0
    if amount <= 0:
        return {"skipped": True, "reason": rec.get("reason", "no_recommendation")}

    bankroll = ensure_bankroll(session)
    available_capital = recalc_remaining(session, bankroll)
    if BUDGET_STRICT_MODE and amount > available_capital:
        return {"skipped": True, "reason": f"Insufficient available capital (${available_capital:.2f} remaining)"}

    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()
    approval_required = amount > APPROVAL_REQUIRED_OVER

    allocation = BudgetAllocation(
        weekly_budget_id=bankroll.id,
        allocation_name=source.description[:120] if source else source_id,
        category=_allocation_category_for_source(source),
        amount_allocated=amount,
        rationale=f"Auto-allocated via packet execution: {rec.get('reason', '')}",
        expected_return=source.estimated_profit if source else None,
        source_id=source_id,
        approval_required=approval_required,
        approved_by_commander=False,
        status=AllocationStatus.planned,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(allocation)
    session.commit()
    session.refresh(allocation)

    mark_budget_candidate(source_id, session)
    _sync_packet_after_allocation(source_id, amount, session)
    sync_bankroll(session, bankroll)
    _log_bankroll_entry(
        session,
        bankroll,
        entry_type=BankrollLedgerEntryType.allocation_committed,
        source_id=source_id,
        allocation_id=allocation.id,
        amount_delta_current=0.0,
        amount_delta_committed=amount,
        amount_delta_available=-amount,
        notes=f"Capital committed to {source_id}",
    )

    from app.models.event import EventType
    from app.services import events as event_svc

    event_svc.log_event(
        source_id,
        EventType.budget_linked,
        session,
        summary=f"Capital reserved: ${amount:.2f}",
        metadata={"allocation_id": allocation.id, "amount": amount},
    )

    return {
        "allocation_id": allocation.id,
        "amount": amount,
        "approval_required": approval_required,
    }


def auto_allocate_top_packets(session: Session, max_packets: int = 5) -> dict:
    bankroll = ensure_bankroll(session)
    refresh_budget_recommendations(session)

    packet_rows = session.exec(
        select(ActionPacket, IncomeSource)
        .join(IncomeSource, IncomeSource.source_id == ActionPacket.source_id)
        .where(ActionPacket.status == PacketStatus.ready)
        .order_by(IncomeSource.score.desc())
    ).all()

    allocations: list[dict] = []
    for packet, source in packet_rows:
        if len(allocations) >= max_packets:
            break
        result = auto_allocate_for_source(source.source_id, session)
        if not result or result.get("skipped"):
            continue
        allocations.append(
            {
                "source_id": source.source_id,
                "description": source.description,
                "allocation_id": result["allocation_id"],
                "amount": result["amount"],
                "approval_required": result["approval_required"],
            }
        )

    sync_bankroll(session, bankroll)
    return {
        "allocations_made": len(allocations),
        "allocations": allocations,
        "total_allocated": round(sum(item["amount"] for item in allocations), 2),
        "available_capital": bankroll.remaining_budget,
    }


def get_budget_commander_summary(session: Session) -> dict:
    bankroll = ensure_bankroll(session)
    sync_bankroll(session, bankroll)
    allocations = get_allocations_for_budget(session, bankroll.id)
    committed = recalc_committed_capital(session, bankroll)
    available = recalc_remaining(session, bankroll)
    unrealized = recalc_unrealized_exposure(session, bankroll)
    review = get_month_end_review(session, bankroll)

    alloc_rows = []
    best = worst = None
    for allocation in allocations:
        outcomes = get_outcomes_for_allocation(session, allocation.id)
        alloc_return = sum(outcome.actual_return for outcome in outcomes)
        alloc_net = round(sum(outcome.net_result for outcome in outcomes), 2)
        row = {
            "id": allocation.id,
            "allocation_name": allocation.allocation_name,
            "category": allocation.category.value,
            "amount_allocated": allocation.amount_allocated,
            "actual_return": round(alloc_return, 2),
            "net_result": alloc_net,
            "status": allocation.status.value,
            "approval_required": allocation.approval_required,
            "approved_by_commander": allocation.approved_by_commander,
            "source_id": allocation.source_id,
            "started_at": allocation.started_at.isoformat() if allocation.started_at else None,
            "completed_at": allocation.completed_at.isoformat() if allocation.completed_at else None,
        }
        alloc_rows.append(row)
        if outcomes:
            if best is None or alloc_net > best["net_result"]:
                best = row
            if worst is None or alloc_net < worst["net_result"]:
                worst = row

    return {
        "evaluation_start_date": bankroll.evaluation_start_date.isoformat(),
        "evaluation_end_date": bankroll.evaluation_end_date.isoformat(),
        "starting_bankroll": bankroll.starting_bankroll,
        "current_bankroll": bankroll.current_bankroll,
        "available_capital": available,
        "committed_capital": committed,
        "realized_profit": bankroll.realized_return,
        "unrealized_exposure": unrealized,
        "capital_match_eligible": review["capital_match_eligible"],
        "capital_match_amount": review["recommended_match_amount"],
        "month_end_review": review,
        "allocations": alloc_rows,
        "allocations_by_source": [
            {
                "source_id": allocation.source_id,
                "allocation_name": allocation.allocation_name,
                "amount_allocated": allocation.amount_allocated,
                "status": allocation.status.value,
            }
            for allocation in allocations
        ],
        "top_performer": best,
        "worst_performer": worst,
        "next_cycle_recommendation": next_budget_recommendation(session, bankroll),
        "ledger": [
            {
                "entry_type": entry.entry_type.value,
                "source_id": entry.source_id,
                "allocation_id": entry.allocation_id,
                "amount_delta_current": entry.amount_delta_current,
                "amount_delta_committed": entry.amount_delta_committed,
                "amount_delta_available": entry.amount_delta_available,
                "notes": entry.notes,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in get_ledger_entries(session, bankroll.id, limit=20)
        ],
    }


def record_capital_completion(
    session: Session,
    allocation: BudgetAllocation,
    *,
    source_id: Optional[str],
    action_packet_id: Optional[int],
    actual_return: float,
    notes: Optional[str] = None,
) -> None:
    bankroll = ensure_bankroll(session)
    sync_bankroll(session, bankroll)
    _log_bankroll_entry(
        session,
        bankroll,
        entry_type=BankrollLedgerEntryType.execution_completed,
        source_id=source_id,
        allocation_id=allocation.id,
        action_packet_id=action_packet_id,
        amount_delta_current=round(actual_return - allocation.amount_allocated, 2),
        amount_delta_committed=-allocation.amount_allocated,
        amount_delta_available=actual_return,
        notes=notes or "Execution completed and bankroll rolled forward",
    )
    sync_bankroll(session, bankroll)


def record_capital_failure(
    session: Session,
    allocation: BudgetAllocation,
    *,
    source_id: Optional[str],
    action_packet_id: Optional[int],
    actual_return: float = 0.0,
    notes: Optional[str] = None,
) -> None:
    bankroll = ensure_bankroll(session)
    sync_bankroll(session, bankroll)
    _log_bankroll_entry(
        session,
        bankroll,
        entry_type=BankrollLedgerEntryType.execution_failed,
        source_id=source_id,
        allocation_id=allocation.id,
        action_packet_id=action_packet_id,
        amount_delta_current=round(actual_return - allocation.amount_allocated, 2),
        amount_delta_committed=-allocation.amount_allocated,
        amount_delta_available=actual_return,
        notes=notes or "Execution failed and bankroll absorbed the loss",
    )
    sync_bankroll(session, bankroll)


def record_capital_release(
    session: Session,
    allocation: BudgetAllocation,
    *,
    source_id: Optional[str],
    action_packet_id: Optional[int],
    notes: Optional[str] = None,
) -> None:
    bankroll = ensure_bankroll(session)
    sync_bankroll(session, bankroll)
    _log_bankroll_entry(
        session,
        bankroll,
        entry_type=BankrollLedgerEntryType.allocation_released,
        source_id=source_id,
        allocation_id=allocation.id,
        action_packet_id=action_packet_id,
        amount_delta_current=0.0,
        amount_delta_committed=-allocation.amount_allocated,
        amount_delta_available=allocation.amount_allocated,
        notes=notes or "Allocation released back to available capital",
    )
    sync_bankroll(session, bankroll)


def record_capital_commit(
    session: Session,
    allocation: BudgetAllocation,
    *,
    source_id: Optional[str],
    action_packet_id: Optional[int],
    notes: Optional[str] = None,
) -> None:
    bankroll = ensure_bankroll(session)
    sync_bankroll(session, bankroll)
    _log_bankroll_entry(
        session,
        bankroll,
        entry_type=BankrollLedgerEntryType.allocation_committed,
        source_id=source_id,
        allocation_id=allocation.id,
        action_packet_id=action_packet_id,
        amount_delta_current=0.0,
        amount_delta_committed=allocation.amount_allocated,
        amount_delta_available=-allocation.amount_allocated,
        notes=notes or "Capital committed to allocation",
    )
    sync_bankroll(session, bankroll)


def _sync_packet_after_allocation(source_id: str, amount: float, session: Session) -> None:
    packet = session.exec(
        select(ActionPacket)
        .where(ActionPacket.source_id == source_id)
        .order_by(ActionPacket.created_at.desc())
    ).first()
    if not packet:
        return
    packet.budget_recommendation = amount
    packet.status = PacketStatus.acknowledged
    packet.updated_at = datetime.now(timezone.utc)
    session.add(packet)
    session.commit()


def _allocation_category_for_source(source: Optional[IncomeSource]) -> AllocationCategory:
    category = (source.category or "").lower() if source else ""
    if "automation" in category or "tool" in category or "dashboard" in category:
        return AllocationCategory.tools
    if (
        "service" in category
        or "healthcare" in category
        or "church" in category
        or "artist" in category
    ):
        return AllocationCategory.services
    if "flip" in category or "arbitrage" in category:
        return AllocationCategory.experiments
    return AllocationCategory.other


def _log_bankroll_entry(
    session: Session,
    bankroll: WeeklyBudget,
    *,
    entry_type: BankrollLedgerEntryType,
    source_id: Optional[str] = None,
    allocation_id: Optional[int] = None,
    action_packet_id: Optional[int] = None,
    amount_delta_current: float,
    amount_delta_committed: float,
    amount_delta_available: float,
    notes: Optional[str] = None,
) -> BankrollLedgerEntry:
    current = recalc_current_bankroll(session, bankroll) + amount_delta_current
    committed = recalc_committed_capital(session, bankroll) + amount_delta_committed
    available = max(0.0, round(current - committed, 2))
    entry = BankrollLedgerEntry(
        weekly_budget_id=bankroll.id,
        entry_type=entry_type,
        source_id=source_id,
        allocation_id=allocation_id,
        action_packet_id=action_packet_id,
        amount_delta_current=round(amount_delta_current, 2),
        amount_delta_committed=round(amount_delta_committed, 2),
        amount_delta_available=round(amount_delta_available, 2),
        notes=notes,
        current_bankroll_after=round(current, 2),
        committed_capital_after=round(committed, 2),
        available_capital_after=available,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _compute_month_end_review(budget: WeeklyBudget) -> dict:
    ending_bankroll = budget.current_bankroll
    net_gain = round(ending_bankroll - budget.starting_bankroll, 2)
    growth_pct = round((net_gain / budget.starting_bankroll) * 100, 2) if budget.starting_bankroll else 0.0
    doubling_threshold = round(budget.starting_bankroll * 2, 2)
    doubled = ending_bankroll >= doubling_threshold
    progress_to_doubling_threshold = (
        round((ending_bankroll / doubling_threshold) * 100, 2) if doubling_threshold else 0.0
    )
    capital_match_eligible = doubled
    recommended_match_amount = round(max(0.0, ending_bankroll - budget.starting_bankroll), 2) if doubled else 0.0
    evaluation_window_closed = date.today() >= budget.evaluation_end_date
    return {
        "starting_bankroll": budget.starting_bankroll,
        "ending_bankroll": ending_bankroll,
        "net_gain_loss": net_gain,
        "growth_pct": growth_pct,
        "doubled_bankroll": doubled,
        "doubling_threshold": doubling_threshold,
        "progress_to_doubling_threshold": progress_to_doubling_threshold,
        "capital_match_eligible": capital_match_eligible,
        "recommended_match_amount": recommended_match_amount,
        "next_cycle_bankroll_if_matched": round(ending_bankroll + recommended_match_amount, 2),
        "evaluation_start_date": budget.evaluation_start_date.isoformat(),
        "evaluation_end_date": budget.evaluation_end_date.isoformat(),
        "days_remaining": max(0, (budget.evaluation_end_date - date.today()).days),
        "evaluation_window_closed": evaluation_window_closed,
    }


def _build_fast_recycle_snapshot(session: Session, broker_dict: dict) -> dict:
    from app.services import position_lifecycle as lifecycle_svc

    fast_enabled = LIVE_EXECUTION_PROFILE == "FAST_RECYCLE" and USE_ONLY_FAST_RECYCLE_BUCKET
    positions = broker_dict.get("positions", []) if isinstance(broker_dict, dict) else []
    open_buy_orders = broker_dict.get("open_buy_orders", []) if isinstance(broker_dict, dict) else []
    open_positions_count = broker_dict.get("open_positions_count", 0) if isinstance(broker_dict, dict) else 0
    effective_buying_power = float(broker_dict.get("effective_buying_power", 0.0) or 0.0)

    closed_fast = lifecycle_svc.get_lifecycles_by_bucket(
        session,
        capital_bucket="fast_recycle",
        status="closed",
    )
    open_fast = lifecycle_svc.get_lifecycles_by_bucket(
        session,
        capital_bucket="fast_recycle",
        status="open",
    )

    fast_symbols = {lifecycle.symbol.upper() for lifecycle in open_fast}
    fast_order_ids = {lifecycle.entry_order_id for lifecycle in open_fast if lifecycle.entry_order_id}
    fast_positions = [position for position in positions if position.get("symbol", "").upper() in fast_symbols]
    legacy_positions = [position for position in positions if position.get("symbol", "").upper() not in fast_symbols]
    fast_open_buy_orders = [
        order
        for order in open_buy_orders
        if order.get("order_id") in fast_order_ids or order.get("symbol", "").upper() in fast_symbols
    ]

    realized_fast_pl = round(sum(float(lifecycle.realized_pl or 0.0) for lifecycle in closed_fast), 2)
    tranche_total = round(FAST_RECYCLE_TRANCHE, 2)
    gross_fast_capital = round(max(0.0, tranche_total + realized_fast_pl), 2)
    deployed_fast = round(
        sum(float(position.get("market_value") or 0.0) for position in fast_positions)
        + sum(float(order.get("notional") or 0.0) for order in fast_open_buy_orders),
        2,
    )
    internal_fast_available = round(
        max(0.0, gross_fast_capital - deployed_fast - CAPITAL_RESERVE_BUFFER),
        2,
    )
    available_fast = round(max(0.0, min(internal_fast_available, effective_buying_power)), 2)
    legacy_deployed = round(
        max(0.0, float(broker_dict.get("committed_capital", 0.0) or 0.0) - deployed_fast),
        2,
    )
    stale_positions_count = sum(
        1
        for position in fast_positions
        if position.get("over_max_hold") or position.get("stale_marked_at")
    )
    closed_with_hold = [lifecycle for lifecycle in closed_fast if lifecycle.hold_duration_minutes is not None]
    avg_hold_minutes = round(
        sum(float(lifecycle.hold_duration_minutes or 0.0) for lifecycle in closed_with_hold) / len(closed_with_hold),
        2,
    ) if closed_with_hold else None
    profitable_fast = [lifecycle for lifecycle in closed_fast if (lifecycle.realized_pl or 0.0) > 0]
    win_rate = round(len(profitable_fast) / len(closed_fast), 4) if closed_fast else None
    realized_profit_times = [
        float(lifecycle.time_to_realized_profit_minutes)
        for lifecycle in profitable_fast
        if lifecycle.time_to_realized_profit_minutes is not None
    ]
    avg_time_to_realized_profit = round(
        sum(realized_profit_times) / len(realized_profit_times),
        2,
    ) if realized_profit_times else None
    reuse_count = len(closed_fast)

    return {
        "enabled": fast_enabled,
        "profile": LIVE_EXECUTION_PROFILE,
        "use_only_fast_recycle_bucket": USE_ONLY_FAST_RECYCLE_BUCKET,
        "total_tranche": tranche_total,
        "gross_capital": gross_fast_capital,
        "deployed_capital": deployed_fast,
        "available_capital": available_fast,
        "internal_available_capital": internal_fast_available,
        "realized_pl": realized_fast_pl,
        "average_hold_minutes": avg_hold_minutes,
        "average_time_to_realized_profit_minutes": avg_time_to_realized_profit,
        "recycle_win_rate": win_rate,
        "stale_positions_count": stale_positions_count,
        "capital_velocity_reuse_count": reuse_count,
        "legacy_capital_deployed": legacy_deployed,
        "legacy_open_positions_count": len(legacy_positions),
        "fast_open_positions_count": len(fast_positions),
        "open_positions_count": open_positions_count,
        "fast_positions": fast_positions,
        "legacy_positions": legacy_positions,
    }


# ── Broker-reconciled capital state (live-mode authoritative view) ─────────────

def get_broker_reconciled_capital_state(session: Session) -> dict:
    """
    Authoritative capital-state dict for all outward-facing display.

    Gate: whenever ALPACA_ENABLED=True and broker sync succeeds, broker truth
    wins for all capital fields — regardless of EXECUTION_MODE (live or sandbox).
    This means paper/sandbox Alpaca positions are correctly reflected as committed
    capital instead of the seed-bankroll ledger showing $0 committed / $100 free.

    Falls back to internal WeeklyBudget ledger only when:
      - ALPACA_ENABLED=False, OR
      - broker sync fails (network error, auth error, etc.)

    Always returns a complete dict with no missing keys so callers never get
    a KeyError.
    """
    from app.services.broker_reconciliation import (
        get_broker_capital_state,
        broker_capital_state_to_dict,
    )

    # ── Internal ledger snapshot ─────────────────────────────────────────────
    bankroll = ensure_bankroll(session)
    sync_bankroll(session, bankroll)

    # WeeklyBudget field name mapping:
    #   remaining_budget  → available capital after committed positions
    #   realized_return   → cumulative closed P/L (NOT realized_profit)
    #   committed_capital and total_allocated are not stored on the model;
    #   derive them from the allocations table via recalc_committed_capital().
    #   weekly_target has no direct model field; zero is a safe default.
    planning_committed = float(recalc_committed_capital(session, bankroll) or 0.0)
    raw_internal_available = float(bankroll.remaining_budget or 0.0)
    raw_internal_bankroll = float(bankroll.current_bankroll or 0.0)
    realized_profit    = float(bankroll.realized_return or 0.0)
    starting_bankroll  = float(bankroll.starting_bankroll or 0.0)
    weekly_target      = 0.0          # no direct field on WeeklyBudget
    total_allocated    = planning_committed  # planning/pipeline committed capital

    eval_start = bankroll.evaluation_start_date.isoformat() if bankroll.evaluation_start_date else None
    eval_end   = bankroll.evaluation_end_date.isoformat()   if bankroll.evaluation_end_date   else None

    from app.models.action_packet import ActionPacket, PacketStatus
    from sqlmodel import select as _sel
    from datetime import date
    db_funded = session.exec(
        _sel(ActionPacket).where(
            ActionPacket.status.in_([PacketStatus.executed, PacketStatus.acknowledged])
        )
    ).all()
    internal_funded_count = len(db_funded)

    # ── Month-end review snapshot ─────────────────────────────────────────────
    ending_bankroll = raw_internal_bankroll
    net_gain = round(ending_bankroll - starting_bankroll, 2)
    growth_pct = round((net_gain / starting_bankroll) * 100, 2) if starting_bankroll else 0.0
    doubling_threshold = round(starting_bankroll * 2, 2)
    doubled = ending_bankroll >= doubling_threshold
    progress_to_doubling = round((ending_bankroll / doubling_threshold) * 100, 2) if doubling_threshold else 0.0
    cap_match_eligible = doubled
    cap_match_amount = round(max(0.0, ending_bankroll - starting_bankroll), 2) if doubled else 0.0
    days_remaining = max(0, (bankroll.evaluation_end_date - date.today()).days) if bankroll.evaluation_end_date else None
    month_end_review = {
        "starting_bankroll": starting_bankroll,
        "ending_bankroll": ending_bankroll,
        "net_gain_loss": net_gain,
        "growth_pct": growth_pct,
        "doubled_bankroll": doubled,
        "doubling_threshold": doubling_threshold,
        "progress_to_doubling_threshold": progress_to_doubling,
        "capital_match_eligible": cap_match_eligible,
        "recommended_match_amount": cap_match_amount,
        "next_cycle_bankroll_if_matched": round(ending_bankroll + cap_match_amount, 2),
        "evaluation_start_date": eval_start,
        "evaluation_end_date": eval_end,
        "days_remaining": days_remaining,
        "evaluation_window_closed": date.today() >= bankroll.evaluation_end_date if bankroll.evaluation_end_date else False,
    }

    # ── Broker reconciliation ─────────────────────────────────────────────────
    broker_state = get_broker_capital_state(
        internal_available_capital=raw_internal_available,
        internal_committed_capital=planning_committed,
        internal_current_bankroll=raw_internal_bankroll,
    )
    from app.services import position_lifecycle as lifecycle_svc

    lifecycle_svc.sync_lifecycles_with_broker_state(session, broker_state)
    lifecycle_svc.reconcile_order_fills_with_broker(session, broker_state=broker_state)
    from app.services import execution as execution_svc

    execution_svc.reconcile_completed_packet_outcomes(session)
    broker_dict = broker_capital_state_to_dict(broker_state)
    broker_dict["positions"] = lifecycle_svc.enrich_broker_positions_with_lifecycle(
        session,
        broker_dict.get("positions", []),
    )
    fast_recycle = _build_fast_recycle_snapshot(session, broker_dict)

    # Use broker truth whenever Alpaca is enabled and sync succeeded.
    # This applies in BOTH live mode AND sandbox/paper mode — Alpaca positions
    # are the authoritative committed-capital source regardless of execution mode.
    use_broker_truth = ALPACA_ENABLED and broker_state.sync_success

    if use_broker_truth:
        # Reconcile the current internal ledger snapshot to the broker-backed
        # reality so we do not keep comparing live capital against untouched
        # seed-era values after a successful sync.
        sync_current_ledger_to_broker(
            session,
            bankroll,
            available_capital=broker_state.available_capital,
            current_bankroll=broker_state.current_bankroll,
        )
        internal_available = float(bankroll.remaining_budget or 0.0)
        internal_bankroll = float(bankroll.current_bankroll or 0.0)
        internal_committed = float(broker_state.committed_capital or 0.0)
        broker_state.internal_available_capital = internal_available
        broker_state.internal_committed_capital = internal_committed
        broker_state.internal_current_bankroll = internal_bankroll
        broker_state.mismatch_detected = False
        broker_state.mismatch_details = None
        display_available      = broker_state.available_capital
        display_committed      = broker_state.committed_capital
        display_bankroll       = broker_state.current_bankroll
        display_funded_packets = broker_state.open_positions_count
        display_unrealized_pl  = broker_state.unrealized_pl
        display_effective_bp   = broker_state.effective_buying_power
    else:
        if ALPACA_ENABLED:
            _logger.warning(
                "get_broker_reconciled_capital_state: broker sync failed (%s) "
                "-- falling back to internal ledger",
                broker_state.sync_error,
            )
        internal_available = raw_internal_available
        internal_bankroll = raw_internal_bankroll
        internal_committed = planning_committed
        display_available      = internal_available
        display_committed      = internal_committed
        display_bankroll       = internal_bankroll
        display_funded_packets = internal_funded_count
        display_unrealized_pl  = 0.0
        display_effective_bp   = internal_available

    is_live = EXECUTION_MODE == "live"

    return {
        # ── Display-authoritative capital values ─────────────────────────────
        "available_capital":        round(display_available, 2),
        "committed_capital":        round(display_committed, 2),
        "current_bankroll":         round(display_bankroll, 2),
        "funded_packets":           display_funded_packets,
        "unrealized_pl":            round(display_unrealized_pl, 2),
        "effective_buying_power":   round(display_effective_bp, 2),
        "fast_recycle":             fast_recycle,

        # ── Internal ledger values (audit trail, realized P/L) ────────────────
        "starting_bankroll":        starting_bankroll,
        "weekly_target":            weekly_target,
        "realized_profit":          round(realized_profit, 2),
        "total_allocated":          total_allocated,
        "internal_available_capital": internal_available,
        "internal_committed_capital": internal_committed,
        "internal_current_bankroll":  internal_bankroll,

        # ── Review / target metrics ───────────────────────────────────────────
        "evaluation_start_date":    eval_start,
        "evaluation_end_date":      eval_end,
        "capital_match_eligible":   cap_match_eligible,
        "capital_match_amount":     cap_match_amount,
        "month_end_review":         month_end_review,

        # ── Strategy metadata ─────────────────────────────────────────────────
        "strategy_mode":            STRATEGY_MODE,
        "live_execution_strategy":  LIVE_EXECUTION_STRATEGY,
        "live_execution_profile":   LIVE_EXECUTION_PROFILE,
        "execution_mode":           EXECUTION_MODE,
        "is_live_mode":             is_live,

        # ── Broker sync metadata ──────────────────────────────────────────────
        "broker_mode":              broker_state.broker_mode,
        "last_broker_sync_at":      broker_state.last_sync_at,
        "broker_sync_success":      broker_state.sync_success,
        "broker_sync_error":        broker_state.sync_error,
        "mismatch_detected":        broker_state.mismatch_detected,
        "mismatch_details":         broker_state.mismatch_details,
        "open_positions_count":     broker_state.open_positions_count,
        "reserved_by_open_orders":  broker_state.reserved_by_open_orders,
        "open_buy_orders_count":    broker_state.open_buy_orders_count,
        "broker_cash":              broker_state.cash,
        "broker_buying_power":      broker_state.buying_power,
        "broker_portfolio_value":   broker_state.portfolio_value,

        # ── Full broker snapshot (positions, orders) ──────────────────────────
        "broker":                   broker_dict,
    }
