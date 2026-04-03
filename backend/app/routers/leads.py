"""
Lead intelligence endpoints — Apollo + Common Room.

GET  /leads/status              — connectivity status for all lead tools
POST /leads/apollo/search       — search people on Apollo
POST /leads/apollo/enrich       — enrich a contact by email
POST /leads/apollo/company      — enrich a company by domain
GET  /leads/commonroom/signals  — recent community buying signals
GET  /leads/commonroom/members  — community member list
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import APOLLO_API_KEY, COMMONROOM_API_KEY

router = APIRouter(prefix="/leads", tags=["leads"])


class ApolloSearchRequest(BaseModel):
    keywords: Optional[str] = None
    titles: Optional[list[str]] = None
    domains: Optional[list[str]] = None
    per_page: int = 10


class ApolloEnrichRequest(BaseModel):
    email: str


class ApolloCompanyRequest(BaseModel):
    domain: str


def _get_apollo():
    from app.integration.leads.apollo import get_apollo_adapter
    try:
        return get_apollo_adapter()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _get_commonroom():
    from app.integration.leads.commonroom import get_commonroom_adapter
    try:
        return get_commonroom_adapter()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/status")
def lead_tool_status():
    """Connectivity status for all configured lead intelligence tools."""
    apollo_status: dict = {"configured": bool(APOLLO_API_KEY)}
    if APOLLO_API_KEY:
        from app.integration.leads.apollo import get_apollo_adapter
        try:
            adapter = get_apollo_adapter()
            apollo_status.update(adapter.connectivity_check())
        except Exception as exc:
            apollo_status["connected"] = False
            apollo_status["error"] = str(exc)
    else:
        apollo_status["connected"] = False
        apollo_status["note"] = "Set APOLLO_API_KEY in .env to enable"

    commonroom_status: dict = {"configured": bool(COMMONROOM_API_KEY)}
    if COMMONROOM_API_KEY:
        from app.integration.leads.commonroom import get_commonroom_adapter
        try:
            adapter = get_commonroom_adapter()
            commonroom_status.update(adapter.connectivity_check())
        except Exception as exc:
            commonroom_status["connected"] = False
            commonroom_status["error"] = str(exc)
    else:
        commonroom_status["connected"] = False
        commonroom_status["note"] = "Set COMMONROOM_API_KEY in .env to enable"

    return {
        "apollo": apollo_status,
        "commonroom": commonroom_status,
        "any_configured": bool(APOLLO_API_KEY or COMMONROOM_API_KEY),
    }


@router.post("/apollo/search")
def apollo_search(payload: ApolloSearchRequest):
    adapter = _get_apollo()
    try:
        return adapter.search_people(
            q_keywords=payload.keywords,
            titles=payload.titles,
            organization_domains=payload.domains,
            per_page=payload.per_page,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Apollo search failed: {exc}")


@router.post("/apollo/enrich")
def apollo_enrich(payload: ApolloEnrichRequest):
    adapter = _get_apollo()
    try:
        return adapter.enrich_person(email=payload.email)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Apollo enrichment failed: {exc}")


@router.post("/apollo/company")
def apollo_company(payload: ApolloCompanyRequest):
    adapter = _get_apollo()
    try:
        return adapter.enrich_organization(domain=payload.domain)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Apollo company enrichment failed: {exc}")


@router.get("/commonroom/signals")
def commonroom_signals(limit: int = 25):
    adapter = _get_commonroom()
    try:
        return adapter.get_signals(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Common Room signals failed: {exc}")


@router.get("/commonroom/members")
def commonroom_members(limit: int = 25):
    adapter = _get_commonroom()
    try:
        return adapter.get_members(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Common Room members failed: {exc}")
