from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database.config import get_session
from app.services.source_acquisition import get_latest_results, get_source_status, run_source_acquisition

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/status")
def sources_status() -> dict:
    return get_source_status()


@router.post("/run")
def run_sources(session: Session = Depends(get_session)) -> dict:
    return run_source_acquisition(session)


@router.get("/results")
def source_results() -> list[dict]:
    return get_latest_results()


@router.get("/results/{origin_module}")
def source_results_by_origin(origin_module: str) -> list[dict]:
    results = get_latest_results(origin_module)
    if not results:
        raise HTTPException(status_code=404, detail=f"No source results found for '{origin_module}'.")
    return results
