"""
Commander, 2026-09-11: "verify that an external success followed by
exhausted callback retries cannot cause duplicate execution."

HunterWorkerClient's terminal callbacks already retry a transient
failure (the confirmed real case: a brief 502 during the web service's
own redeploy). This covers what retry alone cannot: if the outage
outlasts every retry, _process_task must not silently let the task
fall back to being re-claimed and re-executed from scratch — the
in-process outcome cache means a reclaim landing on the SAME worker
process re-reports the already-decided outcome (e.g. "the email was
sent") instead of running execute_task again and sending a second real
email.
"""

from __future__ import annotations

import app.worker.main as worker_main
from app.worker.executors import WorkerExecutionError, WorkerResult


class _FakeClient:
    def __init__(self, *, complete_failures: int = 0) -> None:
        self.complete_calls: list[dict] = []
        self.escalate_calls: list[dict] = []
        self.fail_calls: list[dict] = []
        self.notify_calls: list[dict] = []
        self.record_pending_outcome_calls: list[dict] = []
        self._complete_failures_remaining = complete_failures

    def notify(self, **kwargs):
        self.notify_calls.append(kwargs)

    def heartbeat(self, task_id, worker_id):
        pass

    def record_pending_outcome(self, task_id, worker_id, **kwargs):
        self.record_pending_outcome_calls.append({"task_id": task_id, "worker_id": worker_id, **kwargs})

    def complete(self, task_id, worker_id, **kwargs):
        if self._complete_failures_remaining > 0:
            self._complete_failures_remaining -= 1
            raise RuntimeError("simulated persistent outage — all retries exhausted")
        self.complete_calls.append({"task_id": task_id, "worker_id": worker_id, **kwargs})
        return {"task_id": task_id, "status": "completed"}

    def escalate(self, task_id, worker_id, **kwargs):
        self.escalate_calls.append({"task_id": task_id, "worker_id": worker_id, **kwargs})
        return {"task_id": task_id, "status": "escalated"}

    def fail(self, task_id, worker_id, **kwargs):
        self.fail_calls.append({"task_id": task_id, "worker_id": worker_id, **kwargs})
        return {"task_id": task_id, "status": "failed"}


def setup_function(_):
    worker_main._reported_outcome_cache.clear()


def _task(task_id: str = "t1") -> dict:
    return {"task_id": task_id, "task_type": "service_outreach", "attempts": 1, "max_attempts": 3}


def test_successful_completion_leaves_no_cached_outcome(monkeypatch):
    execute_calls = []

    def _fake_execute_task(task, worker_id):
        execute_calls.append(task["task_id"])
        return WorkerResult(outcome={"email_sent": True}, notes="sent", engine="claude_cu")

    monkeypatch.setattr(worker_main, "execute_task", _fake_execute_task)
    client = _FakeClient()

    worker_main._process_task(client, "worker-1", _task())

    assert len(client.complete_calls) == 1
    assert execute_calls == ["t1"]
    assert "t1" not in worker_main._reported_outcome_cache
    # Durable record (Commander, 2026-09-11: must survive a worker
    # restart, not just the in-process reclaim case) is attempted before
    # the terminal /complete report.
    assert len(client.record_pending_outcome_calls) == 1
    assert client.record_pending_outcome_calls[0]["outcome"]["email_sent"] is True


def test_exhausted_callback_after_real_success_does_not_lose_the_outcome(monkeypatch):
    """The email was genuinely sent (execute_task succeeded), but every
    retry attempt to report it back failed — this must not be treated
    as a failed task, and the real outcome must survive for a
    same-process reclaim to re-report."""

    def _fake_execute_task(task, worker_id):
        return WorkerResult(outcome={"email_sent": True, "contact_email": "x@example.com"}, notes="sent", engine="claude_cu")

    monkeypatch.setattr(worker_main, "execute_task", _fake_execute_task)
    client = _FakeClient(complete_failures=999)  # never succeeds — simulates exhausted retries

    worker_main._process_task(client, "worker-1", _task())

    assert client.escalate_calls == []  # never misreported as a failure
    assert client.fail_calls == []
    cached = worker_main._reported_outcome_cache.get("t1")
    assert cached is not None
    assert cached["outcome"]["email_sent"] is True


def test_reclaim_of_a_cached_outcome_never_reexecutes_and_never_resends(monkeypatch):
    """The concrete duplicate-send scenario: task reported success is
    cached (callback exhausted), then a stale-lease reclaim hands the
    SAME task back to _process_task. execute_task must not run again."""
    execute_calls = []

    def _fake_execute_task(task, worker_id):
        execute_calls.append(task["task_id"])
        return WorkerResult(outcome={"email_sent": True}, notes="sent", engine="claude_cu")

    monkeypatch.setattr(worker_main, "execute_task", _fake_execute_task)

    # First attempt: execute succeeds, but the callback is down.
    failing_client = _FakeClient(complete_failures=999)
    worker_main._process_task(failing_client, "worker-1", _task())
    assert execute_calls == ["t1"]
    assert "t1" in worker_main._reported_outcome_cache

    # Reclaim: outage has cleared, but execute_task must NOT run again —
    # only the cached outcome is re-reported.
    recovered_client = _FakeClient()
    worker_main._process_task(recovered_client, "worker-1", _task())

    assert execute_calls == ["t1"]  # still only once — no duplicate send
    assert len(recovered_client.complete_calls) == 1
    assert recovered_client.complete_calls[0]["outcome"]["email_sent"] is True
    assert "t1" not in worker_main._reported_outcome_cache  # cleared once confirmed reported


def test_a_genuine_failure_is_never_cached(monkeypatch):
    def _fake_execute_task(task, worker_id):
        raise WorkerExecutionError("no contact route", escalation_type="contact_unavailable")

    monkeypatch.setattr(worker_main, "execute_task", _fake_execute_task)
    client = _FakeClient()

    worker_main._process_task(client, "worker-1", _task())

    assert len(client.escalate_calls) == 1
    assert client.complete_calls == []
    assert "t1" not in worker_main._reported_outcome_cache
