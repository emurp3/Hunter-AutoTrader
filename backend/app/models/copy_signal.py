"""
CopySignal — Public-Signal Copy Engine data models.

Tracks publicly disclosed trading activity (Congressional STOCK Act,
SEC Form 4 insider filings) from ingestion through scoring, routing,
and optional execution.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class SignalSource(str, Enum):
    congress_senate = "congress_senate"
    congress_house  = "congress_house"
    sec_form4       = "sec_form4"   # Corporate insider (executives, directors)
    quiver_quant    = "quiver_quant"
    manual          = "manual"


class SignalDecision(str, Enum):
    pending        = "pending"
    mirror         = "mirror"          # Full position, high confidence
    partial_mirror = "partial_mirror"  # Scaled-down position, moderate confidence
    watchlist      = "watchlist"       # Monitor but don't act yet
    reject         = "reject"          # Not actionable


class SignalRisk(str, Enum):
    low    = "low"
    medium = "medium"
    high   = "high"


class CopySignal(SQLModel, table=True):
    """
    A normalised signal extracted from a public disclosure.
    One row per disclosed trade or transaction.
    """
    __tablename__ = "copy_signals"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Source metadata
    source: str = Field(index=True)       # SignalSource value
    source_id: str = Field(index=True)   # External reference / dedupe key
    filer_name: str
    filer_type: str   # senator, representative, ceo, director, etc.
    committee: Optional[str] = None       # Congressional committee (if applicable)

    # Trade details
    ticker: str = Field(index=True)
    asset_type: str = "stock"             # stock, option, etf, crypto
    action: str                           # buy, sell, exchange
    amount_low: Optional[float] = None    # USD range low
    amount_high: Optional[float] = None   # USD range high
    amount_midpoint: Optional[float] = None

    # Timing
    trade_date: Optional[datetime] = None
    disclosed_at: Optional[datetime] = None
    latency_hours: Optional[float] = None  # Hours from trade to public disclosure

    # Scoring & routing
    confidence_score: float = 0.0         # 0.0 – 1.0
    decision: str = "pending"             # SignalDecision value
    decision_reason: Optional[str] = None
    decision_at: Optional[datetime] = None
    risk_level: str = "medium"            # SignalRisk value
    auto_execute: bool = False

    # Execution linkage
    executed: bool = False
    execution_order_id: Optional[str] = None
    execution_at: Optional[datetime] = None
    execution_pnl: Optional[float] = None

    # Raw / notes
    raw_json: Optional[str] = None
    notes: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None


class SignalScanState(SQLModel, table=True):
    """
    Per-source scan state — tracks last fetch so we don't re-ingest old signals.
    """
    __tablename__ = "signal_scan_state"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(unique=True, index=True)
    last_scan_at: Optional[datetime] = None
    last_cursor: Optional[str] = None    # page token / offset / date
    last_count: int = 0
    last_error: Optional[str] = None
    total_ingested: int = 0
