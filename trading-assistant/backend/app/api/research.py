from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from ..config import settings
from ..modules.news.sentiment import assess_news, fetch_rss_articles

router = APIRouter(prefix="/api/research", tags=["research"])
IST = ZoneInfo("Asia/Kolkata")


class NewsAssessmentRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=200)
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return value


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
    coverage_block = not urls or (bool(errors) and not articles)
    reasons = list(assessment.reasons)
    if not urls:
        reasons.append("No official news feeds configured; news coverage is unavailable")
    elif errors and not articles:
        reasons.append("All configured news feeds failed; news coverage is unavailable")
    return {
        "status": "unconfigured" if not urls else "unavailable" if coverage_block else "partial" if errors else "ok",
        "coverage_status": "UNAVAILABLE" if coverage_block else "PARTIAL" if errors else "AVAILABLE",
        "feed_count": len(urls),
        "article_count": len(assessment.articles),
        "state": "BLOCKED" if coverage_block else assessment.state.value,
        "halt_new_entries": assessment.halt_new_entries or coverage_block,
        "halt_all_symbols": assessment.halt_all_symbols or coverage_block,
        "blocked_symbols": list(assessment.blocked_symbols),
        "sentiment_score": assessment.sentiment_score,
        "reasons": reasons,
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
