import json
import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from app.models.task import Task, TaskStatus, EscalationType
from app.services import tasks, outreach_smtp as smtp
from app.models.income_source import IncomeSource
from app.models.decision import OpportunityDecision


@pytest.fixture
def engine(tmp_path, monkeypatch):
    # A real file database, reopened through separate connections after loss.
    engine = create_engine("sqlite:///" + str(tmp_path / "tasks.db"), connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(tasks.alert_svc, "raise_alert", lambda **kw: None)
    monkeypatch.setattr(tasks.event_svc, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(tasks, "_close_loop", lambda *a, **kw: None)
    yield engine
    engine.dispose()


def claim(engine, source="test-source"):
    with Session(engine) as s:
        s.add(IncomeSource(source_id=source, description="Test offer", estimated_profit=0, date_found=date.today()))
        s.add(OpportunityDecision(source_id=source, execution_ready=True,
                                 action_state="auto_execute", execution_path="outreach"))
        s.commit()
        t = tasks.dispatch_task("service_outreach", {}, s, source_id=source, idempotency_key=source)
        t = tasks.claim_task("boot-1", s)
        return t.task_id, t.attempts


def reserve(engine, tid, n, recipient="owner@example.test"):
    with Session(engine) as s:
        tasks.begin_outreach(tid, "boot-1", n, {"to": recipient, "body": "exact proposed action"}, s)


def expire(engine, tid):
    with Session(engine) as s:
        t = s.exec(select(Task).where(Task.task_id == tid)).one()
        t.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.add(t)
        s.commit()


@pytest.mark.parametrize("external_action_occurred", [False, True])
def test_restart_after_permit_never_replays_even_without_outcome(engine, external_action_occurred):
    tid, n = claim(engine)
    reserve(engine, tid, n)
    # Whether SMTP happened or not is unknowable after loss. No local cache.
    sends = [tid] if external_action_occurred else []
    expire(engine, tid)
    with Session(engine) as s:
        assert tasks.claim_task("different-boot", s) is None
        t = s.exec(select(Task).where(Task.task_id == tid)).one()
        assert t.status == TaskStatus.escalated
        assert t.escalation_type == EscalationType.external_outcome_uncertain
        assert json.loads(t.outreach_intent_json)["body"] == "exact proposed action"
        with pytest.raises(ValueError):
            tasks.retry_task(tid, s)
        duplicate = tasks.dispatch_task("service_outreach", {"changed": True}, s,
            source_id="test-source", idempotency_key="new-key")
        assert duplicate.task_id == tid
    assert len(sends) == int(external_action_occurred)


def test_recorded_acceptance_finalizes_after_restart(engine):
    tid, n = claim(engine)
    reserve(engine, tid, n)
    with Session(engine) as s:
        tasks.record_pending_outcome(tid, s, worker_id="boot-1",
            outcome={"send_status": "accepted", "email_sent": True, "smtp_receipt": {"smtp_code": 250}})
    expire(engine, tid)
    with Session(engine) as s:
        assert tasks.claim_task("new-boot", s) is None
        t = s.exec(select(Task).where(Task.task_id == tid)).one()
        assert t.status == TaskStatus.completed
        assert json.loads(t.outcome)["smtp_receipt"]["smtp_code"] == 250
        assert t.outreach_intent_json


@pytest.mark.parametrize("status", ["uncertain", "rejected"])
def test_nonaccepted_receipt_persists_without_completion_credit(engine, status):
    tid, n = claim(engine)
    reserve(engine, tid, n)
    receipt = {"send_status": status, "email_sent": False, "smtp_receipt": {"phase": "data"}}
    with Session(engine) as s:
        tasks.record_pending_outcome(tid, s, worker_id="boot-1", outcome=receipt)
    expire(engine, tid)
    with Session(engine) as s:
        assert tasks.claim_task("boot-2", s) is None
        t = s.exec(select(Task).where(Task.task_id == tid)).one()
        assert t.status == TaskStatus.escalated
        assert json.loads(t.outcome) == receipt
        assert t.outreach_intent_json


def test_late_acceptance_resolves_uncertainty_and_duplicate_report_is_noop(engine):
    tid, n = claim(engine)
    reserve(engine, tid, n)
    expire(engine, tid)
    with Session(engine) as s:
        tasks.claim_task("boot-2", s)
        receipt = {"send_status": "accepted", "email_sent": True}
        t = tasks.record_pending_outcome(tid, s, worker_id="boot-1", outcome=receipt)
        assert t.status == TaskStatus.completed and not t.must_escalate
        tasks.complete_task(tid, receipt, s, worker_id_override="boot-1")
        with pytest.raises(ValueError):
            tasks.fail_task(tid, "stale", s, worker_id_override="boot-2")
        assert t.status == TaskStatus.completed


def test_revoked_approval_blocks_send_permit_and_rolls_back_intent(engine):
    tid, n = claim(engine)
    with Session(engine) as s:
        d = s.exec(select(OpportunityDecision)).one()
        d.approval_required = True
        s.add(d)
        s.commit()
    with pytest.raises(ValueError, match="authorized"):
        reserve(engine, tid, n)
    with Session(engine) as s:
        assert s.exec(select(Task)).one().outreach_intent_json is None


def test_competing_workers_only_one_claim(engine):
    with Session(engine) as s:
        tasks.dispatch_task("service_outreach", {}, s)
    def take(worker):
        with Session(engine) as s:
            t = tasks.claim_task(worker, s)
            return t.task_id if t else None
    with ThreadPoolExecutor(2) as pool:
        got = list(pool.map(take, ["a", "b"]))
    assert sum(x is not None for x in got) == 1


def test_one_shot_permit_and_duplicate_recipient(engine):
    tid, n = claim(engine)
    reserve(engine, tid, n)
    with pytest.raises(ValueError):
        reserve(engine, tid, n)
    other, count = claim(engine, source="different-source")
    with pytest.raises(ValueError, match="already has"):
        reserve(engine, other, count)
    with Session(engine) as s:
        assert s.exec(select(Task).where(Task.task_id == other)).one().outreach_intent_json is None


def test_expired_or_wrong_owner_cannot_begin_or_report(engine):
    tid, n = claim(engine)
    with Session(engine) as s:
        with pytest.raises(ValueError):
            tasks.begin_outreach(tid, "stale", n, {"to": "x@example.test"}, s)
        with pytest.raises(ValueError):
            tasks.complete_task(tid, {"email_sent": True}, s, worker_id_override="stale")
    expire(engine, tid)
    with pytest.raises(ValueError):
        reserve(engine, tid, n)


def test_configuration_retry_is_narrow(engine):
    tid, n = claim(engine)
    with Session(engine) as s:
        tasks.escalate_task(tid, EscalationType.credentials_required, "Missing SMTP_HOST", s)
        t = tasks.retry_task(tid, s)
        assert t.status == TaskStatus.retrying and not t.must_escalate
        tasks.claim_task("boot-2", s)
        tasks.escalate_task(tid, EscalationType.commander_boundary, "skip", s)
        with pytest.raises(ValueError):
            tasks.retry_task(tid, s)


class FakeSMTP:
    def __init__(self, *a, **kw):
        self.calls = []
        self.data_error = None
        self.auth_error = False
        self.rcpt_code = 250
    def ehlo(self): pass
    def starttls(self, **kw): self.calls.append("tls")
    def login(self, *a):
        self.calls.append("login")
        if self.auth_error:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    def mail(self, sender):
        self.calls.append("mail")
        return 250, b"OK"
    def rcpt(self, recipient):
        self.calls.append("rcpt")
        return self.rcpt_code, b"recipient response"
    def data(self, wire):
        assert b"\n" not in wire.replace(b"\r\n", b"")
        self.calls.append("data")
        if self.data_error:
            raise self.data_error
        return 250, b"queued as isolated-test-123"
    def close(self): self.calls.append("close")


@pytest.fixture
def transport(monkeypatch):
    fake = FakeSMTP()
    for key, value in {"SMTP_HOST": "smtp.example.test", "SMTP_USERNAME": "sender@example.test",
                       "SMTP_PASSWORD": "isolated-test", "SMTP_PORT": 587}.items():
        monkeypatch.setattr(smtp.config, key, value)
    monkeypatch.setattr(smtp.smtplib, "SMTP", lambda *a, **kw: fake)
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", lambda *a, **kw: fake)
    return fake


def test_lost_permit_ack_does_not_submit(engine, transport):
    tid, n = claim(engine)
    def lost_ack(intent):
        with Session(engine) as s:
            tasks.begin_outreach(tid, "boot-1", n, intent, s)
        raise ConnectionError("response lost")
    with pytest.raises(smtp.PermitError):
        smtp.send_outreach("owner@example.test", "Offer", "Body", before_send=lost_ack)
    assert "mail" not in transport.calls
    expire(engine, tid)
    with Session(engine) as s:
        assert tasks.claim_task("other", s) is None


def test_smtp_acceptance_then_process_loss_has_no_second_submission(engine, transport):
    tid, n = claim(engine)
    def permit(intent):
        with Session(engine) as s:
            tasks.begin_outreach(tid, "boot-1", n, intent, s)
        transport.calls.append("durable_permit")
    receipt = smtp.send_outreach("owner@example.test", "Offer", "Body", before_send=permit)
    assert receipt["smtp_reply"] == "queued as isolated-test-123"
    assert transport.calls.index("login") < transport.calls.index("durable_permit") < transport.calls.index("data")
    # Intentionally discard receipt, open a different DB session, expire lease.
    expire(engine, tid)
    with Session(engine) as s:
        assert tasks.claim_task("other", s) is None
    assert transport.calls.count("data") == 1


@pytest.mark.parametrize("failure,expected", [(ConnectionError(), "uncertain"),
    (smtplib.SMTPDataError(550, b"rejected"), "rejected")])
def test_transport_failure_classification(transport, failure, expected):
    transport.data_error = failure
    result = smtp.send_outreach("owner@example.test", "Offer", "Body", before_send=lambda i: None)
    assert result["send_status"] == expected and result["email_sent"] is False


def test_authentication_fails_before_any_reservation(transport):
    transport.auth_error = True
    permits = []
    with pytest.raises(smtp.PreSendError, match="authentication"):
        smtp.send_outreach("owner@example.test", "Offer", "Body", before_send=permits.append)
    assert permits == [] and "mail" not in transport.calls


def test_recipient_rejection_never_sends_data(transport):
    transport.rcpt_code = 550
    result = smtp.send_outreach("owner@example.test", "Offer", "Body", before_send=lambda i: None)
    assert result["send_status"] == "rejected" and result["smtp_code"] == 550
    assert "data" not in transport.calls


@pytest.mark.parametrize("recipient", ["a@example.test,b@example.test", "a@example.test\nBcc: x@example.test", "invalid"])
def test_invalid_recipient_cannot_connect_or_reserve(transport, recipient):
    permits = []
    with pytest.raises(smtp.PreSendError):
        smtp.send_outreach(recipient, "Offer", "Body", before_send=permits.append)
    assert not transport.calls and not permits


def test_465_uses_implicit_tls(transport, monkeypatch):
    monkeypatch.setattr(smtp.config, "SMTP_PORT", 465)
    result = smtp.send_outreach("owner@example.test", "Offer", "Body", before_send=lambda i: None)
    assert result["send_status"] == "accepted" and "tls" not in transport.calls
