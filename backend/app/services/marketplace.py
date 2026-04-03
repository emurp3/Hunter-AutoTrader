"""
Facebook Marketplace compliant execution lane service.

Supports two provider integrations:
  - api2cart_facebook_marketplace  (API2Cart channel API)
  - autods_facebook_marketplace    (AutoDS dropshipping API)
  - manual                         (Hunter prepares packet, no provider call)

All actions write a MarketplaceLedgerEntry so the transaction log is complete.
Provider secrets are read from environment — never hardcoded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from app.models.income_source import IncomeSource
from app.models.marketplace import (
    MarketplaceActionType,
    MarketplaceExecutionState,
    MarketplaceLane,
    MarketplaceLedgerEntry,
    MarketplaceMessage,
    MarketplaceProvider,
    MarketplaceRoutingLabel,
)

logger = logging.getLogger(__name__)


# ── Provider adapter base ─────────────────────────────────────────────────────

class _ProviderAdapter:
    """Protocol for marketplace provider adapters."""

    def prepare_listing(self, source: IncomeSource) -> dict[str, Any]:
        raise NotImplementedError

    def create_listing(self, source: IncomeSource, price: float) -> dict[str, Any]:
        raise NotImplementedError

    def reprice_listing(self, external_id: str, new_price: float) -> dict[str, Any]:
        raise NotImplementedError

    def get_listing_status(self, external_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def track_fulfillment(self, external_order_id: str) -> dict[str, Any]:
        raise NotImplementedError


# ── API2Cart adapter ──────────────────────────────────────────────────────────

class Api2CartFBAdapter(_ProviderAdapter):
    """
    Interfaces with API2Cart's Facebook Marketplace channel.
    API docs: https://api2cart.com/docs/
    """

    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._api_key, "Content-Type": "application/json"}

    def prepare_listing(self, source: IncomeSource) -> dict[str, Any]:
        return {
            "provider": "api2cart_facebook_marketplace",
            "status": "listing_prepared",
            "title": source.description[:80],
            "price": source.estimated_profit,
            "source_id": source.source_id,
            "note": "Listing packet prepared. Call create_listing() to publish.",
        }

    def create_listing(self, source: IncomeSource, price: float) -> dict[str, Any]:
        if not self._api_key:
            return {
                "status": "blocked",
                "reason": "API2CART_API_KEY not configured. Set it in Render env vars.",
            }
        try:
            resp = httpx.post(
                f"{self._base_url}/product.add.json",
                headers=self._headers(),
                json={
                    "name": source.description[:80],
                    "price": price,
                    "channel": "facebook_marketplace",
                    "sku": source.source_id,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "listed",
                "external_listing_id": str(data.get("result", {}).get("product_id", "")),
                "provider_response": data,
            }
        except Exception as exc:
            logger.error("api2cart create_listing error: %s", exc)
            return {"status": "failed", "reason": str(exc)}

    def reprice_listing(self, external_id: str, new_price: float) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "blocked", "reason": "API2CART_API_KEY not configured."}
        try:
            resp = httpx.post(
                f"{self._base_url}/product.update.json",
                headers=self._headers(),
                json={"id": external_id, "price": new_price},
                timeout=15,
            )
            resp.raise_for_status()
            return {"status": "repriced", "external_listing_id": external_id, "new_price": new_price}
        except Exception as exc:
            logger.error("api2cart reprice error: %s", exc)
            return {"status": "failed", "reason": str(exc)}

    def get_listing_status(self, external_id: str) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "unknown", "reason": "API2CART_API_KEY not configured."}
        try:
            resp = httpx.get(
                f"{self._base_url}/product.info.json",
                headers=self._headers(),
                params={"id": external_id},
                timeout=15,
            )
            resp.raise_for_status()
            return {"status": "ok", "data": resp.json()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    def track_fulfillment(self, external_order_id: str) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "unknown", "reason": "API2CART_API_KEY not configured."}
        try:
            resp = httpx.get(
                f"{self._base_url}/order.info.json",
                headers=self._headers(),
                params={"id": external_order_id},
                timeout=15,
            )
            resp.raise_for_status()
            return {"status": "ok", "data": resp.json()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


# ── AutoDS adapter ────────────────────────────────────────────────────────────

class AutoDSFBAdapter(_ProviderAdapter):
    """
    Interfaces with AutoDS Facebook Marketplace dropshipping API.
    API docs: https://api.autods.com/docs
    """

    def __init__(self, api_key: str, partner_token: str, base_url: str):
        self._api_key = api_key
        self._partner_token = partner_token
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Partner-Token": self._partner_token,
            "Content-Type": "application/json",
        }

    def prepare_listing(self, source: IncomeSource) -> dict[str, Any]:
        return {
            "provider": "autods_facebook_marketplace",
            "status": "listing_prepared",
            "title": source.description[:80],
            "estimated_profit": source.estimated_profit,
            "source_id": source.source_id,
            "note": "AutoDS listing packet prepared. Call create_listing() to publish.",
        }

    def create_listing(self, source: IncomeSource, price: float) -> dict[str, Any]:
        if not self._api_key:
            return {
                "status": "blocked",
                "reason": "AUTODS_API_KEY not configured. Set it in Render env vars.",
            }
        try:
            resp = httpx.post(
                f"{self._base_url}/products",
                headers=self._headers(),
                json={
                    "title": source.description[:80],
                    "price": price,
                    "marketplace": "facebook",
                    "external_id": source.source_id,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "listed",
                "external_listing_id": str(data.get("id", "")),
                "provider_response": data,
            }
        except Exception as exc:
            logger.error("autods create_listing error: %s", exc)
            return {"status": "failed", "reason": str(exc)}

    def reprice_listing(self, external_id: str, new_price: float) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "blocked", "reason": "AUTODS_API_KEY not configured."}
        try:
            resp = httpx.patch(
                f"{self._base_url}/products/{external_id}",
                headers=self._headers(),
                json={"price": new_price},
                timeout=15,
            )
            resp.raise_for_status()
            return {"status": "repriced", "external_listing_id": external_id, "new_price": new_price}
        except Exception as exc:
            return {"status": "failed", "reason": str(exc)}

    def get_listing_status(self, external_id: str) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "unknown", "reason": "AUTODS_API_KEY not configured."}
        try:
            resp = httpx.get(
                f"{self._base_url}/products/{external_id}",
                headers=self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            return {"status": "ok", "data": resp.json()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    def track_fulfillment(self, external_order_id: str) -> dict[str, Any]:
        if not self._api_key:
            return {"status": "unknown", "reason": "AUTODS_API_KEY not configured."}
        try:
            resp = httpx.get(
                f"{self._base_url}/orders/{external_order_id}",
                headers=self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            return {"status": "ok", "data": resp.json()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


# ── Manual adapter (no API calls) ────────────────────────────────────────────

class ManualFBAdapter(_ProviderAdapter):
    """
    No external API. Hunter prepares listing packets and tracks state locally.
    Commander executes on Marketplace manually and records outcomes.
    """

    def prepare_listing(self, source: IncomeSource) -> dict[str, Any]:
        return {
            "provider": "manual",
            "status": "listing_prepared",
            "title": source.description[:80],
            "estimated_profit": source.estimated_profit,
            "source_id": source.source_id,
            "note": "Manual mode: list this item on Facebook Marketplace yourself and record the outcome.",
        }

    def create_listing(self, source: IncomeSource, price: float) -> dict[str, Any]:
        return {
            "status": "listing_prepared",
            "note": "Manual mode: no API call. List this item manually.",
        }

    def reprice_listing(self, external_id: str, new_price: float) -> dict[str, Any]:
        return {
            "status": "repriced",
            "note": f"Manual mode: update listing {external_id} to ${new_price:.2f} on Marketplace.",
        }

    def get_listing_status(self, external_id: str) -> dict[str, Any]:
        return {"status": "unknown", "note": "Manual mode: check Marketplace directly."}

    def track_fulfillment(self, external_order_id: str) -> dict[str, Any]:
        return {"status": "unknown", "note": "Manual mode: check Marketplace inbox directly."}


# ── Adapter factory ───────────────────────────────────────────────────────────

def get_marketplace_adapter() -> _ProviderAdapter:
    from app.config import (
        API2CART_API_KEY,
        API2CART_BASE_URL,
        AUTODS_API_KEY,
        AUTODS_BASE_URL,
        AUTODS_PARTNER_TOKEN,
        MARKETPLACE_FB_PROVIDER,
    )
    if MARKETPLACE_FB_PROVIDER == MarketplaceProvider.api2cart_facebook_marketplace:
        return Api2CartFBAdapter(API2CART_API_KEY, API2CART_BASE_URL)
    if MARKETPLACE_FB_PROVIDER == MarketplaceProvider.autods_facebook_marketplace:
        return AutoDSFBAdapter(AUTODS_API_KEY, AUTODS_PARTNER_TOKEN, AUTODS_BASE_URL)
    return ManualFBAdapter()


# ── Lane assignment ───────────────────────────────────────────────────────────

def assign_marketplace_lane(
    source: IncomeSource,
    routing_label: MarketplaceRoutingLabel,
    provider: MarketplaceProvider,
    committed_amount: float,
    expected_profit: float,
    notes: str | None,
    session: Session,
) -> MarketplaceLedgerEntry:
    """Assign an income source to the Facebook Marketplace lane and write a ledger entry."""
    source.marketplace_lane = MarketplaceLane.facebook_marketplace_compliant
    source.marketplace_routing_label = routing_label.value
    source.marketplace_provider = provider.value
    source.marketplace_execution_state = MarketplaceExecutionState.pending.value
    session.add(source)

    entry = MarketplaceLedgerEntry(
        provider=provider.value,
        source_id=source.source_id,
        action_type=MarketplaceActionType.assign_lane.value,
        committed_amount=committed_amount,
        expected_profit=expected_profit,
        status=MarketplaceExecutionState.pending.value,
        notes=notes or f"Assigned to {MarketplaceLane.facebook_marketplace_compliant} / {routing_label.value}",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


# ── Execute marketplace action ────────────────────────────────────────────────

def execute_marketplace_action(
    source: IncomeSource,
    action: MarketplaceActionType,
    session: Session,
    price: float | None = None,
    external_id: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Execute a marketplace action for a source already assigned to the FB lane.
    Returns a result dict and writes a ledger entry.
    """
    from app.config import MARKETPLACE_FB_LANE_ENABLED

    if not MARKETPLACE_FB_LANE_ENABLED:
        return {
            "status": "blocked",
            "reason": "MARKETPLACE_FB_LANE_ENABLED=false. Set it to true in env vars.",
        }

    if not source.marketplace_lane:
        return {
            "status": "blocked",
            "reason": "Source not assigned to a marketplace lane. Call POST /marketplace/assign/{source_id} first.",
        }

    adapter = get_marketplace_adapter()
    result: dict[str, Any] = {}

    if action == MarketplaceActionType.prepare_listing:
        result = adapter.prepare_listing(source)
        new_state = MarketplaceExecutionState.listing_prepared

    elif action == MarketplaceActionType.list:
        result = adapter.create_listing(source, price or source.estimated_profit)
        new_state = MarketplaceExecutionState.listed if result.get("status") == "listed" else MarketplaceExecutionState.failed

    elif action == MarketplaceActionType.reprice:
        if not external_id:
            return {"status": "error", "reason": "external_id required for reprice action."}
        result = adapter.reprice_listing(external_id, price or source.estimated_profit)
        new_state = MarketplaceExecutionState.repriced if result.get("status") == "repriced" else MarketplaceExecutionState.failed

    elif action == MarketplaceActionType.fulfill:
        if not external_id:
            return {"status": "error", "reason": "external_id required for fulfillment tracking."}
        result = adapter.track_fulfillment(external_id)
        new_state = MarketplaceExecutionState.fulfillment_in_progress

    elif action == MarketplaceActionType.complete:
        new_state = MarketplaceExecutionState.completed
        result = {"status": "completed", "source_id": source.source_id}

    else:
        new_state = MarketplaceExecutionState.failed
        result = {"status": "error", "reason": f"Unknown action: {action}"}

    # Update source state
    source.marketplace_execution_state = new_state.value
    if new_state == MarketplaceExecutionState.failed:
        source.marketplace_blocked_reason = result.get("reason", "Unknown failure")
    session.add(source)

    # Write ledger entry
    entry = MarketplaceLedgerEntry(
        provider=source.marketplace_provider or "manual",
        source_id=source.source_id,
        action_type=action.value,
        committed_amount=price or 0.0,
        expected_profit=source.estimated_profit,
        status=new_state.value,
        external_listing_id=result.get("external_listing_id") or external_id,
        notes=notes,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    result["ledger_entry_id"] = entry.id
    result["execution_state"] = new_state.value
    return result


# ── Message support ───────────────────────────────────────────────────────────

def draft_customer_message(
    source_id: str,
    message_type: str,
    content: str,
    provider: str,
    session: Session,
) -> MarketplaceMessage:
    """Draft an approved message for a Marketplace buyer. Does not send — Commander reviews first."""
    msg = MarketplaceMessage(
        source_id=source_id,
        provider=provider,
        message_type=message_type,
        message_content=content,
        status="drafted",
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def send_drafted_message(
    message_id: int,
    session: Session,
) -> dict[str, Any]:
    """
    Mark a drafted message as sent. Rate-limit enforced.
    Actual delivery is via Marketplace interface — this records the intent.
    """
    from app.config import MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED, MARKETPLACE_FB_RATE_LIMIT_PER_HOUR
    from datetime import timedelta
    from sqlmodel import func

    if not MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED:
        return {"status": "blocked", "reason": "MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED=false"}

    msg = session.get(MarketplaceMessage, message_id)
    if not msg:
        return {"status": "error", "reason": "Message not found"}
    if msg.status == "sent":
        return {"status": "already_sent"}

    # Rate check: count messages sent in the last hour
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_sent = session.exec(
        select(func.count(MarketplaceMessage.id)).where(
            MarketplaceMessage.status == "sent",
            MarketplaceMessage.sent_at >= one_hour_ago,
        )
    ).one()

    if recent_sent >= MARKETPLACE_FB_RATE_LIMIT_PER_HOUR:
        msg.status = "rate_limited"
        session.add(msg)
        session.commit()
        return {
            "status": "rate_limited",
            "reason": f"Rate limit reached: {MARKETPLACE_FB_RATE_LIMIT_PER_HOUR} messages/hour",
            "sent_in_last_hour": recent_sent,
        }

    msg.status = "sent"
    msg.sent_at = datetime.now(timezone.utc)
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return {"status": "sent", "message_id": msg.id, "sent_at": msg.sent_at.isoformat()}


# ── Ledger queries ────────────────────────────────────────────────────────────

def get_marketplace_ledger(session: Session, source_id: str | None = None, limit: int = 100) -> list[MarketplaceLedgerEntry]:
    stmt = select(MarketplaceLedgerEntry).order_by(MarketplaceLedgerEntry.created_at.desc()).limit(limit)
    if source_id:
        stmt = select(MarketplaceLedgerEntry).where(
            MarketplaceLedgerEntry.source_id == source_id
        ).order_by(MarketplaceLedgerEntry.created_at.desc()).limit(limit)
    return list(session.exec(stmt).all())


def get_marketplace_opportunities(session: Session) -> list[IncomeSource]:
    """Return all income sources assigned to the Facebook Marketplace lane."""
    return list(session.exec(
        select(IncomeSource).where(
            IncomeSource.marketplace_lane == MarketplaceLane.facebook_marketplace_compliant.value
        ).order_by(IncomeSource.score.desc())
    ).all())
