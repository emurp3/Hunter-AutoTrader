"""
Facebook Marketplace compliant execution lane endpoints.

GET  /marketplace/status                    — lane config and provider health
GET  /marketplace/opportunities             — sources in the marketplace lane
POST /marketplace/assign/{source_id}        — assign source to FB Marketplace lane
POST /marketplace/execute/{source_id}       — execute a marketplace action
GET  /marketplace/ledger                    — full marketplace transaction log
GET  /marketplace/ledger/{source_id}        — per-source ledger
POST /marketplace/messages                  — draft a customer message
POST /marketplace/messages/{id}/send        — mark drafted message as sent
GET  /marketplace/messages                  — all messages
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.config import (
    API2CART_API_KEY,
    AUTODS_API_KEY,
    MARKETPLACE_FB_LANE_ENABLED,
    MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED,
    MARKETPLACE_FB_PROVIDER,
    MARKETPLACE_FB_RATE_LIMIT_PER_HOUR,
)
from app.database.config import get_session
from app.models.income_source import IncomeSource
from app.models.marketplace import (
    BankrollReconciliation,
    BankrollReconciliationCreate,
    MarketplaceActionType,
    MarketplaceLaneAssign,
    MarketplaceMessage,
)
from app.services import marketplace as mkt_svc

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def marketplace_status():
    """
    Returns the current configuration and health of the Facebook Marketplace lane.
    Shows which provider is active, whether keys are configured, and lane enabled state.
    """
    provider = MARKETPLACE_FB_PROVIDER
    provider_configured = False
    provider_note = None

    if provider == "api2cart_facebook_marketplace":
        provider_configured = bool(API2CART_API_KEY)
        provider_note = (
            "API2CART_API_KEY configured" if provider_configured
            else "API2CART_API_KEY not set — set it in Render env vars"
        )
    elif provider == "autods_facebook_marketplace":
        provider_configured = bool(AUTODS_API_KEY)
        provider_note = (
            "AUTODS_API_KEY configured" if provider_configured
            else "AUTODS_API_KEY not set — set it in Render env vars"
        )
    else:
        provider_configured = True  # manual mode needs no API key
        provider_note = "Manual mode: no API key required. List on Marketplace yourself."

    return {
        "lane": "facebook_marketplace_compliant",
        "lane_enabled": MARKETPLACE_FB_LANE_ENABLED,
        "provider": provider,
        "provider_configured": provider_configured,
        "provider_note": provider_note,
        "message_support_enabled": MARKETPLACE_FB_MESSAGE_SUPPORT_ENABLED,
        "message_rate_limit_per_hour": MARKETPLACE_FB_RATE_LIMIT_PER_HOUR,
        "supported_routing_labels": [
            "listing_candidate",
            "repricing_candidate",
            "fulfillment_followup",
            "customer_message_needed",
            "policy_blocked",
        ],
        "note": (
            "Lane is disabled. Set MARKETPLACE_FB_LANE_ENABLED=true to activate."
            if not MARKETPLACE_FB_LANE_ENABLED
            else "Lane active."
        ),
    }


# ── Opportunities in the lane ─────────────────────────────────────────────────

@router.get("/opportunities")
def marketplace_opportunities(session: Session = Depends(get_session)):
    """All income sources assigned to the Facebook Marketplace lane, score DESC."""
    sources = mkt_svc.get_marketplace_opportunities(session)
    return {
        "count": len(sources),
        "opportunities": [
            {
                "source_id": s.source_id,
                "description": s.description,
                "score": s.score,
                "priority_band": s.priority_band,
                "status": s.status,
                "estimated_profit": s.estimated_profit,
                "marketplace_lane": s.marketplace_lane,
                "marketplace_routing_label": s.marketplace_routing_label,
                "marketplace_provider": s.marketplace_provider,
                "marketplace_execution_state": s.marketplace_execution_state,
                "marketplace_blocked_reason": s.marketplace_blocked_reason,
            }
            for s in sources
        ],
    }


# ── Assign source to lane ─────────────────────────────────────────────────────

@router.post("/assign/{source_id}")
def assign_to_marketplace(
    source_id: str,
    payload: MarketplaceLaneAssign,
    session: Session = Depends(get_session),
):
    """
    Assign an income source to the Facebook Marketplace compliant lane.
    Sets routing label, provider, and initial execution state.
    Writes a ledger entry.
    """
    source = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found.")

    entry = mkt_svc.assign_marketplace_lane(
        source=source,
        routing_label=payload.routing_label,
        provider=payload.provider,
        committed_amount=payload.committed_amount,
        expected_profit=payload.expected_profit,
        notes=payload.notes,
        session=session,
    )
    return {
        "source_id": source_id,
        "marketplace_lane": source.marketplace_lane,
        "marketplace_routing_label": source.marketplace_routing_label,
        "marketplace_provider": source.marketplace_provider,
        "marketplace_execution_state": source.marketplace_execution_state,
        "ledger_entry_id": entry.id,
    }


# ── Execute a marketplace action ──────────────────────────────────────────────

@router.post("/execute/{source_id}")
def execute_action(
    source_id: str,
    action: MarketplaceActionType,
    price: Optional[float] = None,
    external_id: Optional[str] = None,
    notes: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """
    Execute a marketplace action for a source already in the FB lane.
    Available actions: prepare_listing, list, reprice, fulfill, complete, fail.
    """
    source = session.exec(
        select(IncomeSource).where(IncomeSource.source_id == source_id)
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found.")

    result = mkt_svc.execute_marketplace_action(
        source=source,
        action=action,
        session=session,
        price=price,
        external_id=external_id,
        notes=notes,
    )
    return result


# ── Ledger ────────────────────────────────────────────────────────────────────

@router.get("/ledger")
def marketplace_ledger(limit: int = 100, session: Session = Depends(get_session)):
    """Full marketplace transaction log, newest first."""
    entries = mkt_svc.get_marketplace_ledger(session, limit=limit)
    return {"count": len(entries), "entries": [e.model_dump() for e in entries]}


@router.get("/ledger/{source_id}")
def marketplace_ledger_for_source(source_id: str, session: Session = Depends(get_session)):
    """Marketplace ledger filtered to a single source."""
    entries = mkt_svc.get_marketplace_ledger(session, source_id=source_id)
    return {"source_id": source_id, "count": len(entries), "entries": [e.model_dump() for e in entries]}


# ── Customer messages ─────────────────────────────────────────────────────────

@router.post("/messages")
def draft_message(
    source_id: str,
    message_type: str,
    content: str,
    session: Session = Depends(get_session),
):
    """
    Draft an approved customer message for a Marketplace opportunity.
    message_type: inquiry_response | price_negotiation | follow_up
    Message is stored as 'drafted' — not sent until /messages/{id}/send is called.
    """
    from app.config import MARKETPLACE_FB_PROVIDER
    msg = mkt_svc.draft_customer_message(
        source_id=source_id,
        message_type=message_type,
        content=content,
        provider=MARKETPLACE_FB_PROVIDER,
        session=session,
    )
    return msg.model_dump()


@router.post("/messages/{message_id}/send")
def send_message(message_id: int, session: Session = Depends(get_session)):
    """
    Mark a drafted message as sent. Rate limit enforced per MARKETPLACE_FB_RATE_LIMIT_PER_HOUR.
    Actual delivery is done through the Marketplace interface directly.
    """
    result = mkt_svc.send_drafted_message(message_id, session)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@router.get("/messages")
def list_messages(source_id: Optional[str] = None, limit: int = 50, session: Session = Depends(get_session)):
    """All drafted/sent/rate-limited messages, optionally filtered by source."""
    stmt = select(MarketplaceMessage).order_by(MarketplaceMessage.created_at.desc()).limit(limit)
    if source_id:
        stmt = select(MarketplaceMessage).where(
            MarketplaceMessage.source_id == source_id
        ).order_by(MarketplaceMessage.created_at.desc()).limit(limit)
    msgs = list(session.exec(stmt).all())
    return {"count": len(msgs), "messages": [m.model_dump() for m in msgs]}
