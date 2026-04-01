from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database.config import get_session
from app.models.action_packet import PacketStatus
from app.services import action_packets as packet_svc

router = APIRouter(prefix="/packets", tags=["packets"])


@router.get("/")
def list_packets(status: str | None = None, session: Session = Depends(get_session)):
    return packet_svc.list_packets(session, status=status)


@router.get("/{source_id}")
def get_packet(source_id: str, session: Session = Depends(get_session)):
    packet = packet_svc.get_packet(source_id, session)
    if not packet:
        raise HTTPException(status_code=404, detail="No packet found for source")
    return packet


@router.post("/{source_id}/generate")
def generate_packet(source_id: str, session: Session = Depends(get_session)):
    try:
        return packet_svc.generate_packet(source_id, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{packet_id}/promote")
def promote_packet(packet_id: int, new_status: str, session: Session = Depends(get_session)):
    valid = {s.value for s in PacketStatus}
    if new_status not in valid:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {valid}")
    packet = packet_svc.promote_packet(packet_id, new_status, session)
    if not packet:
        raise HTTPException(status_code=404, detail="Packet not found")
    return packet
