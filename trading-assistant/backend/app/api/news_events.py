from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc

from ..database import SessionLocal
from ..modules.news.models import NewsEvent
from ..modules.news.worker import news_worker

router = APIRouter(prefix="/api/research/news", tags=["news-events"])


@router.get("/worker/status")
def get_news_worker_status() -> dict:
    return news_worker.status


@router.post("/worker/run-once")
def run_news_worker_once() -> dict:
    try:
        return news_worker.run_once()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"News ingestion failed: {type(exc).__name__}",
        ) from exc


@router.get("/events")
def list_news_events(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    try:
        with SessionLocal() as session:
            rows = (
                session.query(NewsEvent)
                .order_by(desc(NewsEvent.published_at))
                .limit(limit)
                .all()
            )
            return [
                {
                    "article_id": row.article_id,
                    "title": row.title,
                    "source": row.source,
                    "url": row.url,
                    "published_at": row.published_at,
                    "fetched_at": row.fetched_at,
                    "symbols": row.symbols,
                    "summary": row.summary,
                    "sentiment_score": row.sentiment_score,
                    "sentiment_label": row.sentiment_label,
                    "event_severity": row.event_severity,
                    "source_trust": row.source_trust,
                    "event_level": row.event_level,
                    "reasons": row.reasons,
                }
                for row in rows
            ]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not read persisted news events: {type(exc).__name__}",
        ) from exc
