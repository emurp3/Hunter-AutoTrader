"""
PolicyEvent — raw political/regulatory/legal actions tracked by
the Policy-to-Profit Engine.

One row per unique event. Deduplication by content_hash (SHA-256 of
source_name + title + url, first 48 chars). After LLM processing the
engine creates linked IncomeSource records and increments opportunities_generated.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Column, Field, SQLModel, Text


class PolicyEvent(SQLModel, table=True):
    __tablename__ = "policy_event"

    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Deduplication ───────────────────────────────────────────────────────
    content_hash: str = Field(max_length=64, index=True, unique=True)

    # ── Source metadata ─────────────────────────────────────────────────────
    source_name: str = Field(max_length=80)    # e.g. "whitehouse_actions"
    source_url: str = Field(max_length=1024)
    title: str = Field(max_length=512)
    summary: str = Field(sa_column=Column(Text))
    raw_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    published_at: Optional[datetime] = None
    detected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ── LLM analysis (JSON strings) ─────────────────────────────────────────
    affected_industries: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )  # JSON list, e.g. '["Healthcare", "Technology"]'
    opportunity_categories: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )  # JSON list of opp categories
    llm_analysis: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )  # Full structured LLM response (JSON)

    # ── Processing state ────────────────────────────────────────────────────
    opportunities_generated: int = Field(default=0)
    processed: bool = Field(default=False)
    processing_error: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )

    @staticmethod
    def make_hash(source_name: str, title: str, url: str = "") -> str:
        blob = f"{source_name}||{title}||{url}".lower().strip().encode()
        return hashlib.sha256(blob).hexdigest()[:48]
