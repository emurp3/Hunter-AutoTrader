from __future__ import annotations

import os
import time
from typing import Any

import httpx

# Demonstrated failure (2026-09-10, EOD recovery): a brief 502 from
# Render's load balancer during the web service's own blue-green
# redeploy hit the /escalate callback for an already-decided task
# (contact info was already confirmed missing — no real action was
# lost). The worker had already done the only irreversible-relevant
# work (deciding there was no contact route); only the follow-up report
# to the server failed. With no retry, the task sat in `executing` with
# no way to close out until its 120s lease expired, at which point
# stale-lease reclaim correctly-but-wastefully replayed the whole task.
# For send-capable outcomes this same gap could replay a real SMTP send.
# Bounded retry on the terminal callbacks (complete/fail/escalate) closes
# it: report the already-decided outcome a few more times, comfortably
# inside the lease window, before ever falling back to a full reclaim.
_CALLBACK_RETRY_DELAYS = (2.0, 4.0, 8.0)


class HunterWorkerClient:
    def __init__(self) -> None:
        base_url = os.getenv("HUNTER_BASE_URL", "").rstrip("/")
        if not base_url:
            raise RuntimeError("HUNTER_BASE_URL is required for the hosted HVA worker")
        self.base_url = base_url
        timeout = float(os.getenv("HUNTER_HTTP_TIMEOUT_SECONDS", "30"))
        _worker_token = os.getenv("HUNTER_WORKER_TOKEN", "")
        _headers = {"Authorization": f"Bearer {_worker_token}"} if _worker_token else {}
        self._client = httpx.Client(base_url=base_url, timeout=timeout, headers=_headers)

    def close(self) -> None:
        self._client.close()

    def _post_terminal_with_retry(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        """POST a terminal task callback (complete/fail/escalate) — the
        underlying work this reports is already decided; only the HTTP
        call itself can still fail transiently. Retries on a 5xx
        response or a transport-level error (never on 4xx — that's a
        real rejection, not a transient outage), bounded well under the
        task lease so a brief outage doesn't strand the task for a full
        reclaim cycle."""
        last_exc: Exception | None = None
        for delay in (0.0, *_CALLBACK_RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                response = self._client.post(path, json=json_body)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def claim_task(self, worker_id: str) -> dict[str, Any] | None:
        response = self._client.post("/tasks/claim", json={"worker_id": worker_id})
        response.raise_for_status()
        payload = response.json()
        return payload.get("task")

    def heartbeat(self, task_id: str, worker_id: str) -> None:
        response = self._client.post(
            f"/tasks/{task_id}/heartbeat",
            json={"worker_id": worker_id},
        )
        response.raise_for_status()

    def record_pending_outcome(
        self,
        task_id: str,
        worker_id: str,
        *,
        outcome: dict[str, Any],
        notes: str = "",
        screenshot_path: str | None = None,
        page_url: str | None = None,
        trace_reference: str | None = None,
        engine: str = "playwright",
    ) -> dict[str, Any]:
        """Durably record a real outcome (server-side, survives a worker
        restart) before attempting the terminal /complete report — see
        _post_terminal_with_retry's comment for the transient-outage case
        this complements. Retried the same way; if every retry is
        exhausted here too, the caller still has its in-process cache as
        a same-process fallback, and the next /complete attempt (or a
        later reclaim, once this does succeed) closes the gap."""
        return self._post_terminal_with_retry(
            f"/tasks/{task_id}/record-outcome",
            {
                "worker_id": worker_id,
                "outcome": outcome,
                "notes": notes,
                "screenshot_path": screenshot_path,
                "page_url": page_url,
                "trace_reference": trace_reference,
                "engine": engine,
            },
        )

    def complete(
        self,
        task_id: str,
        worker_id: str,
        *,
        outcome: dict[str, Any],
        notes: str = "",
        screenshot_path: str | None = None,
        page_url: str | None = None,
        trace_reference: str | None = None,
        engine: str = "playwright",
    ) -> dict[str, Any]:
        return self._post_terminal_with_retry(
            f"/tasks/{task_id}/complete",
            {
                "worker_id": worker_id,
                "outcome": outcome,
                "notes": notes,
                "screenshot_path": screenshot_path,
                "page_url": page_url,
                "trace_reference": trace_reference,
                "engine": engine,
            },
        )

    def fail(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str,
        error_text: str | None = None,
        screenshot_path: str | None = None,
        page_url: str | None = None,
        trace_reference: str | None = None,
        engine: str = "playwright",
    ) -> dict[str, Any]:
        return self._post_terminal_with_retry(
            f"/tasks/{task_id}/fail",
            {
                "worker_id": worker_id,
                "reason": reason,
                "error_text": error_text,
                "screenshot_path": screenshot_path,
                "page_url": page_url,
                "trace_reference": trace_reference,
                "engine": engine,
            },
        )

    def escalate(
        self,
        task_id: str,
        worker_id: str,
        *,
        escalation_type: str,
        reason: str,
        error_text: str | None = None,
        screenshot_path: str | None = None,
        page_url: str | None = None,
        trace_reference: str | None = None,
        engine: str = "playwright",
    ) -> dict[str, Any]:
        return self._post_terminal_with_retry(
            f"/tasks/{task_id}/escalate",
            {
                "worker_id": worker_id,
                "escalation_type": escalation_type,
                "reason": reason,
                "error_text": error_text,
                "screenshot_path": screenshot_path,
                "page_url": page_url,
                "trace_reference": trace_reference,
                "engine": engine,
            },
        )

    def notify(
        self,
        *,
        title: str,
        body: str,
        source_id: str | None = None,
        worker_id: str | None = None,
        priority: str = "medium",
        alert_type: str = "review_required",
    ) -> dict[str, Any]:
        response = self._client.post(
            "/system/notify",
            json={
                "title": title,
                "body": body,
                "priority": priority,
                "alert_type": alert_type,
                "source_id": source_id,
                "worker_id": worker_id,
            },
        )
        response.raise_for_status()
        return response.json()

