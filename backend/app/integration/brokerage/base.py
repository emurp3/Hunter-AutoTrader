"""
Brokerage adapter protocol for controlled execution providers.
"""

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class TradeOrder:
    symbol: str
    side: str
    order_type: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    time_in_force: str = "gtc"
    limit_price: Optional[float] = None
    client_order_id: Optional[str] = None


@dataclass
class TradeResult:
    order_id: str
    symbol: str
    qty: float
    side: str
    status: str
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    submitted_at: Optional[str] = None
    notional: Optional[float] = None
    provider_message: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class PositionInfo:
    symbol: str
    qty: float
    market_value: Optional[float] = None
    avg_entry_price: Optional[float] = None
    side: Optional[str] = None
    unrealized_pl: Optional[float] = None
    raw: Optional[dict] = None


@dataclass
class AccountInfo:
    account_id: str
    cash: float
    portfolio_value: float
    buying_power: float
    currency: str = "USD"
    status: str = "ACTIVE"
    raw: Optional[dict] = None


@runtime_checkable
class BrokerageAdapter(Protocol):
    def get_balance(self) -> AccountInfo: ...
    def get_positions(self) -> list[PositionInfo]: ...
    def place_order(self, order: TradeOrder) -> TradeResult: ...
    def get_order_status(self, order_id: str) -> TradeResult: ...
    def cancel_order(self, order_id: str) -> bool: ...
    def get_account_status(self) -> dict: ...

    def place_trade(self, order: TradeOrder) -> TradeResult: ...
    def get_account(self) -> AccountInfo: ...
    def get_order(self, order_id: str) -> TradeResult: ...
