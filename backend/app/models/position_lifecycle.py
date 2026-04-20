from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class PositionLifecycle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: Optional[str] = Field(default=None, index=True)
    packet_id: Optional[int] = Field(default=None, index=True)
    allocation_id: Optional[int] = Field(default=None, index=True)
    symbol: str = Field(index=True)
    status: str = Field(default="open", index=True)
    entered_at: Optional[datetime] = None
    entry_filled_at: Optional[datetime] = None
    exit_submitted_at: Optional[datetime] = None
    exited_at: Optional[datetime] = None
    first_profitable_at: Optional[datetime] = None
    hold_duration_minutes: Optional[float] = None
    time_to_realized_profit_minutes: Optional[float] = None
    max_hold_minutes: Optional[float] = None
    realized_pl: Optional[float] = None
    entry_order_id: Optional[str] = Field(default=None, index=True)
    exit_order_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
