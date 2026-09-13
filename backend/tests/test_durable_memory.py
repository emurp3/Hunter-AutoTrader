from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.event import OpportunityEvent
from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.models.memory import ConversationMessage, ObjectiveFact
from app.models.task import EscalationType, Task
from app.routers import assistant
from app.routers.hunter_ledger import answer_commander_checkpoint
from app.services import execution_accounting as acct
from app.services import memory as memory_svc
from app.services import tasks as task_svc


def _engine(tmp_path):
    import app.models.action_packet  # noqa: F401
    import app.models.alert  # noqa: F401
    import app.models.income_source  # noqa: F401
    import app.models.memory  # noqa: F401
    import app.models.task  # noqa: F401
    engine = create_engine(
        f"sqlite:///{tmp_path / 'memory.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _candidate(session, cid="OBJ-A", checkpoint="What date range should Hunter use?"):
    opp = CanonicalOpportunity(
        canonical_opportunity_id=cid,
        lane="test",
        factual_mechanism="test objective",
        source_provenance="unit test",
        freshness_date=date(2026, 9, 12),
        disposition=Disposition.pending_commander.value,
        required_commander_checkpoints=checkpoint,
    )
    session.add(opp)
    session.commit()
    acct.record_rescue_attempt(session, cid, "alternate_channel", "tested", result="found")
    return opp


def test_correction_is_append_only_and_survives_new_session(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        conversation = memory_svc.resolve_conversation(session, objective_id="OBJ-A")
        first_message = memory_svc.add_message(
            session, conversation, role="user",
            content="My date range is January 1 through March 31.", objective_id="OBJ-A",
        )
        first = memory_svc.promote_fact(
            session, fact_key="date_range", fact_value="January 1 through March 31",
            objective_id="OBJ-A", source_message_id=first_message.message_id,
        )
        second_message = memory_svc.add_message(
            session, conversation, role="user",
            content="Correction — use January 15 through March 31.", objective_id="OBJ-A",
        )
        second = memory_svc.promote_fact(
            session, fact_key="date_range", fact_value="January 15 through March 31",
            objective_id="OBJ-A", source_message_id=second_message.message_id,
        )
        session.commit()
        assert second.supersedes_fact_id == first.fact_id

    with Session(engine) as restarted:
        all_rows = restarted.exec(select(ObjectiveFact).where(ObjectiveFact.fact_key == "date_range")).all()
        assert len(all_rows) == 2
        assert next(row for row in all_rows if row.fact_id == first.fact_id).active is False
        assert memory_svc.fact_context(memory_svc.active_facts(restarted, "OBJ-A"))["date_range"] == "January 15 through March 31"


def test_explicit_date_correction_parser_does_not_infer_cross_objective_scope():
    assert memory_svc.extract_explicit_facts(
        "My date range is January 1 through March 31.", "OBJ-A"
    )[0]["fact_value"] == "January 1 through March 31"
    assert memory_svc.extract_explicit_facts(
        "Correction — use January 15 through March 31.", "OBJ-A"
    )[0]["fact_value"] == "January 15 through March 31"
    assert memory_svc.extract_explicit_facts("My date range is January 1 through March 31.", None) == []
    general = memory_svc.extract_explicit_facts(
        "General preference: prefer email contact when available", None
    )
    assert general == [{
        "fact_key": "general_preference",
        "fact_value": "prefer email contact when available",
        "scope": "general",
    }]


def test_objective_isolation_and_explicit_general_preferences(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        memory_svc.promote_fact(session, fact_key="date_range", fact_value="January 15 through March 31", objective_id="OBJ-A")
        memory_svc.promote_fact(
            session, fact_key="contact_preference", fact_value="Prefer email contact when available",
            objective_id=None, scope="general",
        )
        session.commit()
        facts_a = memory_svc.fact_context(memory_svc.active_facts(session, "OBJ-A"))
        facts_b = memory_svc.fact_context(memory_svc.active_facts(session, "OBJ-B"))
        assert facts_a["date_range"] == "January 15 through March 31"
        assert "date_range" not in facts_b
        assert facts_a["contact_preference"] == facts_b["contact_preference"]


def test_chat_persists_and_restores_without_browser_history(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    captured = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            captured.append(kwargs["messages"])
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Remembered."))],
                usage=None,
            )

    monkeypatch.setattr("openai.OpenAI", Client)
    monkeypatch.setattr(assistant, "_gather_context", lambda _: {"account_cash": "0", "top_opps": [], "signals_total": 0})
    monkeypatch.setattr(assistant, "_build_system_prompt", lambda _: "system")

    with Session(engine) as session:
        first = assistant.chat(assistant.ChatRequest(message="My date range is Jan 15 to Mar 31", objective_id="OBJ-A"), session=session)
    with Session(engine) as restarted:
        restored = assistant.current_conversation(conversation_id=first.conversation_id, session=restarted)
        assert [m["content"] for m in restored["messages"]] == ["My date range is Jan 15 to Mar 31", "Remembered."]
        assistant.chat(
            assistant.ChatRequest(message="What did I tell you?", conversation_id=first.conversation_id),
            session=restarted,
        )
    assert any(m["content"] == "My date range is Jan 15 to Mar 31" for m in captured[-1])
    assert any(m["content"] == "Remembered." for m in captured[-1])


def test_checkpoint_answer_links_domain_message_event_fact_and_task(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        _candidate(session)
        task = task_svc.dispatch_task(
            task_type="government_portal_search", spec_payload={"search_url": "https://example.gov"},
            session=session, source_type="canonical_opportunity", source_id="OBJ-A",
        )
        result = answer_commander_checkpoint(
            "OBJ-A", "January 15 through March 31", conversation_id="new-client-id", session=session,
        )
        assert result.commander_response == "January 15 through March 31"
        message = session.exec(select(ConversationMessage).where(ConversationMessage.objective_id == "OBJ-A")).first()
        event = session.exec(select(OpportunityEvent).where(OpportunityEvent.source_id == "OBJ-A", OpportunityEvent.event_type == "checkpoint_answer")).first()
        facts = memory_svc.fact_context(memory_svc.active_facts(session, "OBJ-A"))
        assert message and message.task_id == task.task_id and message.checkpoint_key
        assert event and json.loads(event.metadata_json)["message_id"] == message.message_id
        assert facts["date_range"] == "January 15 through March 31"
        assert facts["checkpoint_answer"] == "January 15 through March 31"


def test_resume_hydrates_current_facts_without_overwriting_original_spec(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        _candidate(session, checkpoint="[MANUAL-ACTION-NEEDED] Complete the interruption")
        original = task_svc.dispatch_task(
            task_type="government_portal_search",
            spec_payload={"search_url": "https://example.gov", "date_range": "authoritative-domain-value"},
            session=session, source_type="canonical_opportunity", source_id="OBJ-A",
        )
        task_svc.escalate_task(original.task_id, EscalationType.commander_boundary, "manual step", session)
        memory_svc.promote_fact(session, fact_key="date_range", fact_value="January 15 through March 31", objective_id="OBJ-A")
        opp = session.exec(select(CanonicalOpportunity).where(CanonicalOpportunity.canonical_opportunity_id == "OBJ-A")).first()
        opp.commander_response = "done"
        from datetime import datetime, timezone
        opp.commander_responded_at = datetime.now(timezone.utc)
        session.add(opp)
        session.commit()
        resumed = task_svc.resume_manual_action_tasks(session)[0]
        spec = json.loads(resumed.spec_payload)
        assert spec["date_range"] == "authoritative-domain-value"
        assert spec["durable_memory"]["active_facts"]["date_range"] == "January 15 through March 31"
        assert spec["durable_memory"]["objective_id"] == "OBJ-A"
        assert spec["durable_memory"]["resumed_from_task_id"] == original.task_id
        assert "MANUAL-ACTION-NEEDED" in spec["durable_memory"]["checkpoint"]
        assert resumed.source_id == original.source_id
        assert resumed.idempotency_key.startswith("resume:OBJ-A:")


def test_secret_material_is_redacted_or_rejected(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        conversation = memory_svc.resolve_conversation(session, objective_id="OBJ-A")
        message = memory_svc.add_message(
            session, conversation, role="user",
            content="password=hunter123 api_key: abc123 Bearer token-value", objective_id="OBJ-A",
        )
        session.commit()
        assert "hunter123" not in message.content
        assert "abc123" not in message.content
        assert "token-value" not in message.content
        with pytest.raises(ValueError):
            memory_svc.promote_fact(session, fact_key="api_key", fact_value="abc123", objective_id="OBJ-A")


def test_recent_context_is_bounded_but_older_messages_remain_stored(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setenv("HUNTER_CHAT_RECENT_MESSAGE_LIMIT", "3")
    with Session(engine) as session:
        conversation = memory_svc.resolve_conversation(session, objective_id="OBJ-A")
        for i in range(6):
            memory_svc.add_message(session, conversation, role="user", content=f"message {i}", objective_id="OBJ-A")
        session.commit()
        assert len(session.exec(select(ConversationMessage)).all()) == 6
        assert [m.content for m in memory_svc.recent_messages(session, conversation.conversation_id)] == ["message 3", "message 4", "message 5"]


def test_frontend_restores_by_stable_id_and_stores_no_conversation_text():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "components" / "HunterAssistant.jsx").read_text(encoding="utf-8")
    assert "/api/assistant/conversations/current" in source
    assert "hunter_conversation_id" in source
    assert "localStorage.setItem('hunter_conversation_id', data.conversation_id)" in source
    assert "localStorage.setItem('hunter_messages'" not in source
    assert "existingIds" in source
