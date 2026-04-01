from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class BudgetStatus(str, Enum):
    open = "open"
    closed = "closed"
    reviewed = "reviewed"


class AllocationCategory(str, Enum):
    trading = "trading"
    software = "software"
    marketing = "marketing"
    tools = "tools"
    services = "services"
    experiments = "experiments"
    other = "other"


class AllocationStatus(str, Enum):
    planned = "planned"
    active = "active"
    spent = "spent"
    canceled = "canceled"
    complete = "complete"


class BankrollLedgerEntryType(str, Enum):
    allocation_committed = "allocation_committed"
    allocation_released = "allocation_released"
    execution_completed = "execution_completed"
    execution_failed = "execution_failed"
    manual_injection = "manual_injection"
    capital_match = "capital_match"


class WeeklyBudgetBase(SQLModel):
    # Legacy fields kept for historical compatibility
    week_start_date: date
    week_end_date: date
    starting_budget: float = Field(ge=0)
    remaining_budget: float = Field(ge=0)
    realized_return: float = Field(default=0.0)
    status: BudgetStatus = Field(default=BudgetStatus.open)
    notes: Optional[str] = None

    # Rolling bankroll model
    starting_bankroll: float = Field(default=100.0, ge=0)
    current_bankroll: float = Field(default=100.0, ge=0)
    evaluation_start_date: date = Field(default_factory=date.today)
    evaluation_end_date: date = Field(default_factory=date.today)
    capital_match_eligible: bool = Field(default=False)
    capital_match_amount: float = Field(default=0.0, ge=0)
    manual_injection_total: float = Field(default=0.0, ge=0)


class WeeklyBudget(WeeklyBudgetBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class WeeklyBudgetCreate(SQLModel):
    starting_budget: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None


class WeeklyBudgetUpdate(SQLModel):
    status: Optional[BudgetStatus] = None
    notes: Optional[str] = None


class BudgetAllocationBase(SQLModel):
    weekly_budget_id: int = Field(foreign_key="weeklybudget.id")
    allocation_name: str
    category: AllocationCategory
    amount_allocated: float = Field(ge=0)
    rationale: str
    expected_return: Optional[float] = Field(default=None, ge=0)
    source_id: Optional[str] = Field(default=None, index=True)
    approval_required: bool = Field(default=False)
    approved_by_commander: bool = Field(default=False)
    status: AllocationStatus = Field(default=AllocationStatus.planned)


class BudgetAllocation(BudgetAllocationBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BudgetAllocationCreate(SQLModel):
    allocation_name: str
    category: AllocationCategory
    amount_allocated: float = Field(ge=0)
    rationale: str
    expected_return: Optional[float] = Field(default=None, ge=0)
    source_id: Optional[str] = None


class BudgetAllocationUpdate(SQLModel):
    allocation_name: Optional[str] = None
    category: Optional[AllocationCategory] = None
    rationale: Optional[str] = None
    expected_return: Optional[float] = Field(default=None, ge=0)
    source_id: Optional[str] = None
    approved_by_commander: Optional[bool] = None
    status: Optional[AllocationStatus] = None


class BudgetOutcomeBase(SQLModel):
    allocation_id: int = Field(foreign_key="budgetallocation.id")
    actual_return: float
    outcome_notes: Optional[str] = None
    success_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    time_to_completion_hours: Optional[float] = None
    source_id: Optional[str] = Field(default=None, index=True)
    strategy_id: Optional[str] = Field(default=None, index=True)
    action_packet_id: Optional[int] = Field(default=None, index=True)
    lane: Optional[str] = None
    category: Optional[str] = None


class BudgetOutcome(BudgetOutcomeBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    net_result: float = Field(default=0.0)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BudgetOutcomeCreate(SQLModel):
    allocation_id: int
    actual_return: float
    outcome_notes: Optional[str] = None
    success_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    time_to_completion_hours: Optional[float] = None
    source_id: Optional[str] = None
    strategy_id: Optional[str] = None
    action_packet_id: Optional[int] = None
    lane: Optional[str] = None
    category: Optional[str] = None


class BudgetOutcomeUpdate(SQLModel):
    outcome_notes: Optional[str] = None
    success_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    time_to_completion_hours: Optional[float] = None


class BankrollLedgerEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    weekly_budget_id: int = Field(foreign_key="weeklybudget.id", index=True)
    entry_type: BankrollLedgerEntryType = Field(index=True)
    source_id: Optional[str] = Field(default=None, index=True)
    allocation_id: Optional[int] = Field(default=None, index=True)
    action_packet_id: Optional[int] = Field(default=None, index=True)
    amount_delta_current: float = Field(default=0.0)
    amount_delta_committed: float = Field(default=0.0)
    amount_delta_available: float = Field(default=0.0)
    notes: Optional[str] = None
    current_bankroll_after: Optional[float] = None
    committed_capital_after: Optional[float] = None
    available_capital_after: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ManualCapitalInjectionCreate(SQLModel):
    amount: float = Field(gt=0)
    notes: Optional[str] = None
