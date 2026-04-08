from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Any

from app.worker.client import HunterWorkerClient
from app.worker.executors import RetryableExecutionError, WorkerExecutionError, execute_task


logging.basicConfig(
    level=os.getenv("HUNTER_WORKER_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("hunter.worker")

_STOP = False


def _handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
    global _STOP
    _STOP = True
    logger.info("received signal %s; shutting down worker loop", signum)


def _heartbeat_loop(
    client: HunterWorkerClient,
    task_id: str,
    worker_id: str,
    stop_event: threading.Event,
) -> None:
    interval = max(15, int(os.getenv("HUNTER_HEARTBEAT_INTERVAL_SECONDS", "45")))
    while not stop_event.wait(interval):
        try:
            client.heartbeat(task_id, worker_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("heartbeat failed for %s: %s", task_id, exc)
            return


def _process_task(client: HunterWorkerClient, worker_id: str, task: dict[str, Any]) -> None:
    task_id = task["task_id"]
    task_type = task.get("task_type") or "unknown"
    source_id = task.get("source_id")
    logger.info("claimed task %s (%s)", task_id, task_type)

    stop_event = threading.Event()
    hb = threading.Thread(
        target=_heartbeat_loop,
        args=(client, task_id, worker_id, stop_event),
        daemon=True,
    )
    hb.start()
    try:
        if task_type == "marketplace_listing":
            client.notify(
                title="Facebook Marketplace login attempt starting",
                body=(
                    "Hunter is about to log in to Facebook Marketplace to execute a task.\n"
                    f"task_id={task_id} | task_type={task_type} | attempt={task.get('attempts', 0)}/"
                    f"{task.get('max_attempts', 0)}\n"
                    "If you receive a Facebook security notification, this is Hunter. "
                    "No action needed unless Facebook blocks the login."
                ),
                source_id=source_id,
                worker_id=worker_id,
                priority="medium",
                alert_type="review_required",
            )
        result = execute_task(task, worker_id)
        client.complete(
            task_id,
            worker_id,
            outcome=result.outcome,
            notes=result.notes,
            screenshot_path=result.screenshot_path,
            page_url=result.page_url,
            trace_reference=result.trace_reference,
            engine=result.engine,
        )
        logger.info("completed task %s", task_id)
    except RetryableExecutionError as exc:
        attempt_num = int(task.get("attempts", 0))
        max_attempts = int(task.get("max_attempts", 0))
        if attempt_num >= max_attempts:
            client.escalate(
                task_id,
                worker_id,
                escalation_type=exc.escalation_type,
                reason=exc.reason,
                error_text=exc.error_text,
                screenshot_path=exc.screenshot_path,
                page_url=exc.page_url,
                trace_reference=exc.trace_reference,
                engine=exc.engine,
            )
            logger.warning("escalated exhausted task %s: %s", task_id, exc.reason)
        else:
            client.fail(
                task_id,
                worker_id,
                reason=exc.reason,
                error_text=exc.error_text,
                screenshot_path=exc.screenshot_path,
                page_url=exc.page_url,
                trace_reference=exc.trace_reference,
                engine=exc.engine,
            )
            logger.warning("marked retryable task %s failed: %s", task_id, exc.reason)
    except WorkerExecutionError as exc:
        client.escalate(
            task_id,
            worker_id,
            escalation_type=exc.escalation_type,
            reason=exc.reason,
            error_text=exc.error_text,
            screenshot_path=exc.screenshot_path,
            page_url=exc.page_url,
            trace_reference=exc.trace_reference,
            engine=exc.engine,
        )
        logger.warning("escalated task %s: %s", task_id, exc.reason)
    except Exception as exc:  # noqa: BLE001
        client.escalate(
            task_id,
            worker_id,
            escalation_type="unrecoverable_failure",
            reason=f"Unhandled worker exception: {exc}",
            error_text=str(exc),
            engine="playwright",
        )
        logger.exception("unhandled exception while executing %s", task_id)
    finally:
        stop_event.set()
        hb.join(timeout=2)


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle_signal)

    worker_id = os.getenv("HUNTER_WORKER_ID", "hosted-hva-worker-1")
    poll_interval = max(5, int(os.getenv("HUNTER_POLL_INTERVAL_SECONDS", "10")))
    client = HunterWorkerClient()
    logger.info("starting hosted HVA worker as %s", worker_id)
    try:
        while not _STOP:
            try:
                task = client.claim_task(worker_id)
                if not task:
                    time.sleep(poll_interval)
                    continue
                _process_task(client, worker_id, task)
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker loop error: %s", exc)
                time.sleep(poll_interval)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
