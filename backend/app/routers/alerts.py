from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database.config import get_session
from app.services import alerts as alert_svc

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/")
def list_alerts(active_only: bool = True, session: Session = Depends(get_session)):
    if active_only:
        return alert_svc.get_active_alerts(session)
    return alert_svc.get_all_alerts(session)


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, session: Session = Depends(get_session)):
    alert = alert_svc.acknowledge_alert(alert_id, session)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert
