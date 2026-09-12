import json
import httpx
from app.worker.captcha_provider import (CaptchaProvider, CaptchaSolution, ChallengeClassification,
    TwoCaptchaProvider, solve_in_browser)

class Loc:
    def __init__(self,page): self.page=page; self.first=self
    def count(self): return 1 if self.page.challenge else 0
    def is_visible(self): return self.page.challenge
class Page:
    url="https://allowed.gov/form"
    def __init__(self,family="recaptcha", passive=False): self.family=family; self.passive=passive; self.challenge=not passive; self.injected=None
    def evaluate(self,script,arg=None):
        if arg is None:
            return {"recaptcha":"site-key" if self.family=="recaptcha" and self.challenge else None,"hcaptcha":"site-key" if self.family=="hcaptcha" and self.challenge else None,"turnstile":"site-key" if self.family=="turnstile" and self.challenge else None,"action":None,"passive":self.passive,"humanOnly":False}
        self.injected=arg["token"]; self.challenge=False
    def wait_for_timeout(self,ms): pass
    def locator(self,s): return Loc(self)

class FakeProvider(CaptchaProvider):
    def __init__(self,results=None, outage=False): self.submits=0; self.polls=0; self.bad=0; self.results=results or [{"status":"ready","solution":{"token":"solved-token"}}]; self.outage=outage
    def detect_supported_challenge(self,c): return True
    def submit_challenge(self,c):
        self.submits+=1
        if self.outage: raise httpx.ConnectError("down")
        return str(self.submits)
    def poll_result(self,r): self.polls+=1; return self.results[min(self.submits-1,len(self.results)-1)]
    def return_solution(self,result):
        token=result.get("solution",{}).get("token")
        if not token: raise RuntimeError("invalid")
        return CaptchaSolution(str(result.get("taskId")),token,result)
    def report_bad_solution(self,r): self.bad+=1
    def provider_health(self,live=False): return {"provider":"fixture","credentials":"configured"}

def allowed(): return {"automated_verification_allowed":True,"verification_policy":"provider_allowed"}

def test_supported_real_challenge_calls_provider_and_submits():
    p=FakeProvider(); receipt=solve_in_browser(Page(),allowed(),provider=p,poll_seconds=0)
    assert p.submits==1 and receipt["solver_request_id"]=="1"

def test_provider_result_reaches_browser_and_is_accepted():
    page=Page(); receipt=solve_in_browser(page,allowed(),provider=FakeProvider(),poll_seconds=0)
    assert page.injected=="solved-token" and receipt["verification_accepted"] is True

def test_success_continues_original_task():
    page=Page(); continued=False
    if solve_in_browser(page,allowed(),provider=FakeProvider(),poll_seconds=0)["verification_accepted"]: continued=True
    assert continued

def test_invalid_result_has_bounded_retry():
    p=FakeProvider(results=[{"status":"ready","solution":{}}]); r=solve_in_browser(Page(),allowed(),provider=p,max_attempts=2,poll_seconds=0)
    assert p.submits==2 and not r["verification_accepted"]

def test_provider_outage_falls_back_without_terminal_failure():
    r=solve_in_browser(Page(),allowed(),provider=FakeProvider(outage=True),max_attempts=2,poll_seconds=0)
    assert r["fallback_reason"]=="ConnectError" and r["solver_attempt_count"]==2

def test_missing_credentials_reports_not_configured(monkeypatch):
    monkeypatch.delenv("CAPTCHA_API_KEY",raising=False)
    assert TwoCaptchaProvider().provider_health()["status"]=="NOT_CONFIGURED"

def test_passive_badge_never_calls_solver():
    p=FakeProvider(); r=solve_in_browser(Page(passive=True),allowed(),provider=p)
    assert p.submits==0 and r["fallback_reason"]=="unsupported_challenge"

def test_automation_disallowed_never_calls_solver():
    p=FakeProvider(); r=solve_in_browser(Page(),{},provider=p)
    assert p.submits==0 and r["fallback_reason"]=="automation_disallowed"

def test_api_adapter_actually_posts_create_and_poll(monkeypatch):
    seen=[]
    def handler(req):
        seen.append((req.url.path,json.loads(req.content)))
        return httpx.Response(200,json={"errorId":0,"taskId":77} if req.url.path.endswith("createTask") else {"errorId":0,"status":"ready","solution":{"token":"x"}})
    client=httpx.Client(transport=httpx.MockTransport(handler)); p=TwoCaptchaProvider("secret",client=client,sleep=lambda _:None)
    from app.worker.captcha_provider import CaptchaChallenge
    c=CaptchaChallenge(ChallengeClassification.INTERACTIVE_SUPPORTED,"recaptcha_v2","https://allowed.gov/form","key")
    rid=p.submit_challenge(c); result=p.poll_result(rid)
    assert [x[0] for x in seen]==["/createTask","/getTaskResult"] and result["status"]=="ready"

def test_secrets_never_enter_solver_receipt(monkeypatch):
    monkeypatch.setenv("CAPTCHA_API_KEY","top-secret-key")
    r=solve_in_browser(Page(),allowed(),provider=FakeProvider(),poll_seconds=0)
    assert "top-secret-key" not in json.dumps(r) and "api_key" not in r

# Controlled integration fixture: detector -> provider submission/poll -> token injection -> page acceptance -> task continuation.
def test_integration_complete_browser_provider_chain():
    page=Page("turnstile"); provider=FakeProvider(); original_task={"continued":False}
    receipt=solve_in_browser(page,allowed(),provider=provider,poll_seconds=0)
    if receipt["verification_accepted"]: original_task["continued"]=True
    assert provider.submits==1 and provider.polls==1 and page.injected=="solved-token" and original_task["continued"]


def test_integration_controlled_provider_full_chain(monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("createTask"):
            return httpx.Response(200,json={"errorId":0,"taskId":991})
        return httpx.Response(200,json={"errorId":0,"status":"ready","solution":{"token":"fixture-e2e-token"}})
    provider=TwoCaptchaProvider("fixture-secret",client=httpx.Client(transport=httpx.MockTransport(handler)),sleep=lambda _:None)
    page=Page()
    receipt=solve_in_browser(page,allowed(),provider=provider,poll_seconds=0)
    assert calls==["/createTask","/getTaskResult"]
    assert page.injected=="fixture-e2e-token"
    assert receipt["verification_accepted"] is True
    assert receipt["solver_result"]=="solved"
