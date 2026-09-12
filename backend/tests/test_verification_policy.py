from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from app.models.task import Task, TaskStatus, VerificationReceipt
from app.services.verification import (ACTIVE, AWAITING_HUMAN_VERIFICATION, await_human,
    record_receipt, recovery_plan, resume)
from app.worker.executors import _has_anti_bot_challenge, _has_passive_verification_marker, _dismiss_claimant_login_if_possible
class _Locator:
    def __init__(self,count=0,visible=False,text=""):
        self._count=count; self._visible=visible; self._text=text; self.clicked=False; self.first=self
    def count(self): return self._count
    def is_visible(self): return self._visible
    def inner_text(self): return self._text
    def click(self): self.clicked=True
class _Page:
    def __init__(self,mapping,body_text=""):
        self.mapping=mapping; self.body=_Locator(1,True,body_text); self.waited=0
    def locator(self,selector):
        if selector=="body": return self.body
        for marker,loc in self.mapping.items():
            if marker in selector: return loc
        return _Locator()
    def wait_for_timeout(self,ms): self.waited=ms

def engine():
    e=create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e); return e

def task(s):
    t=Task(task_type="government_portal_search", source_type="test", source_id="objective-1", spec_payload="{}")
    s.add(t); s.commit(); s.refresh(t); return t

def test_a_passive_badge_continues_and_receipts_active():
    page=_Page({"grecaptcha-badge":_Locator(count=1,visible=True)})
    assert _has_passive_verification_marker(page) and not _has_anti_bot_challenge(page)
    with Session(engine()) as s:
        t=task(s); record_receipt(s,t,url="https://official.gov/form",challenge_type="passive_captcha_badge",real=False,attempted_paths=["normal flow continued"],state=ACTIVE); s.commit()
        r=s.exec(select(VerificationReceipt)).one(); assert r.challenge_reality=="passive" and r.current_state==ACTIVE

def test_b_real_interactive_challenge_enters_recovery_path():
    assert _has_anti_bot_challenge(_Page({"bframe":_Locator(count=1,visible=True)}))
    assert recovery_plan(0)[0].startswith("retry")

def test_c_human_verification_preserves_same_task_and_checkpoint():
    with Session(engine()) as s:
        t=task(s); await_human(s,t.task_id,url="https://official.gov/form",challenge_type="captcha",attempted_paths=recovery_plan(2),required_human_action="check the box",checkpoint={"url":"https://official.gov/form","stage":"submit"})
        row=s.get(Task,t.id); assert row.status==TaskStatus.awaiting_human_verification and row.resume_checkpoint_json

def test_d_verification_completion_resumes_same_task_automatically():
    with Session(engine()) as s:
        t=task(s); await_human(s,t.task_id,url="https://official.gov/form",challenge_type="captcha",attempted_paths=[],required_human_action="verify",checkpoint={"url":"https://official.gov/form"})
        resumed=resume(s.get(Task,t.id),s); assert resumed.id==t.id and resumed.status==TaskStatus.retrying and resumed.verification_state=="RESUMING"

def test_e_failed_site_reroutes_to_configured_official_path():
    plan=recovery_plan(2,["https://agency.gov/alternate"]); assert "reroute official:https://agency.gov/alternate" in plan

def test_f_repeated_selector_failure_changes_strategy_and_is_bounded():
    assert recovery_plan(0)[:2] != recovery_plan(2)[:2]
    assert sum("retry normal" in x for x in recovery_plan(2))==0

def test_g_unexpected_login_uses_only_public_cancel():
    cancel=_Locator(count=1,visible=True); page=_Page({"username":_Locator(count=1,visible=True),"password":_Locator(count=1,visible=True),"Cancel":cancel})
    assert _dismiss_claimant_login_if_possible(page) and cancel.clicked

