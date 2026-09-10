"""
Real production defect (found via the inventory diagnostic's
equities_failure_reasons grouping, 2026-09-10): 116 of 169 recorded
equities-trade failures were "Object of type UUID is not JSON
serializable" — raised AFTER adapter.place_order() already succeeded at
the broker, while building the local ProviderExecution record
(execution.py's json.dumps(result.raw)). Alpaca's SDK returns UUID/
datetime fields as native Python objects from response.model_dump();
without mode="json" those objects survive into TradeResult.raw and blow
up json.dumps() downstream — a real trade with the local bookkeeping
crashing right after, misrecorded as "skipped before broker submission."
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.integration.brokerage.alpaca import AlpacaAdapter


class _FakeEnum:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeOrderResponse:
    """Mimics an alpaca-py order response closely enough to exercise
    _map_result: UUID fields are real uuid.UUID objects, and model_dump
    behaves like a real pydantic model — mode="json" stringifies them,
    a bare call does not."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.client_order_id = uuid.uuid4()
        self.symbol = "AAPL"
        self.qty = "1"
        self.side = _FakeEnum("buy")
        self.status = _FakeEnum("filled")
        self.filled_qty = "1"
        self.filled_avg_price = "150.00"
        self.submitted_at = datetime.now(timezone.utc)
        self.notional = None
        self.status_message = None

    def model_dump(self, mode: str | None = None):
        if mode == "json":
            return {"id": str(self.id), "client_order_id": str(self.client_order_id), "symbol": self.symbol}
        return {"id": self.id, "client_order_id": self.client_order_id, "symbol": self.symbol}


def _adapter() -> AlpacaAdapter:
    # Bypass __init__ (which builds a real alpaca-py TradingClient) — we
    # only need _map_result, a pure function of the response object.
    return AlpacaAdapter.__new__(AlpacaAdapter)


def test_map_result_calls_model_dump_with_json_mode():
    adapter = _adapter()
    response = _FakeOrderResponse()

    result = adapter._map_result(response)

    assert result.raw == {"id": str(response.id), "client_order_id": str(response.client_order_id), "symbol": "AAPL"}
    assert isinstance(result.raw["id"], str)


def test_map_result_raw_is_always_json_serializable():
    """The actual regression: this must never raise TypeError, since a
    real broker order has already been placed by the time this runs."""
    adapter = _adapter()
    response = _FakeOrderResponse()

    result = adapter._map_result(response)

    json.dumps(result.raw)  # must not raise


def test_map_result_with_bare_model_dump_would_have_broken_json_dumps():
    """Documents the actual bug this fixes: without mode="json", the raw
    UUID objects survive and json.dumps() on them raises exactly the
    error found in production."""
    response = _FakeOrderResponse()
    bare = response.model_dump()  # no mode= — the old, buggy call

    with pytest.raises(TypeError, match="not JSON serializable"):
        json.dumps(bare)


def test_execution_py_defensive_default_str_survives_a_uuid_leak():
    """Belt-and-suspenders: even if some other response type leaks a raw
    UUID into result.raw, the json.dumps(..., default=str) call site in
    execution.py must not crash the whole submission after a real order
    was already placed."""
    leaky = {"id": uuid.uuid4(), "note": "still must not crash"}

    serialized = json.dumps(leaky, default=str)

    assert json.loads(serialized)["note"] == "still must not crash"
