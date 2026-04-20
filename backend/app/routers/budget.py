from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.config import APPROVAL_REQUIRED_OVER, BUDGET_STRICT_MODE, HUNTER_OPERATING_ACCOUNT_PROVIDER
from app.database.config import get_session
from app.models.marketplace import BankrollReconciliation, BankrollReconciliationCreate
from app.services import budget as budget_svc
from app.services import diagnostics as diag_svc
from app.models.budget import (
    AllocationStatus,
    BudgetAllocation,
    BudgetAllocationCreate,
    BudgetAllocationUpdate,
    BudgetOutcome,
    BudgetOutcomeCreate,
    BudgetStatus,
    ManualCapitalInjectionCreate,
    WeeklyBudget,
    WeeklyBudgetCreate,
    WeeklyBudgetUpdate,
)
from app.services.budget import (
    auto_allocate_top_packets,
    ensure_bankroll,
    get_allocations_for_budget,
    get_broker_reconciled_capital_state,
    get_budget_commander_summary,
    get_month_end_review,
    get_open_budget,
    get_outcomes_for_allocation,
    inject_manual_capital,
    is_flipped,
    mark_budget_candidate,
    next_budget_recommendation,
    open_weekly_budget,
    recalc_realized_return,
    recalc_remaining,
    refresh_budget_recommendations,
    recommend_allocation,
)

router = APIRouter(prefix="/budget", tags=["budget"])


# ── GET /budget/allocations ───────────────────────────────────────────────────

@router.get("/allocations")
def list_allocations(session: Session = Depends(get_session)) -> list:
    """List all allocations for the current bankroll cycle, newest first."""
    bankroll = ensure_bankroll(session)
    allocations = get_allocations_for_budget(session, bankroll.id)
    result = []
    for a in allocations:
        outcomes = get_outcomes_for_allocation(session, a.id)
        result.append({
            **a.model_dump(),
            "actual_return": round(sum(o.actual_return for o in outcomes), 2),
            "net_result": round(sum(o.net_result for o in outcomes), 2),
            "outcome_count": len(outcomes),
        })
    return result


@router.get("/allocations/{allocation_id}")
def get_allocation(allocation_id: int, session: Session = Depends(get_session)):
    """Single allocation with its outcomes."""
    allocation = session.get(BudgetAllocation, allocation_id)
    if not allocation:
        raise HTTPException(status_code=404, detail="Allocation not found.")
    outcomes = get_outcomes_for_allocation(session, allocation_id)
    return {
        **allocation.model_dump(),
        "outcomes": outcomes,
        "actual_return": round(sum(o.actual_return for o in outcomes), 2),
        "net_result": round(sum(o.net_result for o in outcomes), 2),
    }


# ── POST /budget/open-week ────────────────────────────────────────────────────

@router.post("/open-week", response_model=WeeklyBudget, status_code=201)
def open_week(payload: WeeklyBudgetCreate, session: Session = Depends(get_session)):
    """Initialize Hunter's rolling bankroll cycle. Fails with 409 if one is already active."""
    try:
        return open_weekly_budget(session, payload.starting_budget, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ── GET /budget/capital-state ─────────────────────────────────────────────────

@router.get("/capital-state")
def get_capital_state(session: Session = Depends(get_session)) -> dict:
    """
    Authoritative live capital state.

    In LIVE mode: broker truth overrides internal ledger for all display
    values (available_capital, committed_capital, current_bankroll,
    funded_packets, unrealized_pl).

    In SANDBOX mode: returns internal ledger values with broker state
    included for observability.

    Always includes: last_broker_sync_at, mismatch_detected,
    strategy_mode, live_execution_strategy.
    """
    try:
        capital_state = get_broker_reconciled_capital_state(session)
        open_budget = budget_svc.get_open_budget(session)
        metadata = diag_svc.build_capital_metadata(
            budget_payload={"budget": {"status": "open" if open_budget else "no_open_budget"}},
            capital_state=capital_state,
            readiness_budget_open=bool(open_budget),
        )
        diag_svc.record_success("budget.capital_state", metadata=metadata)
        if capital_state.get("broker_sync_success"):
            diag_svc.record_success(
                "broker.sync",
                metadata={
                    "last_broker_sync_at": capital_state.get("last_broker_sync_at"),
                    "broker_state_label": "broker truth",
                },
            )
        else:
            diag_svc.record_error(
                "broker.sync",
                capital_state.get("broker_sync_error") or "Broker sync unavailable",
                status="degraded",
                metadata={
                    "last_broker_sync_at": capital_state.get("last_broker_sync_at"),
                    "broker_state_label": "fallback/internal ledger",
                },
            )
        return capital_state
    except Exception as exc:
        diag_svc.record_error("budget.capital_state", exc, affected_component="budget.capital_state")
        raise


# ── GET /budget/current ───────────────────────────────────────────────────────

@router.get("/current")
def get_current(session: Session = Depends(get_session)) -> dict:
    """
    Return the active bankroll with broker-reconciled capital stats.
    In live mode, capital display values are sourced from Alpaca directly.
    """
    try:
        # Get broker-reconciled state (broker truth wins in live mode)
        reconciled = get_broker_reconciled_capital_state(session)

        budget = ensure_bankroll(session)
        allocations = get_allocations_for_budget(session, budget.id)
        review = reconciled.get("month_end_review", {})

        payload = {
        "budget": budget,
        # ── Broker-authoritative display values ──────────────────────────────
        "starting_bankroll": reconciled["starting_bankroll"],
        "current_bankroll": reconciled["current_bankroll"],
        "available_capital": reconciled["available_capital"],
        "committed_capital": reconciled["committed_capital"],
        "realized_profit": reconciled["realized_profit"],
        "unrealized_pl": reconciled["unrealized_pl"],
        "unrealized_exposure": reconciled["committed_capital"],  # compat alias
        # ── Backward-compat aliases ──────────────────────────────────────────
        "remaining_budget": reconciled["available_capital"],
        "available_budget": reconciled["available_capital"],
        "allocated_budget": reconciled["committed_capital"],
        "total_allocated": reconciled["committed_capital"],
        "realized_return": reconciled["realized_profit"],
        # ── Review / target metrics ──────────────────────────────────────────
        "net_gain_loss": review.get("net_gain_loss", 0.0),
        "roi_pct": review.get("growth_pct", 0.0),
        "flipped": review.get("doubled_bankroll", False),
        "flip_target": round(reconciled["starting_bankroll"] * 2, 2),
        "allocation_count": len(allocations),
        "evaluation_start_date": reconciled["evaluation_start_date"],
        "evaluation_end_date": reconciled["evaluation_end_date"],
        "capital_match_eligible": reconciled["capital_match_eligible"],
        "capital_match_amount": reconciled["capital_match_amount"],
        "month_end_review": review,
        # ── Strategy / mode context ──────────────────────────────────────────
        "strategy_mode": reconciled["strategy_mode"],
        "live_execution_strategy": reconciled["live_execution_strategy"],
        "execution_mode": reconciled["execution_mode"],
        # ── Broker sync metadata ─────────────────────────────────────────────
        "last_broker_sync_at": reconciled["last_broker_sync_at"],
        "broker_sync_success": reconciled["broker_sync_success"],
        "broker_mode": reconciled["broker_mode"],
        "mismatch_detected": reconciled["mismatch_detected"],
        "mismatch_details": reconciled["mismatch_details"],
        # ── Internal ledger (audit trail) ────────────────────────────────────
        "internal_available_capital": reconciled["internal_available_capital"],
        "internal_committed_capital": reconciled["internal_committed_capital"],
        "internal_current_bankroll": reconciled["internal_current_bankroll"],
        # ── Broker live positions (for position cards) ───────────────────────
        "broker": reconciled.get("broker", {}),
        "funded_packets": reconciled["funded_packets"],
        "allocations_by_source": [
            {
                "source_id": allocation.source_id,
                "allocation_name": allocation.allocation_name,
                "amount_allocated": allocation.amount_allocated,
                "status": allocation.status,
            }
            for allocation in allocations
        ],
        }
        metadata = diag_svc.build_capital_metadata(
            budget_payload=payload,
            capital_state=reconciled,
            readiness_budget_open=bool(budget_svc.get_open_budget(session)),
        )
        diag_svc.record_success("budget.current", metadata=metadata)
        return payload
    except Exception as exc:
        diag_svc.record_error("budget.current", exc, affected_component="budget.current")
        raise


# ── POST /budget/allocate ─────────────────────────────────────────────────────

@router.post("/allocate", response_model=BudgetAllocation, status_code=201)
def create_allocation(
    payload: BudgetAllocationCreate, session: Session = Depends(get_session)
):
    """
    Allocate funds from Hunter's live bankroll.

    - In strict mode, rejects if amount_allocated > remaining_budget.
    - Automatically flags approval_required for amounts over the configured threshold.
    """
    budget = ensure_bankroll(session)
    remaining = recalc_remaining(session, budget)

    if BUDGET_STRICT_MODE and payload.amount_allocated > remaining:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Insufficient budget. Requested ${payload.amount_allocated:.2f}, "
                f"available ${remaining:.2f}. "
                "Reduce the allocation or release an existing commitment."
            ),
        )

    approval_required = payload.amount_allocated > APPROVAL_REQUIRED_OVER

    record = BudgetAllocation(
        weekly_budget_id=budget.id,
        allocation_name=payload.allocation_name,
        category=payload.category,
        amount_allocated=payload.amount_allocated,
        rationale=payload.rationale,
        expected_return=payload.expected_return,
        source_id=payload.source_id,
        approval_required=approval_required,
        approved_by_commander=False,
        status=AllocationStatus.planned,
    )
    session.add(record)

    session.commit()
    session.refresh(record)
    budget_svc.record_capital_commit(
        session,
        record,
        source_id=record.source_id,
        action_packet_id=None,
        notes="Manual allocation committed from bankroll",
    )
    return record


# ── PATCH /budget/allocate/{id} ───────────────────────────────────────────────

@router.patch("/allocate/{allocation_id}", response_model=BudgetAllocation)
def update_allocation(
    allocation_id: int,
    payload: BudgetAllocationUpdate,
    session: Session = Depends(get_session),
):
    """
    Update an allocation's status, approval, or metadata.
    """
    record = session.get(BudgetAllocation, allocation_id)
    if not record:
        raise HTTPException(status_code=404, detail="Allocation not found.")

    was_canceled = record.status == AllocationStatus.canceled
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    session.add(record)
    session.commit()
    session.refresh(record)
    if (
        "status" in update_data
        and update_data["status"] == AllocationStatus.canceled
        and not was_canceled
    ):
        budget_svc.record_capital_release(
            session,
            record,
            source_id=record.source_id,
            action_packet_id=None,
            notes="Manual allocation cancellation",
        )
    return record


# ── POST /budget/outcome ──────────────────────────────────────────────────────

@router.post("/outcome", response_model=BudgetOutcome, status_code=201)
def record_outcome(
    payload: BudgetOutcomeCreate, session: Session = Depends(get_session)
):
    """
    Record the real outcome for an allocation inside the rolling bankroll model.
    """
    allocation = session.get(BudgetAllocation, payload.allocation_id)
    if not allocation:
        raise HTTPException(status_code=404, detail="Allocation not found.")

    net = round(payload.actual_return - allocation.amount_allocated, 2)
    outcome = BudgetOutcome(
        allocation_id=payload.allocation_id,
        actual_return=payload.actual_return,
        net_result=net,
        outcome_notes=payload.outcome_notes,
        success_reason=payload.success_reason,
        failure_reason=payload.failure_reason,
        time_to_completion_hours=payload.time_to_completion_hours,
        source_id=payload.source_id or allocation.source_id,
        strategy_id=payload.strategy_id,
        action_packet_id=payload.action_packet_id,
        lane=payload.lane,
        category=payload.category,
    )
    session.add(outcome)

    session.commit()
    session.refresh(outcome)

    # Propagate actual_return to any linked strategy
    if allocation.source_id:
        from sqlmodel import select as _select
        from app.models.strategy import Strategy
        strategy = session.exec(
            _select(Strategy).where(Strategy.linked_opportunity_source_id == allocation.source_id)
        ).first()
        if strategy:
            from datetime import datetime, timezone
            strategy.actual_return = round((strategy.actual_return or 0.0) + payload.actual_return, 2)
            strategy.updated_at = datetime.now(timezone.utc)
            session.add(strategy)
            session.commit()

    return outcome


# ── GET /budget/scoreboard ────────────────────────────────────────────────────

@router.get("/scoreboard")
def scoreboard(session: Session = Depends(get_session)) -> list[dict]:
    """Historical summary of bankroll cycles, newest first."""
    budgets = session.exec(
        select(WeeklyBudget).order_by(WeeklyBudget.week_start_date.desc())
    ).all()

    rows = []
    for b in budgets:
        realized = recalc_realized_return(session, b)
        total_alloc = round(b.starting_budget - recalc_remaining(session, b), 2)
        roi_pct = (
            round((realized - b.starting_budget) / b.starting_budget * 100, 2)
            if b.starting_budget > 0
            else 0.0
        )
        rows.append(
            {
                "id": b.id,
                "week_start": b.week_start_date.isoformat(),
                "week_end": b.week_end_date.isoformat(),
                "starting_budget": b.starting_budget,
                "starting_bankroll": b.starting_bankroll,
                "current_bankroll": b.current_bankroll,
                "total_allocated": total_alloc,
                "realized_return": realized,
                "net_gain_loss": round(realized - total_alloc, 2),
                "roi_pct": roi_pct,
                "flipped": is_flipped(b),
                "status": b.status.value,
            }
        )
    return rows


# ── POST /budget/auto-allocate/{source_id} ───────────────────────────────────

@router.post("/auto-allocate/{source_id}", status_code=201)
def auto_allocate(source_id: str, session: Session = Depends(get_session)):
    """
    One-step budget allocation for an income source.
    Calls recommend_allocation() to determine the amount, then creates the allocation.
    Advances the source to 'budgeted' status.
    Returns 422 if no open budget, insufficient funds, or source not found.
    """
    try:
        rec = recommend_allocation(source_id, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if rec.get("recommended_allocation") is None:
        raise HTTPException(status_code=422, detail=rec.get("reason", "Cannot recommend allocation."))

    amount = rec["recommended_allocation"]
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Recommended allocation is $0 — insufficient profit signal.")

    budget = get_open_budget(session)
    if not budget:
        raise HTTPException(status_code=404, detail="No open weekly budget.")

    remaining = recalc_remaining(session, budget)
    if BUDGET_STRICT_MODE and amount > remaining:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient budget. Recommended ${amount:.2f}, available ${remaining:.2f}.",
        )

    approval_required = amount > APPROVAL_REQUIRED_OVER

    from app.models.income_source import IncomeSource
    source = session.exec(select(IncomeSource).where(IncomeSource.source_id == source_id)).first()

    record = BudgetAllocation(
        weekly_budget_id=budget.id,
        allocation_name=source.description[:120] if source else source_id,
        category=AllocationCategory.other,
        amount_allocated=amount,
        rationale=f"Auto-allocated: {rec.get('reason', '')}",
        expected_return=source.estimated_profit if source else None,
        source_id=source_id,
        approval_required=approval_required,
        approved_by_commander=False,
        status=AllocationStatus.planned,
    )
    session.add(record)
    budget.remaining_budget = round(remaining - amount, 2)
    session.add(budget)
    session.commit()
    session.refresh(record)

    # Advance source to budgeted
    try:
        mark_budget_candidate(source_id, session)
    except Exception:
        pass

    return {
        "allocation": record,
        "recommendation": rec,
        "approval_required": approval_required,
    }


@router.post("/refresh-recommendations")
def refresh_recommendations(session: Session = Depends(get_session)):
    return refresh_budget_recommendations(session)


@router.post("/auto-allocate-top")
def auto_allocate_top(limit: int = 5, session: Session = Depends(get_session)):
    try:
        return auto_allocate_top_packets(session, max_packets=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── POST /budget/close-week ───────────────────────────────────────────────────

@router.post("/close-week")
def close_week(session: Session = Depends(get_session)):
    """Close the active bankroll cycle after syncing capital metrics."""
    budget = get_open_budget(session)
    if not budget:
        raise HTTPException(status_code=404, detail="No active bankroll cycle.")

    budget.remaining_budget = recalc_remaining(session, budget)
    budget.realized_return = recalc_realized_return(session, budget)
    budget.status = BudgetStatus.closed
    session.add(budget)
    session.commit()
    session.refresh(budget)
    return {
        "closed": True,
        "week_start": budget.week_start_date.isoformat(),
        "week_end": budget.week_end_date.isoformat(),
        "starting_budget": budget.starting_budget,
        "realized_return": budget.realized_return,
        "remaining_budget": budget.remaining_budget,
        "flipped": is_flipped(budget),
    }


@router.get("/review")
def bankroll_review(session: Session = Depends(get_session)) -> dict:
    budget = ensure_bankroll(session)
    return get_month_end_review(session, budget)


@router.post("/inject-capital")
def inject_capital(payload: ManualCapitalInjectionCreate, session: Session = Depends(get_session)) -> dict:
    return inject_manual_capital(session, payload)


# ── GET /budget/recommend/{source_id} ────────────────────────────────────────

@router.get("/recommend/{source_id}")
def get_recommendation(source_id: str, session: Session = Depends(get_session)) -> dict:
    """
    Return a suggested allocation amount for the given income source.
    Based on estimated_profit × priority band multiplier, capped at 30% of remaining budget.
    Does NOT create an allocation — call POST /budget/allocate to act on it.
    """
    try:
        return recommend_allocation(source_id, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── POST /budget/candidate/{source_id} ───────────────────────────────────────

@router.post("/candidate/{source_id}")
def mark_candidate(source_id: str, session: Session = Depends(get_session)):
    """Advance an income source to 'budgeted' status and log the state change."""
    try:
        return mark_budget_candidate(source_id, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── GET /budget/transactions ─────────────────────────────────────────────────

@router.get("/transactions")
def list_transactions(
    limit: int = 200,
    session: Session = Depends(get_session),
) -> dict:
    """
    Unified transaction log: all allocations across all budget cycles,
    with their outcomes merged in. Sorted newest first.
    """
    from sqlmodel import select as _select
    from app.models.budget import BudgetAllocation, BudgetOutcome, WeeklyBudget

    allocations = session.exec(
        _select(BudgetAllocation).order_by(BudgetAllocation.created_at.desc()).limit(limit)
    ).all()

    all_outcomes = session.exec(_select(BudgetOutcome)).all()
    outcomes_by_alloc: dict[int, list[BudgetOutcome]] = {}
    for o in all_outcomes:
        outcomes_by_alloc.setdefault(o.allocation_id, []).append(o)

    budget_names: dict[int, str] = {}
    budgets = session.exec(_select(WeeklyBudget)).all()
    for b in budgets:
        budget_names[b.id] = (
            b.week_start_date.isoformat()
            if hasattr(b.week_start_date, "isoformat")
            else str(b.week_start_date)
        )

    rows = []
    for a in allocations:
        outcomes = outcomes_by_alloc.get(a.id, [])
        actual_return = round(sum(o.actual_return for o in outcomes), 2)
        net_result = round(sum(o.net_result for o in outcomes), 2)
        rows.append({
            "id": a.id,
            "timestamp": a.created_at.isoformat() if a.created_at else None,
            "allocation_name": a.allocation_name,
            "source_id": a.source_id,
            "category": a.category,
            "amount_committed": float(a.amount_allocated),
            "expected_return": float(a.expected_return) if a.expected_return else None,
            "actual_return": actual_return if outcomes else None,
            "net_result": net_result if outcomes else None,
            "status": a.status if isinstance(a.status, str) else a.status.value,
            "approval_required": a.approval_required,
            "approved_by_commander": a.approved_by_commander,
            "budget_cycle": budget_names.get(a.weekly_budget_id, "—"),
            "outcome_count": len(outcomes),
            "outcome_notes": outcomes[0].outcome_notes if outcomes else None,
        })

    return {
        "total": len(rows),
        "transactions": rows,
    }


# ── GET /budget/weekly-report ─────────────────────────────────────────────────

@router.get("/weekly-report")
def budget_weekly_report(session: Session = Depends(get_session)) -> dict:
    """
    Full detailed budget report for the current open week.
    Includes allocation breakdown, top/worst performer, and next-week recommendation.
    """
    budget = get_open_budget(session)
    if not budget:
        raise HTTPException(status_code=404, detail="No open weekly budget.")

    summary = get_budget_commander_summary(session)
    return {"budget_commander_summary": summary}



# ── POST /budget/reconcile ───────────────────────────────────────────────────

@router.post("/reconcile")
def reconcile_broker(session: Session = Depends(get_session)) -> dict:
    """
    Force an immediate broker reconciliation and return the updated capital state.
    Useful for the dashboard refresh button in live mode.
    """
    return get_broker_reconciled_capital_state(session)
