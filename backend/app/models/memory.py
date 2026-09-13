"""Durable, objective-scoped conversational context for Hunter."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True)
    user_id: str = Field(default="commander", index=True)
    objective_id: Optional[str] = Field(default=None, index=True)
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow, index=True)


class ConversationMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True)
    conversation_id: str = Field(index=True)
    user_id: str = Field(default="commander", index=True)
    role: str = Field(index=True)
    content: str
    objective_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    checkpoint_key: Optional[str] = Field(default=None, index=True)
    source_type: str = Field(default="explicit_user", index=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class ObjectiveFact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    fact_id: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True)
    user_id: str = Field(default="commander", index=True)
    objective_id: Optional[str] = Field(default=None, index=True)
    scope: str = Field(default="objective", index=True)
    fact_key: str = Field(index=True)
    fact_value: str
    source_message_id: Optional[str] = Field(default=None, index=True)
    source_type: str = Field(default="explicit_user", index=True)
    authoritative: bool = Field(default=False)
    active: bool = Field(default=True, index=True)
    supersedes_fact_id: Optional[str] = Field(default=None, index=True)
    superseded_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
