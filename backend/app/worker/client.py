from __future__ import annotations

import os
from typing import Any

import httpx


class HunterWorkerClient:
    def __init__(self) -> None:
        base_url = os.getenv("HUNTER_BASE_URL", "").rstrip("/")
        if not base_url:
            raise RuntimeError("HUNTER_BASE_URL is required for the hosted HVA worker")
        self.base_url = base_url
        timeout = float(os.getenv("HUNTER_HTTP_TIMEOUT_SECONDS", "30"))
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

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
        response = self._client.post(
            f"/tasks/{task_id}/complete",
            json={
                "worker_id": worker_id,
                "outcome": outcome,
                "notes": notes,
                "screenshot_path": screenshot_path,
                "page_url": page_url,
                "trace_reference": trace_reference,
                "engine": engine,
            },
        )
        response.raise_for_status()
        return response.json()

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
        response = self._client.post(
            f"/tasks/{task_id}/fail",
            json={
                "worker_id": worker_id,
                "reason": reason,
                "error_text": error_text,
                "screenshot_path": screenshot_path,
                "page_url": page_url,
                "trace_reference": trace_reference,
                "engine": engine,
            },
        )
        response.raise_for_status()
        return response.json()

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
        response = self._client.post(
            f"/tasks/{task_id}/escalate",
            json={
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
        response.raise_for_status()
        return response.json()

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

