from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import settings
from ..modules.news.sentiment import assess_news, fetch_rss_articles

router = APIRouter(prefix="/api/research", tags=["research"])
IST = ZoneInfo("Asia/Kolkata")


class NewsAssessmentRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=200)
    as_of: datetime | None = None


@router.post("/news/assess")
def assess_configured_news(request: NewsAssessmentRequest) -> dict:
    """Fetch operator-configured RSS feeds and assess fresh intraday news."""
    urls = [
        value.strip()
        for value in settings.news_rss_feed_urls.split(",")
        if value.strip()
    ]
    articles, errors = fetch_rss_articles(urls)
    assessment = assess_news(
        articles,
        as_of=request.as_of or datetime.now(IST),
        relevant_symbols=request.symbols,
    )
    return {
        "status": "partial" if errors else "ok",
        "feed_count": len(urls),
        "article_count": len(assessment.articles),
        "state": assessment.state.value,
        "halt_new_entries": assessment.halt_new_entries,
        "sentiment_score": assessment.sentiment_score,
        "reasons": list(assessment.reasons),
        "articles": [
            {
                "article_id": item.article_id,
                "title": item.title,
                "source": item.source,
                "symbols": list(item.symbols),
                "age_minutes": item.age_minutes,
                "sentiment_score": item.sentiment_score,
                "sentiment_label": item.sentiment_label,
                "event_severity": item.event_severity,
                "source_trust": item.source_trust,
                "reasons": list(item.reasons),
            }
            for item in assessment.articles
        ],
        "feed_errors": errors,
        "limitations": [
            "Only configured RSS/Atom feeds are checked; this is not a complete real-time news terminal.",
            "Sentiment is a transparent keyword baseline, not an ML prediction or calibrated probability.",
            "A BLOCKED result pauses new entries only; it does not liquidate existing positions.",
            "Feed publication timestamps and symbol tags may be incomplete or delayed.",
        ],
    }
