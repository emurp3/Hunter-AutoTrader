"""
Alpaca paper-trading adapter.
"""

from __future__ import annotations

from typing import Optional

from app.integration.brokerage.base import AccountInfo, PositionInfo, TradeOrder, TradeResult
from app.config import (
    ALPACA_API_KEY,
    ALPACA_BASE_URL,
    ALPACA_ENABLED,
    ALPACA_PAPER,
    ALPACA_SECRET_KEY,
)


class AlpacaAdapter:
    def __init__(self, api_key: str, secret_key: str, *, paper: bool = True, base_url: Optional[str] = None):
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest

        self._paper = paper
        self._client = TradingClient(api_key, secret_key, paper=paper, url_override=base_url)
        self._GetOrdersRequest = GetOrdersRequest
        self._LimitOrderRequest = LimitOrderRequest
        self._MarketOrderRequest = MarketOrderRequest
        self._OrderSide = OrderSide
        self._TimeInForce = TimeInForce

    def _map_side(self, side: str):
        return self._OrderSide.BUY if side.lower() == 'buy' else self._OrderSide.SELL

    def _map_tif(self, tif: str):
        mapping = {
            'gtc': self._TimeInForce.GTC,
            'day': self._TimeInForce.DAY,
            'ioc': self._TimeInForce.IOC,
            'fok': self._TimeInForce.FOK,
        }
        return mapping.get((tif or 'gtc').lower(), self._TimeInForce.GTC)

    def get_balance(self) -> AccountInfo:
        acct = self._client.get_account()
        return AccountInfo(
            account_id=str(acct.id),
            cash=float(acct.cash),
            portfolio_value=float(acct.portfolio_value),
            buying_power=float(acct.buying_power),
            currency=acct.currency or 'USD',
            status=str(acct.status),
            raw=acct.model_dump() if hasattr(acct, 'model_dump') else None,
        )

    def get_positions(self) -> list[PositionInfo]:
        positions = self._client.get_all_positions()
        return [
            PositionInfo(
                symbol=str(position.symbol),
                qty=float(position.qty),
                market_value=float(position.market_value) if position.market_value is not None else None,
                avg_entry_price=float(position.avg_entry_price) if position.avg_entry_price is not None else None,
                side=str(position.side.value) if hasattr(position.side, 'value') else str(position.side),
                unrealized_pl=float(position.unrealized_pl) if position.unrealized_pl is not None else None,
                raw=position.model_dump() if hasattr(position, 'model_dump') else None,
            )
            for position in positions
        ]

    def place_order(self, order: TradeOrder) -> TradeResult:
        if order.qty is None and order.notional is None:
            raise ValueError('Either qty or notional is required for an Alpaca order.')

        side = self._map_side(order.side)
        tif = self._map_tif(order.time_in_force)
        payload = {
            'symbol': order.symbol,
            'side': side,
            'time_in_force': tif,
            'client_order_id': order.client_order_id,
        }
        if order.qty is not None:
            payload['qty'] = order.qty
        if order.notional is not None:
            payload['notional'] = order.notional

        if order.order_type == 'limit' and order.limit_price is not None:
            payload['limit_price'] = order.limit_price
            req = self._LimitOrderRequest(**payload)
        else:
            req = self._MarketOrderRequest(**payload)

        response = self._client.submit_order(req)
        return self._map_result(response)

    def get_order_status(self, order_id: str) -> TradeResult:
        response = self._client.get_order_by_id(order_id)
        return self._map_result(response)

    def cancel_order(self, order_id: str) -> bool:
        self._client.cancel_order_by_id(order_id)
        return True

    def get_account_status(self) -> dict:
        account = self.get_balance()
        return {
            'provider': 'alpaca',
            'enabled': True,
            'mode': 'paper' if self._paper else 'live',
            'connected': True,
            'status': account.status,
            'account_id': account.account_id,
            'buying_power': account.buying_power,
            'cash': account.cash,
        }

    def list_orders(self, limit: int = 20) -> list[TradeResult]:
        request = self._GetOrdersRequest(status='all', limit=limit, nested=False)
        return [self._map_result(order) for order in self._client.get_orders(filter=request)]

    def place_trade(self, order: TradeOrder) -> TradeResult:
        return self.place_order(order)

    def get_account(self) -> AccountInfo:
        return self.get_balance()

    def get_order(self, order_id: str) -> TradeResult:
        return self.get_order_status(order_id)

    def _map_result(self, response) -> TradeResult:
        filled_qty = float(response.filled_qty or 0)
        filled_avg = float(response.filled_avg_price) if response.filled_avg_price else None
        submitted_at = response.submitted_at.isoformat() if getattr(response, 'submitted_at', None) else None
        notional = float(response.notional) if getattr(response, 'notional', None) is not None else None
        return TradeResult(
            order_id=str(response.id),
            symbol=str(response.symbol),
            qty=float(response.qty or 0),
            side=str(response.side.value) if hasattr(response.side, 'value') else str(response.side),
            status=str(response.status.value) if hasattr(response.status, 'value') else str(response.status),
            filled_qty=filled_qty,
            filled_avg_price=filled_avg,
            submitted_at=submitted_at,
            notional=notional,
            provider_message=str(getattr(response, 'status_message', '') or ''),
            raw=response.model_dump() if hasattr(response, 'model_dump') else None,
        )


def get_alpaca_adapter() -> AlpacaAdapter:
    if not ALPACA_ENABLED:
        raise EnvironmentError('Alpaca execution is disabled. Set ALPACA_ENABLED=true to activate.')
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise EnvironmentError('Missing Alpaca credentials. Set the appropriate API key and secret for the current EXECUTION_MODE.')
    return AlpacaAdapter(
        ALPACA_API_KEY,
        ALPACA_SECRET_KEY,
        paper=ALPACA_PAPER,
        base_url=ALPACA_BASE_URL,
    )
