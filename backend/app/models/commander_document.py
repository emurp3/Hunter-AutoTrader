"""
Commander-supplied reference documents (capability profiles, resumes,
etc.) that Hunter can read from when evaluating opportunities or
answering Commander in chat. Storage only — see the standing addendum
Commander-checkpoint rule: having this on file lets Hunter reference and
propose, but any consequential external submission still requires a
separate Commander-approved step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class CommanderDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: str = Field(index=True, unique=True)
    filename: str
    document_type: str = Field(default="other", index=True)
    content_type: str = "application/octet-stream"
    stored_path: Optional[str] = None
    extracted_text: str = ""
    source: str = "commander_upload"
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
