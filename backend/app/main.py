from contextlib import asynccontextmanager
from fastapi import FastAPI

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
from app.services.scheduler import scheduler, daily_scan_task, weekly_report_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    scheduler.add_job(daily_scan_task, "interval", days=1, id="daily_scan")
    scheduler.add_job(weekly_report_task, "interval", weeks=1, id="weekly_report")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Hunter",
    description="Elite Liberation Agent — autonomous income operations engine",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(opportunities_router)
app.include_router(reports_router)
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


@app.get("/")
def root():
    return {"message": "Hunter v0.2.0 — autonomous operations engine running"}
