"""
Recovery-board Track A item 3 (Commander, 2026-09-10): reconcile
historical broker activity WITHOUT resubmitting anything. Read-only
against the broker; matches broker orders to Hunter's own local records
using reliable identifiers only — an exact broker order id, or a
client_order_id Hunter itself assigned (f"hunter-pkt-{packet_id}",
added in execution.py's submit_packet_trade fix). Timestamp/symbol/
quantity similarity is never used to attribute an order — an order that
doesn't match by exact identifier stays unmatched.

This is distinct from app.services.broker_reconciliation, which
reconciles current broker CAPITAL STATE (cash/buying-power/positions)
against Hunter's internal budget ledger. This module reconciles
HISTORICAL ORDER RECORDS — did Hunter's local database ever write down
what the broker actually did.

Two local paths can already hold a broker order id:
  - ProviderExecution.external_order_id (the packet-based
    submit_packet_trade() path)
  - PositionLifecycle.entry_order_id / exit_order_id (RECYCLE's
    execute_entries()/execute_exits(), which calls place_order()
    directly)

A broker order matches neither, but carries Hunter's own
"hunter-pkt-{id}" client_order_id, only when it was placed after the
7a3d92b deploy (2026-09-10) — that is a real, Hunter-assigned
identifier, not an inference, so a missing ProviderExecution row for it
is restored here with an explicit reconciliation marker. Orders placed
before that deploy (including the ones lost to the UUID-serialization
bug) carry no such identifier and cannot be reliably attributed to a
specific packet from broker data alone; they are reported as unmatched,
and the ActionPacket rows already known to correspond to a real but
unrecorded broker fill are reported separately as "internal packets
with uncertain external outcomes."
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.integration.brokerage.alpaca import get_alpaca_adapter
from app.models.action_packet import ActionPacket
from app.models.position_lifecycle import PositionLifecycle
from app.models.provider_execution import ProviderExecution

logger = logging.getLogger("hunter.broker_history_reconciliation")

_MARKER_PREFIX = "broker_history_reconciliation"
_CLIENT_ORDER_ID_RE = re.compile(r"^hunter-pkt-(\d+)$")

_UNCERTAIN_OUTCOME_PREFIXES = (
    "Trade skipped before broker submission: Object of type UUID",
    "BROKER_ACCEPTED_RECORDING_INCOMPLETE",
)


def _extract_packet_id(client_order_id: Optional[str]) -> Optional[int]:
    if not client_order_id:
        return None
    match = _CLIENT_ORDER_ID_RE.match(client_order_id)
    if not match:
        return None
    return int(match.group(1))


def _parse_submitted_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def reconcile_broker_history(session: Session, *, max_pages: int = 20, page_size: int = 500) -> dict:
    adapter = get_alpaca_adapter()
    orders = adapter.list_orders_paginated(max_pages=max_pages, page_size=page_size)

    existing_provider_order_ids = set(
        session.exec(select(ProviderExecution.external_order_id)).all()
    )
    lifecycle_order_ids: set[str] = set()
    for entry_id, exit_id in session.exec(
        select(PositionLifecycle.entry_order_id, PositionLifecycle.exit_order_id)
    ).all():
        if entry_id:
            lifecycle_order_ids.add(entry_id)
        if exit_id:
            lifecycle_order_ids.add(exit_id)

    now = datetime.now(timezone.utc)
    marker = f"{_MARKER_PREFIX}_{now.date().isoformat()}"

    matched_packet_based = 0
    matched_recycle = 0
    restored: list[dict] = []
    unmatched: list[dict] = []

    for order in orders:
        if order.order_id in existing_provider_order_ids:
            matched_packet_based += 1
            continue
        if order.order_id in lifecycle_order_ids:
            matched_recycle += 1
            continue

        client_order_id = (order.raw or {}).get("client_order_id")
        packet_id = _extract_packet_id(client_order_id)
        packet = session.get(ActionPacket, packet_id) if packet_id is not None else None

        if packet is not None:
            already_restored = session.exec(
                select(ProviderExecution).where(ProviderExecution.external_order_id == order.order_id)
            ).first()
            if already_restored is None:
                session.add(
                    ProviderExecution(
                        packet_id=packet.id,
                        source_id=packet.source_id,
                        provider="alpaca",
                        provider_mode="live",
                        external_order_id=order.order_id,
                        symbol=order.symbol,
                        order_side=order.side,
                        order_type="market",
                        qty=order.qty,
                        notional=order.notional,
                        submitted_at=_parse_submitted_at(order.submitted_at),
                        execution_status=order.status,
                        provider_message=order.provider_message,
                        reconciled_from_broker_history=True,
                        reconciliation_marker=marker,
                        reconciled_at=now,
                    )
                )
                restored.append(
                    {
                        "packet_id": packet.id,
                        "order_id": order.order_id,
                        "status": order.status,
                        "symbol": order.symbol,
                    }
                )
            continue

        unmatched.append({"order_id": order.order_id, "status": order.status, "symbol": order.symbol})

    session.commit()

    all_packets = session.exec(select(ActionPacket)).all()
    uncertain_count = sum(
        1
        for packet in all_packets
        if packet.execution_notes
        and any(packet.execution_notes.startswith(prefix) for prefix in _UNCERTAIN_OUTCOME_PREFIXES)
    )

    submitted_dates = [o.submitted_at for o in orders if o.submitted_at]
    coverage_possibly_incomplete = len(orders) >= max_pages * page_size

    result = {
        "reconciliation_marker": marker,
        "broker_orders_examined": len(orders),
        "matched_packet_based": matched_packet_based,
        "matched_recycle": matched_recycle,
        "restored_count": len(restored),
        "restored": restored,
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "internal_packets_with_uncertain_outcomes": uncertain_count,
        "coverage": {
            "max_pages": max_pages,
            "page_size": page_size,
            "earliest_submitted_at": min(submitted_dates) if submitted_dates else None,
            "latest_submitted_at": max(submitted_dates) if submitted_dates else None,
            "possibly_incomplete": coverage_possibly_incomplete,
        },
    }
    logger.info(
        "BROKER_HISTORY_RECONCILIATION marker=%s examined=%d matched_packet_based=%d matched_recycle=%d "
        "restored=%d unmatched=%d uncertain_packets=%d coverage_possibly_incomplete=%s",
        marker,
        result["broker_orders_examined"],
        matched_packet_based,
        matched_recycle,
        len(restored),
        len(unmatched),
        uncertain_count,
        coverage_possibly_incomplete,
    )
    return result
