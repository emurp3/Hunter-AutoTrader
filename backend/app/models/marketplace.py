"""
Marketplace models for the Facebook Marketplace compliant execution lane.

Supports two provider integrations (API2Cart, AutoDS) and a manual mode.
All marketplace activity is written to MarketplaceLedgerEntry so the ledger
and transaction log maintain a complete record.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class MarketplaceLane(str, Enum):
    facebook_marketplace_compliant = "facebook_marketplace_compliant"


class MarketplaceRoutingLabel(str, Enum):
    listing_candidate = "listing_candidate"
    repricing_candidate = "repricing_candidate"
    fulfillment_followup = "fulfillment_followup"
    customer_message_needed = "customer_message_needed"
    policy_blocked = "policy_blocked"


class MarketplaceProvider(str, Enum):
    api2cart_facebook_marketplace = "api2cart_facebook_marketplace"
    autods_facebook_marketplace = "autods_facebook_marketplace"
    manual = "manual"


class MarketplaceExecutionState(str, Enum):
    pending = "pending"
    listing_prepared = "listing_prepared"
    listed = "listed"
    repriced = "repriced"
    fulfillment_in_progress = "fulfillment_in_progress"
    fulfilled = "fulfilled"
    blocked = "blocked"
    policy_blocked = "policy_blocked"
    completed = "completed"
    failed = "failed"


class MarketplaceActionType(str, Enum):
    assign_lane = "assign_lane"
    prepare_listing = "prepare_listing"
    list = "list"
    reprice = "reprice"
    fulfill = "fulfill"
    send_message = "send_message"
    block = "block"
    complete = "complete"
    fail = "fail"


# ── Ledger table ──────────────────────────────────────────────────────────────
# Every marketplace action writes a row here.  Required columns match PART 2-E.

class MarketplaceLedgerEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = Field(index=True)                  # e.g. api2cart_facebook_marketplace
    source_id: str = Field(index=True)
    action_type: str = Field(index=True)               # MarketplaceActionType value
    committed_amount: float = Field(default=0.0)
    expected_profit: float = Field(default=0.0)
    realized_profit: Optional[float] = None
    status: str = Field(index=True)                    # MarketplaceExecutionState value
    notes: Optional[str] = None
    external_listing_id: Optional[str] = None
    external_order_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Customer message log ──────────────────────────────────────────────────────

class MarketplaceMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)
    provider: str
    message_type: str          # inquiry_response | price_negotiation | follow_up
    message_content: str
    sent_at: Optional[datetime] = None
    status: str = Field(default="drafted")  # drafted | sent | rate_limited | rejected
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Assign payload ────────────────────────────────────────────────────────────

class MarketplaceLaneAssign(SQLModel):
    """Body for POST /marketplace/assign/{source_id}."""
    routing_label: MarketplaceRoutingLabel
    provider: MarketplaceProvider = MarketplaceProvider.manual
    committed_amount: float = Field(default=0.0, ge=0)
    expected_profit: float = Field(default=0.0, ge=0)
    notes: Optional[str] = None


# ── Bankroll reconciliation ───────────────────────────────────────────────────

class BankrollReconciliation(SQLModel, table=True):
    """Stores manual reconciliation snapshots comparing internal ledger to
    the real operating account (Robins Financial checking or other provider)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(default="robins_financial", index=True)
    reported_balance: float
    internal_balance: float
    discrepancy: float          # reported - internal
    discrepancy_pct: float
    status: str                 # reconciled | minor_discrepancy | major_discrepancy
    notes: Optional[str] = None
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BankrollReconciliationCreate(SQLModel):
    reported_balance: float
    provider: str = "robins_financial"
    notes: Optional[str] = None
