from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...config import settings
from ...database import Base, SessionLocal, engine
from .models import NewsEvent
from .sentiment import assess_news, fetch_rss_articles

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class NewsWorker:
    """Polling RSS worker with idempotent PostgreSQL event persistence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "running": False,
            "state": "STOPPED",
            "message": "News worker has not started.",
            "feed_count": 0,
            "last_run_at": None,
            "last_success_at": None,
            "last_article_count": 0,
            "last_stored_count": 0,
            "last_errors": [],
            "last_error": None,
        }

    @property
    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._status.update(
                running=True,
                state="STARTING",
                message="Background news worker started.",
            )
            self._thread = threading.Thread(
                target=self._loop,
                name="news-rss-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        with self._lock:
            self._status.update(
                running=False,
                state="STOPPED",
                message="Background news worker stopped.",
            )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.exception("Background news worker cycle failed")
                self._update(
                    state="ERROR",
                    last_error=type(exc).__name__,
                    message="News worker cycle failed; it will retry on the next interval.",
                )
            self._stop_event.wait(max(60, settings.news_poll_interval_seconds))
        self._update(running=False, state="STOPPED", message="Background news worker stopped.")

    def run_once(self) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {**self.status, "message": "A news ingestion cycle is already running."}
        try:
            urls = [
                value.strip()
                for value in settings.news_rss_feed_urls.split(",")
                if value.strip()
            ]
            now = datetime.now(IST)
            if not urls:
                result = {
                    "running": bool(self._thread and self._thread.is_alive()),
                    "state": "UNCONFIGURED",
                    "message": "No RSS feeds are configured; news coverage is unavailable.",
                    "feed_count": 0,
                    "last_run_at": now.isoformat(),
                    "last_article_count": 0,
                    "last_stored_count": 0,
                    "last_errors": [],
                    "last_error": None,
                }
                self._update(**result)
                return result

            Base.metadata.create_all(bind=engine)
            articles, errors = fetch_rss_articles(urls)
            assessment = assess_news(
                articles,
                as_of=now,
                max_article_age_minutes=settings.news_max_article_age_minutes,
            )
            assessed = {item.article_id: item for item in assessment.articles}
            stored_count = 0

            with SessionLocal() as session:
                for article in articles:
                    digest = hashlib.sha256(article.url.casefold().encode("utf-8")).hexdigest()[:16]
                    item = assessed.get(digest)
                    if item is None:
                        continue
                    row = session.get(NewsEvent, digest)
                    if row is None:
                        row = NewsEvent(article_id=digest)
                        session.add(row)
                        stored_count += 1
                    row.title = article.title
                    row.source = article.source
                    row.url = article.url
                    row.published_at = article.published_at
                    row.fetched_at = now
                    row.symbols = list(item.symbols)
                    row.summary = article.summary
                    row.sentiment_score = item.sentiment_score
                    row.sentiment_label = item.sentiment_label
                    row.event_severity = item.event_severity
                    row.source_trust = item.source_trust
                    row.event_level = (
                        "HIGH" if item.event_severity >= 3
                        else "MEDIUM" if item.event_severity == 2
                        else "LOW"
                    )
                    row.reasons = list(item.reasons)
                session.commit()

            state = (
                "UNAVAILABLE" if errors and not articles
                else "PARTIAL" if errors
                else "EMPTY" if not assessment.articles
                else "AVAILABLE"
            )
            result = {
                "running": bool(self._thread and self._thread.is_alive()),
                "state": state,
                "message": (
                    "News ingestion completed."
                    if state == "AVAILABLE"
                    else "News ingestion completed with feed errors."
                    if state == "PARTIAL"
                    else "All configured feeds failed; news coverage is unavailable."
                    if state == "UNAVAILABLE"
                    else "Feeds were reachable, but no fresh usable articles were found."
                ),
                "feed_count": len(urls),
                "last_run_at": now.isoformat(),
                "last_success_at": now.isoformat(),
                "last_article_count": len(assessment.articles),
                "last_stored_count": stored_count,
                "last_errors": errors,
                "last_error": None,
            }
            self._update(**result)
            return result
        except Exception as exc:
            self._update(
                state="ERROR",
                last_run_at=datetime.now(IST).isoformat(),
                last_error=type(exc).__name__,
                message="News ingestion failed; existing stored events were not deleted.",
            )
            raise
        finally:
            self._run_lock.release()

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)


news_worker = NewsWorker()
