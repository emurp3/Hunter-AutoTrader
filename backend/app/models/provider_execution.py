from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ProviderExecution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    packet_id: int = Field(index=True)
    source_id: str = Field(index=True)
    allocation_id: Optional[int] = Field(default=None, index=True)
    provider: str = Field(index=True)
    provider_mode: str = Field(default='paper')
    external_order_id: str = Field(index=True)
    symbol: str = Field(index=True)
    order_side: str
    order_type: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    limit_price: Optional[float] = None
    submitted_at: Optional[datetime] = None
    execution_status: str = Field(index=True)
    provider_message: Optional[str] = None
    raw_response_json: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
