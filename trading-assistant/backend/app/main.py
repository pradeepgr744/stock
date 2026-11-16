from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.market_data import router as market_data_router
from .config import settings
from .database import database_status

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_data_router)


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
