"""
Tests for Commander's identity-field storage (DOB, SSN, address, etc.).

These fields are deliberately NOT in the database and NEVER included in
Hunter AI's chat context — they live only in environment variables
(Render's own encrypted secrets store), set directly by Commander
through the Render dashboard. Hunter AI only ever sees which fields are
PRESENT (booleans), never the values. No executor exists yet that reads
an actual value — this is storage-only infrastructure, per the standing
addendum rule that a consequential external submission stays its own
separate Commander-approved step.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services import commander_identity


def _make_session() -> Session:
    import app.models.hunter_ledger  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.budget  # noqa: F401
    import app.models.execution_outcome  # noqa: F401
    import app.models.strategy  # noqa: F401
    import app.models.copy_signal  # noqa: F401
    import app.models.forge  # noqa: F401
    import app.models.commander_document  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.alert  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_no_fields_present_when_no_env_vars_set(monkeypatch):
    for env_var in commander_identity.FIELD_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)
    presence = commander_identity.get_identity_field_presence()
    assert all(v is False for v in presence.values())
    assert set(presence.keys()) == set(commander_identity.FIELD_ENV_VARS.keys())


def test_presence_reflects_only_set_fields(monkeypatch):
    for env_var in commander_identity.FIELD_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("HUNTER_COMMANDER_DOB", "1990-01-01")
    monkeypatch.setenv("HUNTER_COMMANDER_SSN", "123-45-6789")

    presence = commander_identity.get_identity_field_presence()
    assert presence["dob"] is True
    assert presence["ssn"] is True
    assert presence["full_name"] is False
    assert presence["address_line1"] is False


def test_get_identity_field_returns_real_value_for_known_field(monkeypatch):
    monkeypatch.setenv("HUNTER_COMMANDER_SSN", "123-45-6789")
    assert commander_identity.get_identity_field("ssn") == "123-45-6789"


def test_get_identity_field_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("HUNTER_COMMANDER_SSN", raising=False)
    assert commander_identity.get_identity_field("ssn") is None


def test_get_identity_field_rejects_unknown_field():
    with pytest.raises(ValueError):
        commander_identity.get_identity_field("bank_account_number")


# ── Chat context: presence only, never values ──────────────────────────


def test_chat_prompt_lists_field_names_but_never_values(monkeypatch):
    from app.routers.assistant import _build_system_prompt, _gather_context

    monkeypatch.setenv("HUNTER_COMMANDER_SSN", "123-45-6789")
    monkeypatch.setenv("HUNTER_COMMANDER_DOB", "1990-01-01")

    session = _make_session()
    ctx = _gather_context(session)
    prompt = _build_system_prompt(ctx)

    assert "ssn" in prompt
    assert "dob" in prompt
    assert "123-45-6789" not in prompt
    assert "1990-01-01" not in prompt


def test_chat_prompt_safe_when_no_identity_fields_present(monkeypatch):
    from app.routers.assistant import _build_system_prompt, _gather_context

    for env_var in commander_identity.FIELD_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)

    session = _make_session()
    ctx = _gather_context(session)
    prompt = _build_system_prompt(ctx)
    assert "none on file" in prompt


def test_chat_prompt_instructs_model_never_to_fabricate_a_value():
    from app.routers.assistant import _build_system_prompt, _gather_context

    session = _make_session()
    ctx = _gather_context(session)
    prompt = _build_system_prompt(ctx)
    assert "never state, guess, or fabricate" in prompt
