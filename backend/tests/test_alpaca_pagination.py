"""
Track A item 3 (Commander, 2026-09-10): reconciliation needs full broker
order history, not just the most-recent page. Verifies
AlpacaAdapter.list_orders_paginated pages via the `until` cursor on
submitted_at, never re-fetches the same order twice, and is bounded by
max_pages so it can never become an unbounded broker-history scan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.integration.brokerage.alpaca import AlpacaAdapter


class _FakeEnum:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeOrder:
    def __init__(self, order_id: str, submitted_at: datetime) -> None:
        self.id = order_id
        self.client_order_id = f"client-{order_id}"
        self.symbol = "AAPL"
        self.qty = "1"
        self.side = _FakeEnum("buy")
        self.status = _FakeEnum("filled")
        self.filled_qty = "1"
        self.filled_avg_price = "150.00"
        self.submitted_at = submitted_at
        self.notional = None
        self.status_message = None

    def model_dump(self, mode: str | None = None):
        if mode == "json":
            return {"id": str(self.id), "client_order_id": self.client_order_id, "symbol": self.symbol}
        return {"id": self.id, "client_order_id": self.client_order_id, "symbol": self.symbol}


class _FakeGetOrdersRequest:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _adapter_with_pages(pages: list[list[_FakeOrder]]):
    adapter = AlpacaAdapter.__new__(AlpacaAdapter)
    calls: list[_FakeGetOrdersRequest] = []
    remaining = list(pages)

    class _FakeClient:
        def get_orders(self, filter):
            calls.append(filter)
            if not remaining:
                return []
            return remaining.pop(0)

    adapter._client = _FakeClient()
    adapter._GetOrdersRequest = _FakeGetOrdersRequest
    return adapter, calls


_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_single_short_page_stops_after_one_call():
    orders = [_FakeOrder("o1", _NOW), _FakeOrder("o2", _NOW - timedelta(minutes=1))]
    adapter, calls = _adapter_with_pages([orders])

    result = adapter.list_orders_paginated(max_pages=10, page_size=5)

    assert len(calls) == 1
    assert "until" not in calls[0].kwargs
    assert [r.order_id for r in result] == ["o1", "o2"]


def test_full_page_triggers_a_second_call_with_until_cursor():
    page1 = [_FakeOrder("o1", _NOW), _FakeOrder("o2", _NOW - timedelta(minutes=1))]
    page2 = [_FakeOrder("o3", _NOW - timedelta(minutes=2))]
    adapter, calls = _adapter_with_pages([page1, page2])

    result = adapter.list_orders_paginated(max_pages=10, page_size=2)

    assert len(calls) == 2
    assert "until" not in calls[0].kwargs
    assert calls[1].kwargs["until"] == _NOW - timedelta(minutes=1) - timedelta(microseconds=1)
    assert [r.order_id for r in result] == ["o1", "o2", "o3"]


def test_bounded_by_max_pages_even_when_every_page_is_full():
    pages = [[_FakeOrder(f"o{i}-{j}", _NOW - timedelta(minutes=j)) for j in range(2)] for i in range(50)]
    adapter, calls = _adapter_with_pages(pages)

    result = adapter.list_orders_paginated(max_pages=3, page_size=2)

    assert len(calls) == 3
    assert len(result) == 6


def test_empty_first_page_returns_empty_list():
    adapter, calls = _adapter_with_pages([[]])

    result = adapter.list_orders_paginated(max_pages=10, page_size=5)

    assert result == []
    assert len(calls) == 1
