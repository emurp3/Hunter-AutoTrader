from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.database.config import create_db_and_tables
from app.routers.autotrader import router as autotrader_router
from app.routers.budget import router as budget_router
from app.routers.opportunities import router as opportunities_router
from app.routers.reports import router as reports_router
from app.routers.alerts import router as alerts_router
from app.routers.packets import router as packets_router
from app.routers.strategies import router as strategies_router
from app.routers.operations import router as operations_router
from app.routers.execution import router as execution_router
from app.routers.advisors import router as advisors_router
from app.routers.sources import router as sources_router
from app.routers.performance import router as performance_router
from app.routers.system import router as system_router
from app.routers.monitoring import router as monitoring_router
from app.routers.handoff import router as handoff_router
from app.routers.leads import router as leads_router
from app.routers.decisions import router as decisions_router
from app.routers.marketplace import router as marketplace_router
from app.routers.tasks import router as tasks_router
from app.routers.auth import router as auth_router
from app.routers.diag import router as diag_router
from app.services.scheduler import scheduler, daily_scan_task, weekly_report_task, recycle_cycle_task
from app.config import RECYCLE_CYCLE_INTERVAL_SECONDS, STRATEGY_MODE, ALPACA_ENABLED

# ── Paths ─────────────────────────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_FRONTEND_DIST = _BACKEND_DIR / "frontend_dist"

# ── Scheduler timezone — all jobs run at 08:00 America/New_York ──────────────
# First fire: Monday 2026-04-07 08:00 ET (next Monday from deploy).
# Subsequent fires: every day at 08:00 ET (daily scan) / every Monday (weekly report).
# misfire_grace_time=3600: if the app was briefly down at 8 AM it still fires
# within the hour rather than silently skipping.
_SCHEDULER_TZ = "America/New_York"


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    try:
        from app.database.config import engine
        from app.services.autotrader import bootstrap_intake

        with Session(engine) as session:
            bootstrap_intake(session)
    except Exception:
        pass
    scheduler.add_job(
        daily_scan_task,
        "cron",
        hour=8,
        minute=0,
        timezone=_SCHEDULER_TZ,
        id="daily_scan",
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        weekly_report_task,
        "cron",
        day_of_week="mon",
        hour=8,
        minute=0,
        timezone=_SCHEDULER_TZ,
        id="weekly_report",
        misfire_grace_time=3600,
    )
    # ── INTRADAY_RECYCLE cycle — runs every N seconds during market hours ─────
    # Sell-first → refresh → buy-after loop. Only active in RECYCLE mode.
    if ALPACA_ENABLED and STRATEGY_MODE == "RECYCLE":
        scheduler.add_job(
            recycle_cycle_task,
            "interval",
            seconds=RECYCLE_CYCLE_INTERVAL_SECONDS,
            id="recycle_cycle",
            max_instances=1,        # Never allow concurrent cycle runs
            misfire_grace_time=30,
        )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Hunter",
    description="Elite Liberation Agent — autonomous income operations engine",
    version="0.2.0",
    lifespan=lifespan,
)

# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(system_router)        # /system — health + readiness (first for priority)
app.include_router(opportunities_router)
app.include_router(reports_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(budget_router)
app.include_router(autotrader_router)
app.include_router(alerts_router)
app.include_router(packets_router)
app.include_router(strategies_router)
app.include_router(operations_router)
app.include_router(sources_router)
app.include_router(execution_router)
app.include_router(performance_router)
app.include_router(advisors_router)
app.include_router(monitoring_router)
app.include_router(handoff_router)
app.include_router(leads_router)
app.include_router(decisions_router)
app.include_router(marketplace_router)
app.include_router(tasks_router)
app.include_router(auth_router)
app.include_router(diag_router)

# ── Static file serving (production only) ────────────────────────────────────
# _FRONTEND_DIST only exists after build.sh runs (i.e. on Render).
# When absent, local dev uses the Vite dev server as before.
if _FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="assets",
    )
    app.mount(
        "/media",
        StaticFiles(directory=str(_FRONTEND_DIST / "media")),
        name="media",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """Catch-all: serve index.html for all unmatched paths (SPA client-side routing)."""
        return FileResponse(str(_FRONTEND_DIST / "index.html"))

else:
    @app.get("/")
    def root():
        return {"message": "Hunter v0.2.0 — autonomous operations engine running"}


# ── ASGI middleware: strip /api prefix ───────────────────────────────────────
# Must be the LAST thing in this module — wraps the entire app including
# lifespan. Rewrites /api/... → /... so the frontend's const API = '/api'
# works in production (same origin, no proxy) while all backend routes
# remain unchanged. Local dev uses the Vite proxy instead; this middleware
# is still present but harmless (direct curl hits /api/... and it strips).

class _StripApiPrefix:
    """
    Pure ASGI middleware. Strips the /api prefix from HTTP request paths.
    Passes lifespan and websocket scopes through unchanged.
    Both scope["path"] and scope["raw_path"] are rewritten for consistency.
    """

    __slots__ = ("_app",)

    def __init__(self, inner_app):
        self._app = inner_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path: str = scope.get("path", "")
            if path.startswith("/api"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                raw: bytes = scope.get("raw_path", b"")
                if raw.startswith(b"/api"):
                    scope["raw_path"] = raw[4:] or b"/"
        await self._app(scope, receive, send)


app = _StripApiPrefix(app)
