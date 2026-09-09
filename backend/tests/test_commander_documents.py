"""
Tests for Commander document storage: upload/extraction, the seeded
capability profile, and its wiring into Hunter AI chat context and
opportunity capability-fit scoring.

Storage only — these tests never touch execution accounting, trading,
or any "apply on Commander's behalf" action, because no such executor
exists. That boundary is deliberate (see commander_documents.py docstring).
"""

from __future__ import annotations

import io

import docx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.commander_document import CommanderDocument
from app.services import commander_documents as docs_svc


def _make_session() -> Session:
    import app.models.commander_document  # noqa: F401
    import app.models.hunter_ledger  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.budget  # noqa: F401
    import app.models.execution_outcome  # noqa: F401
    import app.models.strategy  # noqa: F401
    import app.models.copy_signal  # noqa: F401
    import app.models.forge  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.event  # noqa: F401
    import app.models.alert  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_docx_bytes(text: str) -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ── Extraction / storage ────────────────────────────────────────────────


def test_save_txt_document_extracts_text_and_persists(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DOCUMENTS_DIR", str(tmp_path))
    session = _make_session()
    doc = docs_svc.save_document(
        session, filename="notes.txt", content_type="text/plain",
        raw_bytes=b"hello hunter", document_type="other",
    )
    assert doc.extracted_text == "hello hunter"
    assert doc.document_id
    row = session.exec(select(CommanderDocument).where(CommanderDocument.document_id == doc.document_id)).first()
    assert row is not None


def test_save_docx_document_extracts_paragraph_text(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DOCUMENTS_DIR", str(tmp_path))
    session = _make_session()
    raw = _make_docx_bytes("Eddie Murphy capability summary line one.")
    doc = docs_svc.save_document(
        session, filename="resume.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        raw_bytes=raw, document_type="resume",
    )
    assert "Eddie Murphy capability summary line one." in doc.extracted_text


def test_unsupported_document_type_raises_without_persisting(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DOCUMENTS_DIR", str(tmp_path))
    session = _make_session()
    with pytest.raises(docs_svc.UnsupportedDocumentType):
        docs_svc.save_document(
            session, filename="archive.zip", content_type="application/zip",
            raw_bytes=b"PK\x03\x04", document_type="other",
        )
    assert session.exec(select(CommanderDocument)).all() == []


def test_disk_write_failure_does_not_block_db_persistence(monkeypatch, tmp_path):
    # A regular file where a directory is expected forces mkdir(parents=True)
    # to fail regardless of the sandbox's filesystem permissions (root can
    # still write anywhere, but not *through* a plain file as if it were a
    # directory).
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocking")
    monkeypatch.setenv("HUNTER_DOCUMENTS_DIR", str(blocking_file / "sub"))
    session = _make_session()
    doc = docs_svc.save_document(
        session, filename="notes.txt", content_type="text/plain",
        raw_bytes=b"still saved", document_type="other",
    )
    assert doc.extracted_text == "still saved"
    assert doc.stored_path is None  # disk write failed, but the row exists


# ── Seeded capability profile ──────────────────────────────────────────


def test_seed_capability_profile_creates_one_document(tmp_path):
    session = _make_session()
    seeded = docs_svc.seed_capability_profile(session)
    assert seeded is not None
    assert seeded.document_type == "capability_profile"
    assert seeded.source == "seed"
    assert "EMURPH" in seeded.extracted_text.upper()


def test_seed_capability_profile_is_idempotent():
    session = _make_session()
    first = docs_svc.seed_capability_profile(session)
    second = docs_svc.seed_capability_profile(session)
    assert first is not None
    assert second is None  # already seeded — no duplicate
    rows = session.exec(
        select(CommanderDocument).where(CommanderDocument.document_type == "capability_profile")
    ).all()
    assert len(rows) == 1


def test_get_capability_profile_text_returns_none_when_unseeded():
    session = _make_session()
    assert docs_svc.get_capability_profile_text(session) is None


def test_get_capability_profile_text_returns_seeded_content():
    session = _make_session()
    docs_svc.seed_capability_profile(session)
    text = docs_svc.get_capability_profile_text(session)
    assert text is not None
    assert "NO-AUTO-REJECTION RULE" in text


# ── Chat context wiring ─────────────────────────────────────────────────


def test_assistant_context_includes_capability_profile_once_seeded():
    from app.routers.assistant import _gather_context, _build_system_prompt

    session = _make_session()
    docs_svc.seed_capability_profile(session)

    ctx = _gather_context(session)
    assert ctx["capability_profile"] is not None
    assert ctx["document_count"] == 1

    prompt = _build_system_prompt(ctx)
    assert "COMMANDER'S CAPABILITY & EXPERIENCE PROFILE" in prompt
    assert "EMURPH" in prompt.upper()


def test_assistant_context_safe_with_no_documents_at_all():
    from app.routers.assistant import _gather_context, _build_system_prompt

    session = _make_session()
    ctx = _gather_context(session)
    assert ctx["capability_profile"] is None
    prompt = _build_system_prompt(ctx)  # must not raise
    assert "COMMANDER'S CAPABILITY" not in prompt


def test_prompt_points_to_document_upload_before_generic_deflection():
    from app.routers.assistant import _gather_context, _build_system_prompt

    session = _make_session()
    prompt = _build_system_prompt(_gather_context(session))
    assert "document-upload feature" in prompt.lower()


# ── Capability-fit scoring wiring ──────────────────────────────────────


def test_capability_fit_matches_new_tags_from_profile():
    from app.services.capability_fit import score_capability_fit

    result = score_capability_fit(
        description="AI safety and responsible AI grant for autonomous agent governance research",
        next_action="", category="grant", origin_module="",
    )
    assert result.score > 0
    assert "ai_safety_and_agent_governance" in result.matched_tags


def test_capability_fit_matches_healthcare_it_tag():
    from app.services.capability_fit import score_capability_fit

    result = score_capability_fit(
        description="Veterans health IT modernization contract requiring EHR experience",
        next_action="", category="contract", origin_module="",
    )
    assert "healthcare_it_and_government_health_tech" in result.matched_tags
