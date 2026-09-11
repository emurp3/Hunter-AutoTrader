from __future__ import annotations

import logging
import os
import signal
import subprocess
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

# Commander, 2026-09-11: "verify that an external success followed by
# exhausted callback retries cannot cause duplicate execution."
# HunterWorkerClient's terminal callbacks already retry a transient
# failure (the confirmed real case — a brief 502 during the web
# service's own redeploy). This covers what retry alone cannot: if
# EVERY retry is exhausted (a longer outage) after execute_task()
# already did something irreversible (e.g. sent a real email),
# stale-lease reclaim would otherwise hand the SAME task back to
# claim_task() with no memory that it already ran — re-executing it
# from scratch. Caching the outcome in-process, keyed by task_id, means
# a reclaim landing on THIS SAME worker process re-reports the already-
# decided outcome instead of redoing the work. Bounded so it can never
# grow unboundedly across a long-running process; does not survive a
# worker restart, which is a materially narrower and much rarer window
# than the confirmed failure this closes.
_reported_outcome_cache: dict[str, dict[str, Any]] = {}
_MAX_CACHED_OUTCOMES = 500


def _cache_outcome(task_id: str, kwargs: dict[str, Any]) -> None:
    if task_id in _reported_outcome_cache:
        del _reported_outcome_cache[task_id]
    elif len(_reported_outcome_cache) >= _MAX_CACHED_OUTCOMES:
        oldest = next(iter(_reported_outcome_cache))
        del _reported_outcome_cache[oldest]
    _reported_outcome_cache[task_id] = kwargs


def _ensure_playwright_browsers_installed() -> None:
    """The build step only installs the regular Chromium binary
    (`playwright install chromium`), but Playwright's default headless
    launch requires a separate "headless shell" binary that isn't
    fetched by that command — this service has no persistent disk, so
    the gap reappears on every deploy/restart. Install it here, once
    per boot, before the worker starts claiming tasks. A failure here
    must not crash the worker: task execution already fails safely
    (escalates) if the browser is genuinely still missing."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "chromium-headless-shell"],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        logger.info("playwright browser install check complete")
    except Exception as exc:  # noqa: BLE001
        logger.warning("playwright browser install check failed — %s: %s", type(exc).__name__, exc)


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

    cached = _reported_outcome_cache.get(task_id)
    if cached is not None:
        # This exact worker process already ran this task and knows its
        # real outcome — a claim landing here again means the earlier
        # /complete report never got through, not that the work is
        # undone. Re-report it; never re-run execute_task, which would
        # repeat whatever external action already happened.
        logger.warning(
            "task %s (%s) reclaimed but already executed in this process — "
            "re-reporting cached outcome instead of re-running it",
            task_id, task_type,
        )
        try:
            client.complete(task_id, worker_id, **cached)
            del _reported_outcome_cache[task_id]
            logger.info("completed task %s (from cache)", task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to re-report cached outcome for %s: %s", task_id, exc)
        return

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
    else:
        # execute_task() already succeeded — it may have done something
        # irreversible (e.g. sent a real email). Only the report of that
        # outcome can still fail from here, and a failure here must
        # never be treated as if the task itself failed (the generic
        # except above would misrepresent a real success as an
        # "unhandled exception").
        complete_kwargs = dict(
            outcome=result.outcome,
            notes=result.notes,
            screenshot_path=result.screenshot_path,
            page_url=result.page_url,
            trace_reference=result.trace_reference,
            engine=result.engine,
        )
        _cache_outcome(task_id, complete_kwargs)
        # Commander, 2026-09-11: the in-process cache above only survives
        # a reclaim landing on THIS SAME worker process — a restart loses
        # it. Record the same outcome durably on the server BEFORE
        # attempting /complete, so a reclaim after a lost process (crash,
        # restart, deploy) finalizes from the real recorded outcome
        # instead of claim_task() handing this task to a new worker for
        # re-execution. Best-effort: record_pending_outcome already
        # retries internally; if it's still exhausted, /complete is tried
        # anyway and the in-process cache remains the fallback for a
        # same-process reclaim.
        try:
            client.record_pending_outcome(task_id, worker_id, **complete_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "task %s: could not durably record pending outcome before "
                "reporting completion — %s",
                task_id, exc,
            )
        try:
            client.complete(task_id, worker_id, **complete_kwargs)
            del _reported_outcome_cache[task_id]
            logger.info("completed task %s", task_id)
        except Exception as exc:  # noqa: BLE001
            logger.critical(
                "task %s completed a real action but could not report it after "
                "retries — cached in-process for re-report on next reclaim: %s",
                task_id, exc,
            )
    finally:
        stop_event.set()
        hb.join(timeout=2)


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle_signal)

    worker_id = os.getenv("HUNTER_WORKER_ID", "hosted-hva-worker-1")
    poll_interval = max(5, int(os.getenv("HUNTER_POLL_INTERVAL_SECONDS", "10")))
    _ensure_playwright_browsers_installed()
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
