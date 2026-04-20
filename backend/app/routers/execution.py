from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.database.config import get_session
from app.integration.brokerage.base import TradeOrder
from app.services import diagnostics as diag_svc
from app.services import execution as exec_svc

router = APIRouter(prefix="/execution", tags=["execution"])


class TradeRequest(BaseModel):
    symbol: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    side: str                   # "buy" | "sell"
    order_type: str = "market"  # "market" | "limit"
    time_in_force: str = "gtc"
    limit_price: Optional[float] = None
    client_order_id: Optional[str] = None
    source_id: Optional[str] = None  # link to income source for audit trail
    packet_id: Optional[int] = None


class ExecutionTransitionRequest(BaseModel):
    notes: Optional[str] = None
    actual_return: Optional[float] = None
    success_reason: Optional[str] = None
    failure_reason: Optional[str] = None


@router.post("/trade")
def place_trade(req: TradeRequest, session: Session = Depends(get_session)):
    valid_sides = {"buy", "sell"}
    if req.side.lower() not in valid_sides:
        raise HTTPException(status_code=422, detail=f"side must be 'buy' or 'sell'")
    if req.qty is None and req.notional is None:
        raise HTTPException(status_code=422, detail="qty or notional must be provided")
    if req.qty is not None and req.qty <= 0:
        raise HTTPException(status_code=422, detail="qty must be > 0")
    if req.notional is not None and req.notional <= 0:
        raise HTTPException(status_code=422, detail="notional must be > 0")

    order = TradeOrder(
        symbol=req.symbol.upper(),
        side=req.side.lower(),
        order_type=req.order_type,
        qty=req.qty,
        notional=req.notional,
        time_in_force=req.time_in_force,
        limit_price=req.limit_price,
        client_order_id=req.client_order_id,
    )

    try:
        result = exec_svc.place_trade(order, session, packet_id=req.packet_id, source_id=req.source_id)
        return result.__dict__
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Trade execution failed: {exc}")


@router.get("/order/{order_id}")
def get_order(order_id: str):
    try:
        result = exec_svc.get_order(order_id)
        return result.__dict__
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Brokerage error: {exc}")


@router.delete("/order/{order_id}")
def cancel_order(order_id: str, source_id: Optional[str] = None, session: Session = Depends(get_session)):
    try:
        success = exec_svc.cancel_order(order_id, session, source_id=source_id)
        return {"cancelled": success, "order_id": order_id}
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Brokerage error: {exc}")


@router.post("/start/{packet_id}")
def start_execution(
    packet_id: int,
    payload: ExecutionTransitionRequest | None = None,
    session: Session = Depends(get_session),
):
    try:
        packet = exec_svc.start_packet_execution(
            packet_id,
            session,
            notes=payload.notes if payload else None,
        )
        return exec_svc.get_packet_execution_payload(packet.id, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/complete/{packet_id}")
def complete_execution(
    packet_id: int,
    payload: ExecutionTransitionRequest,
    session: Session = Depends(get_session),
):
    try:
        packet = exec_svc.complete_packet_execution(
            packet_id,
            session,
            actual_return=payload.actual_return,
            success_reason=payload.success_reason,
            notes=payload.notes,
        )
        return exec_svc.get_packet_execution_payload(packet.id, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/fail/{packet_id}")
def fail_execution(
    packet_id: int,
    payload: ExecutionTransitionRequest,
    session: Session = Depends(get_session),
):
    try:
        packet = exec_svc.fail_packet_execution(
            packet_id,
            session,
            actual_return=payload.actual_return,
            failure_reason=payload.failure_reason,
            notes=payload.notes,
        )
        return exec_svc.get_packet_execution_payload(packet.id, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/status")
def execution_status(session: Session = Depends(get_session)):
    try:
        payload = exec_svc.get_execution_status(session)
        diag_svc.record_success(
            "execution.status",
            metadata={
                "active": payload.get("counts", {}).get("active", 0),
                "completed": payload.get("counts", {}).get("completed", 0),
                "failed": payload.get("counts", {}).get("failed", 0),
            },
        )
        return payload
    except Exception as exc:
        diag_svc.record_error("execution.status", exc, affected_component="execution.status")
        raise


@router.get("/provider-status")
def provider_status(session: Session = Depends(get_session)):
    status = exec_svc.get_execution_provider_status(session)
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=status)
    return status


@router.get("/provider-diagnostics")
def provider_diagnostics():
    return exec_svc.get_execution_provider_diagnostics()


@router.get("/account")
def provider_account():
    try:
        return exec_svc.get_provider_account().__dict__
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Provider account lookup failed: {exc}")


@router.get("/positions")
def provider_positions():
    try:
        return [position.__dict__ for position in exec_svc.get_provider_positions()]
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Provider positions lookup failed: {exc}")


@router.get("/orders")
def provider_orders(limit: int = 20, session: Session = Depends(get_session)):
    orders = exec_svc.get_provider_orders(session, limit=limit)
    return [
        {
            "id": order.id,
            "packet_id": order.packet_id,
            "source_id": order.source_id,
            "allocation_id": order.allocation_id,
            "provider": order.provider,
            "provider_mode": order.provider_mode,
            "external_order_id": order.external_order_id,
            "symbol": order.symbol,
            "order_side": order.order_side,
            "order_type": order.order_type,
            "qty": order.qty,
            "notional": order.notional,
            "limit_price": order.limit_price,
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "execution_status": order.execution_status,
            "provider_message": order.provider_message,
            "created_at": order.created_at.isoformat(),
        }
        for order in orders
    ]
