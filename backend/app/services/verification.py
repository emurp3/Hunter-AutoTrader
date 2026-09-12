from __future__ import annotations
import json
from typing import Any
from urllib.parse import urlsplit
from sqlmodel import Session, select
from app.models.task import Task, TaskStatus, VerificationReceipt
ACTIVE="ACTIVE"; RETRYING="RETRYING"; REROUTING="REROUTING"
AWAITING_HUMAN_VERIFICATION="AWAITING_HUMAN_VERIFICATION"; RESUMING="RESUMING"; COMPLETED="COMPLETED"
FAILED_ONLY_AFTER_ALL_VALID_PATHS_EXHAUSTED="FAILED_ONLY_AFTER_ALL_VALID_PATHS_EXHAUSTED"
HUMAN_VERIFICATION_TAG="[HUMAN-VERIFICATION-REQUIRED]"
def record_receipt(session: Session, task: Task, *, url: str, challenge_type: str, real: bool, attempted_paths: list[str], state: str, required_human_action: str|None=None, checkpoint: dict[str, Any]|None=None, solver: dict[str, Any]|None=None) -> VerificationReceipt:
    solver=solver or {}
    receipt=VerificationReceipt(objective_id=task.source_id, task_id=task.task_id, target_site=urlsplit(url).netloc, url=url, challenge_type=challenge_type, challenge_reality="real" if real else "passive", attempted_recovery_paths=json.dumps(attempted_paths), current_state=state, required_human_action=required_human_action, resume_checkpoint=json.dumps(checkpoint or {}), solver_provider=solver.get("solver_provider"), solver_enabled=solver.get("solver_enabled",False), solver_configured=solver.get("solver_configured",False), challenge_family=solver.get("challenge_family"), solver_request_id=solver.get("solver_request_id"), solver_attempt_count=solver.get("solver_attempt_count",0), solver_result=solver.get("solver_result"), verification_accepted=solver.get("verification_accepted",False), fallback_reason=solver.get("fallback_reason"))
    session.add(receipt); return receipt
def await_human(session: Session, task_id: str, *, url: str, challenge_type: str, attempted_paths: list[str], required_human_action: str, checkpoint: dict[str, Any]) -> Task:
    task=session.exec(select(Task).where(Task.task_id==task_id)).first()
    if not task: raise ValueError(f"Task not found: {task_id}")
    task.status=TaskStatus.awaiting_human_verification; task.verification_state=AWAITING_HUMAN_VERIFICATION; task.verification_attempts += 1
    task.resume_checkpoint_json=json.dumps(checkpoint); task.worker_id=None; task.lease_expires_at=None
    record_receipt(session, task, url=url, challenge_type=challenge_type, real=True, attempted_paths=attempted_paths, state=AWAITING_HUMAN_VERIFICATION, required_human_action=required_human_action, checkpoint=checkpoint)
    session.add(task)
    if task.source_type=="canonical_opportunity" and task.source_id:
        from app.models.hunter_ledger import Disposition
        from app.services import execution_accounting as acct
        message=(f"{HUMAN_VERIFICATION_TAG} Verification checkpoint encountered. Objective remains active. I preserved progress at {url}. Perform only this step: {required_human_action} Reply when complete; Hunter will resume immediately afterward.")
        acct.set_disposition(session, task.source_id, Disposition.pending_commander, new_checkpoint=message)
    session.commit(); session.refresh(task); return task
def resume(task: Task, session: Session) -> Task:
    checkpoint=json.loads(task.resume_checkpoint_json or "{}")
    task.status=TaskStatus.resuming; task.verification_state=RESUMING
    record_receipt(session, task, url=checkpoint.get("url",""), challenge_type="verification_completed", real=True, attempted_paths=["human verification completed"], state=RESUMING, checkpoint=checkpoint)
    task.status=TaskStatus.retrying; task.worker_id=None; task.lease_expires_at=None
    session.add(task); session.commit(); session.refresh(task); return task

def recovery_plan(failure_count: int, alternate_official_urls: list[str] | None = None) -> list[str]:
    """Bounded order: retry twice, then change route; never invent portal navigation."""
    plan=[]
    if failure_count < 2:
        plan.extend(["retry normal supported browser flow", "reload/reopen current step"])
    for url in (alternate_official_urls or []):
        if url.startswith("https://"):
            plan.append(f"reroute official:{url}")
    plan.append("check configured approved CAPTCHA integration")
    plan.append("await minimum human verification")
    return plan
