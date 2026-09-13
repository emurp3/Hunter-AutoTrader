"""Persistence and bounded retrieval for Hunter's conversational memory."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import Session, select

from app.models.memory import Conversation, ConversationMessage, ObjectiveFact

USER_ID = "commander"
DEFAULT_RECENT_LIMIT = 20
MAX_RECENT_LIMIT = 100
_SECRET_KEY = re.compile(r"(?:password|passwd|api[_ -]?key|auth(?:entication)?[_ -]?token|session[_ -]?cookie|private[_ -]?key|secret)", re.I)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"(?i)\b(password|passwd|api[_ -]?key|auth(?:entication)?[_ -]?token|session[_ -]?cookie|secret)\s*[:=]\s*([^\s,;]+)"),
]


def recent_message_limit() -> int:
    try:
        configured = int(os.getenv("HUNTER_CHAT_RECENT_MESSAGE_LIMIT", str(DEFAULT_RECENT_LIMIT)))
    except ValueError:
        configured = DEFAULT_RECENT_LIMIT
    return max(1, min(configured, MAX_RECENT_LIMIT))


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(lambda m: f"{m.group(1)}: [REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def resolve_conversation(session: Session, *, conversation_id: str | None = None,
                         objective_id: str | None = None, user_id: str = USER_ID) -> Conversation:
    conversation = None
    if conversation_id:
        conversation = session.exec(select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
        )).first()
    if conversation is None:
        stmt = select(Conversation).where(Conversation.user_id == user_id, Conversation.active == True)  # noqa: E712
        if objective_id:
            stmt = stmt.where(Conversation.objective_id == objective_id)
        conversation = session.exec(stmt.order_by(Conversation.updated_at.desc())).first()
    if conversation is None:
        conversation = Conversation(user_id=user_id, objective_id=objective_id)
        session.add(conversation)
        session.flush()
    elif objective_id and conversation.objective_id != objective_id:
        conversation.objective_id = objective_id
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(conversation)
    session.flush()
    return conversation


def add_message(session: Session, conversation: Conversation, *, role: str, content: str,
                objective_id: str | None = None, task_id: str | None = None,
                checkpoint_key: str | None = None, source_type: str = "explicit_user") -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=conversation.conversation_id, user_id=conversation.user_id,
        role=role, content=redact_secrets(content), objective_id=objective_id or conversation.objective_id,
        task_id=task_id, checkpoint_key=checkpoint_key, source_type=source_type,
    )
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(message)
    session.add(conversation)
    session.flush()
    return message


def recent_messages(session: Session, conversation_id: str, *, limit: int | None = None) -> list[ConversationMessage]:
    bounded = max(1, min(limit or recent_message_limit(), MAX_RECENT_LIMIT))
    rows = session.exec(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id,
    ).order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc()).limit(bounded)).all()
    return list(reversed(rows))


def active_facts(session: Session, objective_id: str | None, *, user_id: str = USER_ID) -> list[ObjectiveFact]:
    scope_clause = ObjectiveFact.scope == "general"
    if objective_id:
        scope_clause = scope_clause | ((ObjectiveFact.scope == "objective") & (ObjectiveFact.objective_id == objective_id))
    rows = session.exec(select(ObjectiveFact).where(
        ObjectiveFact.user_id == user_id, ObjectiveFact.active == True, scope_clause,  # noqa: E712
    ).order_by(ObjectiveFact.created_at.asc())).all()
    return list(rows)


def promote_fact(session: Session, *, fact_key: str, fact_value: str, objective_id: str | None,
                 scope: str = "objective", source_message_id: str | None = None,
                 source_type: str = "explicit_user", user_id: str = USER_ID) -> ObjectiveFact:
    key = fact_key.strip().lower().replace(" ", "_")
    if not key or _SECRET_KEY.search(key):
        raise ValueError("Credential-like facts cannot be persisted.")
    if scope not in {"objective", "general"}:
        raise ValueError("Fact scope must be objective or general.")
    if source_type not in {"explicit_user", "checkpoint_answer"}:
        raise ValueError("Only explicit user facts may be promoted.")
    if scope == "objective" and not objective_id:
        raise ValueError("Objective-scoped facts require objective_id.")
    value = redact_secrets(fact_value.strip())
    if not value or "[REDACTED]" in value:
        raise ValueError("Credential-like fact values cannot be persisted.")
    scoped_objective = objective_id if scope == "objective" else None
    existing = session.exec(select(ObjectiveFact).where(
        ObjectiveFact.user_id == user_id, ObjectiveFact.scope == scope,
        ObjectiveFact.objective_id == scoped_objective, ObjectiveFact.fact_key == key,
        ObjectiveFact.active == True,  # noqa: E712
    ).order_by(ObjectiveFact.created_at.desc())).first()
    now = datetime.now(timezone.utc)
    if existing and existing.fact_value == value:
        return existing
    if existing:
        existing.active = False
        existing.superseded_at = now
        session.add(existing)
    fact = ObjectiveFact(
        user_id=user_id, objective_id=scoped_objective, scope=scope, fact_key=key,
        fact_value=value, source_message_id=source_message_id, source_type=source_type,
        authoritative=False, supersedes_fact_id=existing.fact_id if existing else None, created_at=now,
    )
    session.add(fact)
    session.flush()
    return fact


def checkpoint_key(objective_id: str, checkpoint: str) -> str:
    return f"{objective_id}:{hashlib.sha256(checkpoint.encode('utf-8')).hexdigest()[:16]}"


def checkpoint_facts(answer: str, checkpoint: str) -> list[dict[str, str]]:
    facts = [{"fact_key": "checkpoint_answer", "fact_value": answer, "scope": "objective"}]
    lower = checkpoint.lower()
    if "date" in lower or "range" in lower or re.search(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b", answer, re.I):
        facts.append({"fact_key": "date_range", "fact_value": answer, "scope": "objective"})
    if "business name" in lower or "fein" in lower:
        facts.append({"fact_key": "business_name_or_fein", "fact_value": answer, "scope": "objective"})
    return facts


def extract_explicit_facts(message: str, objective_id: str | None) -> list[dict[str, str]]:
    """Conservatively recognize user-stated facts; never model inference.

    Cross-objective scope requires explicit general-preference wording.
    Objective facts are only extracted when the conversation is already
    linked to an objective.
    """
    text = message.strip()
    general = re.match(r"(?is)^\s*(?:general preference|for all objectives)\s*[:,-]\s*(.+)$", text)
    if general:
        return [{"fact_key": "general_preference", "fact_value": general.group(1).strip(), "scope": "general"}]
    if not objective_id:
        return []
    stated_range = re.search(r"(?is)\b(?:my\s+)?date\s+range\s+(?:is|should be)\s+(.+?)[.!]?\s*$", text)
    if stated_range:
        return [{"fact_key": "date_range", "fact_value": stated_range.group(1).strip().rstrip("."), "scope": "objective"}]
    correction = re.search(r"(?is)^\s*correction\s*[—:-]\s*(?:please\s+)?use\s+(.+?)[.!]?\s*$", text)
    if correction:
        return [{"fact_key": "date_range", "fact_value": correction.group(1).strip().rstrip("."), "scope": "objective"}]
    return []


def fact_context(rows: Iterable[ObjectiveFact]) -> dict[str, str]:
    return {row.fact_key: row.fact_value for row in rows}
