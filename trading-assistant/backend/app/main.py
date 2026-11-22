from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.market_data import router as market_data_router
from .api.research import router as research_router
from .api.news_events import router as news_events_router
from .api.risk import router as risk_router
from .config import settings
from .database import database_status
from .modules.news.worker import news_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    news_worker.start()
    try:
        yield
    finally:
        news_worker.stop()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_data_router)
app.include_router(research_router)
app.include_router(news_events_router)
app.include_router(risk_router)


@app.get("/health")
def health_check() -> dict:
    return {
        "status": "ok",
        "service": "trading-assistant-backend",
        "environment": settings.environment,
        "version": settings.app_version,
    }


@app.get("/api/health")
def api_health() -> JSONResponse:
    database = database_status()
    overall_status = "ok" if database["status"] == "connected" else "degraded"

    return JSONResponse(
        status_code=200,
        content={
            "status": overall_status,
            "service": "backend",
            "environment": settings.environment,
            "database": database,
        },
    )


@app.get("/api/hello")
def hello() -> dict:
    return {"message": "Trading Assistant backend is ready for Phase 1."}
