from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx


class ChallengeClassification(str, Enum):
    PASSIVE_BADGE = "PASSIVE_BADGE"
    INTERACTIVE_SUPPORTED = "INTERACTIVE_SUPPORTED"
    INTERACTIVE_UNSUPPORTED = "INTERACTIVE_UNSUPPORTED"
    HUMAN_ONLY = "HUMAN_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass
class CaptchaChallenge:
    classification: ChallengeClassification
    family: str
    website_url: str
    site_key: str | None = None
    action: str | None = None


@dataclass
class CaptchaSolution:
    request_id: str
    token: str
    raw: dict[str, Any]


class CaptchaProvider(ABC):
    @abstractmethod
    def detect_supported_challenge(self, challenge: CaptchaChallenge) -> bool: ...
    @abstractmethod
    def submit_challenge(self, challenge: CaptchaChallenge) -> str: ...
    @abstractmethod
    def poll_result(self, request_id: str) -> dict[str, Any] | None: ...
    @abstractmethod
    def return_solution(self, result: dict[str, Any]) -> CaptchaSolution: ...
    @abstractmethod
    def report_bad_solution(self, request_id: str) -> None: ...
    @abstractmethod
    def provider_health(self, *, live: bool = False) -> dict[str, Any]: ...


class TwoCaptchaProvider(CaptchaProvider):
    """2Captcha API v2 adapter. The API key is never retained in receipts."""
    base_url = "https://api.2captcha.com"
    _last_e2e_success_at: str | None = None

    def __init__(self, api_key: str | None = None, *, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("CAPTCHA_API_KEY", "")
        self.client = client or httpx.Client(timeout=float(os.getenv("CAPTCHA_HTTP_TIMEOUT_SECONDS", "30")))
        self.sleep = sleep

    def detect_supported_challenge(self, challenge: CaptchaChallenge) -> bool:
        return challenge.classification == ChallengeClassification.INTERACTIVE_SUPPORTED and challenge.family in {"recaptcha_v2", "hcaptcha", "turnstile"} and bool(challenge.site_key)

    def _task(self, c: CaptchaChallenge) -> dict[str, Any]:
        types={"recaptcha_v2":"RecaptchaV2TaskProxyless","hcaptcha":"HCaptchaTaskProxyless","turnstile":"TurnstileTaskProxyless"}
        task={"type":types[c.family],"websiteURL":c.website_url,"websiteKey":c.site_key}
        if c.family=="turnstile" and c.action: task["action"]=c.action
        return task

    def submit_challenge(self, challenge: CaptchaChallenge) -> str:
        if not self.api_key: raise RuntimeError("NOT_CONFIGURED")
        response=self.client.post(f"{self.base_url}/createTask",json={"clientKey":self.api_key,"task":self._task(challenge)})
        response.raise_for_status(); data=response.json()
        if data.get("errorId"): raise RuntimeError(data.get("errorCode","PROVIDER_ERROR"))
        return str(data["taskId"])

    def poll_result(self, request_id: str) -> dict[str, Any] | None:
        response=self.client.post(f"{self.base_url}/getTaskResult",json={"clientKey":self.api_key,"taskId":int(request_id)})
        response.raise_for_status(); data=response.json()
        if data.get("errorId"): raise RuntimeError(data.get("errorCode","PROVIDER_ERROR"))
        return data if data.get("status")=="ready" else None

    def return_solution(self, result: dict[str, Any]) -> CaptchaSolution:
        solution=result.get("solution") or {}; token=solution.get("token") or solution.get("gRecaptchaResponse")
        if not token: raise RuntimeError("INVALID_PROVIDER_RESULT")
        return CaptchaSolution(str(result.get("taskId", "")), token, solution)

    def report_bad_solution(self, request_id: str) -> None:
        if self.api_key:
            self.client.post(f"{self.base_url}/reportIncorrect",json={"clientKey":self.api_key,"taskId":int(request_id)}).raise_for_status()

    def provider_health(self, *, live: bool = False) -> dict[str, Any]:
        enabled=os.getenv("CAPTCHA_SOLVER_ENABLED", "false").lower()=="true"
        status={"enabled":enabled,"provider":"2captcha","credentials":"configured" if self.api_key else "missing","api_reachable":None,"last_end_to_end_success":self._last_e2e_success_at,"operational":False}
        if not self.api_key:
            status["status"]="NOT_CONFIGURED"; return status
        status["status"]="CONFIGURED_NOT_LIVE_TESTED"
        if live:
            try:
                r=self.client.post(f"{self.base_url}/getBalance",json={"clientKey":self.api_key}); r.raise_for_status(); data=r.json()
                status["api_reachable"]=data.get("errorId")==0
            except Exception: status["api_reachable"]=False
        status["operational"]=bool(enabled and status["api_reachable"] and self._last_e2e_success_at)
        return status


def configured_provider() -> CaptchaProvider | None:
    if os.getenv("CAPTCHA_SOLVER_ENABLED", "false").lower()!="true": return None
    name=os.getenv("CAPTCHA_PROVIDER", "2captcha").lower()
    return TwoCaptchaProvider() if name in {"2captcha","two_captcha"} else None


def automation_verification_allowed(site: str, objective: dict[str, Any]) -> bool:
    if objective.get("automated_verification_allowed") is not True: return False
    host=urlsplit(site).hostname or ""
    allow={x.strip().lower() for x in os.getenv("CAPTCHA_ALLOWED_HOSTS", "").split(",") if x.strip()}
    return bool(host and (host in allow or objective.get("verification_policy")=="provider_allowed"))


def classify_page_challenge(page) -> CaptchaChallenge:
    data=page.evaluate("""() => { const q=(s)=>document.querySelector(s); const key=(e)=>e&&(e.dataset.sitekey||e.getAttribute('data-sitekey')); const r=q('.g-recaptcha,[data-sitekey]'); const h=q('.h-captcha'); const t=q('.cf-turnstile'); const b=q('iframe[src*=recaptcha][src*=bframe]'); return {recaptcha:key(r)||new URL(b?.src||location.href).searchParams.get('k'),hcaptcha:key(h),turnstile:key(t),action:t?.dataset.action,passive:!!document.querySelector('.grecaptcha-badge,iframe[src*=recaptcha][src*=anchor],script[src*=turnstile]'),humanOnly:!!document.querySelector('[data-human-verification-only=true],input[name*=otp],input[autocomplete=one-time-code]')}; }""")
    if data.get("humanOnly"): return CaptchaChallenge(ChallengeClassification.HUMAN_ONLY,"human_only",page.url)
    if data.get("recaptcha"): return CaptchaChallenge(ChallengeClassification.INTERACTIVE_SUPPORTED,"recaptcha_v2",page.url,data["recaptcha"])
    if data.get("hcaptcha"): return CaptchaChallenge(ChallengeClassification.INTERACTIVE_SUPPORTED,"hcaptcha",page.url,data["hcaptcha"])
    if data.get("turnstile"): return CaptchaChallenge(ChallengeClassification.INTERACTIVE_SUPPORTED,"turnstile",page.url,data["turnstile"],data.get("action"))
    if data.get("passive"): return CaptchaChallenge(ChallengeClassification.PASSIVE_BADGE,"passive_badge",page.url)
    return CaptchaChallenge(ChallengeClassification.INTERACTIVE_UNSUPPORTED,"unknown",page.url)


def apply_solution_to_browser(page, challenge: CaptchaChallenge, solution: CaptchaSolution) -> None:
    page.evaluate("""({family,token}) => { const names=family==='hcaptcha'?['h-captcha-response']:family==='turnstile'?['cf-turnstile-response']:['g-recaptcha-response']; for(const name of names){ let el=document.querySelector(`[name="${name}"]`); if(!el){el=document.createElement('textarea');el.name=name;el.style.display='none';document.body.appendChild(el);} el.value=token; el.innerHTML=token; el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true})); } if(family==='recaptcha_v2'&&window.___grecaptcha_cfg){ const walk=o=>{for(const v of Object.values(o||{})){if(typeof v==='function'){try{v(token)}catch(e){}}else if(v&&typeof v==='object')walk(v)}}; walk(window.___grecaptcha_cfg.clients); } }""", {"family":challenge.family,"token":solution.token})


def solve_in_browser(page, objective: dict[str, Any], *, provider: CaptchaProvider | None=None, max_attempts: int=2, max_polls: int=12, poll_seconds: float=5) -> dict[str, Any]:
    challenge=classify_page_challenge(page); receipt={"challenge_family":challenge.family,"solver_provider":os.getenv("CAPTCHA_PROVIDER","2captcha"),"solver_enabled":os.getenv("CAPTCHA_SOLVER_ENABLED","false").lower()=="true","solver_configured":bool(os.getenv("CAPTCHA_API_KEY")),"solver_attempt_count":0,"solver_request_id":None,"solver_result":"not_attempted","verification_accepted":False,"fallback_reason":None}
    if challenge.classification != ChallengeClassification.INTERACTIVE_SUPPORTED: receipt["fallback_reason"]="unsupported_challenge"; return receipt
    if not automation_verification_allowed(page.url,objective): receipt["fallback_reason"]="automation_disallowed"; return receipt
    provider=provider or configured_provider()
    if provider is None: receipt["fallback_reason"]="provider_not_configured"; return receipt
    receipt["solver_provider"]=provider.provider_health().get("provider","unknown"); receipt["solver_configured"]=provider.provider_health().get("credentials")=="configured"
    for attempt in range(1,max_attempts+1):
        receipt["solver_attempt_count"]=attempt
        try:
            request_id=provider.submit_challenge(challenge); receipt["solver_request_id"]=request_id
            result=None
            for _ in range(max_polls):
                result=provider.poll_result(request_id)
                if result: break
                if hasattr(provider,"sleep"): provider.sleep(poll_seconds)
            if not result: raise RuntimeError("POLL_TIMEOUT")
            result=dict(result); result["taskId"]=request_id
            solution=provider.return_solution(result); apply_solution_to_browser(page,challenge,solution); page.wait_for_timeout(1500)
            accepted=not any(page.locator(s).count()>0 and page.locator(s).first.is_visible() for s in ('iframe[src*="recaptcha"][src*="bframe"]','iframe[src*="hcaptcha"][title*="challenge"]','#captchaModal.show'))
            if accepted:
                receipt["solver_result"]="solved"; receipt["verification_accepted"]=True
                if isinstance(provider,TwoCaptchaProvider): provider._last_e2e_success_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
                return receipt
            provider.report_bad_solution(request_id); receipt["solver_result"]="rejected"
        except Exception as exc:
            receipt["solver_result"]="provider_failure"; receipt["fallback_reason"]=type(exc).__name__
    receipt["fallback_reason"]=receipt["fallback_reason"] or "provider_retries_exhausted"; return receipt
