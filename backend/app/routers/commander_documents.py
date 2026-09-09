from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session

from app.auth.jwt import require_admin
from app.database.config import get_session
from app.services import commander_documents as docs_svc

router = APIRouter(prefix="/assistant/documents", tags=["commander-documents"])


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form("other"),
    session: Session = Depends(get_session),
    _user=Depends(require_admin),
):
    raw = await file.read()
    try:
        doc = docs_svc.save_document(
            session,
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
            raw_bytes=raw,
            document_type=document_type,
            source="commander_upload",
        )
    except docs_svc.UnsupportedDocumentType as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "document_id": doc.document_id,
        "filename": doc.filename,
        "document_type": doc.document_type,
        "extracted_chars": len(doc.extracted_text),
        "uploaded_at": doc.uploaded_at,
    }


@router.get("")
def list_documents(session: Session = Depends(get_session), _user=Depends(require_admin)):
    return [
        {
            "document_id": d.document_id,
            "filename": d.filename,
            "document_type": d.document_type,
            "source": d.source,
            "extracted_chars": len(d.extracted_text),
            "uploaded_at": d.uploaded_at,
        }
        for d in docs_svc.list_documents(session)
    ]
