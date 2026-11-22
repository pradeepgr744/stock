from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ...config import settings
from ...database import Base, SessionLocal, engine
from .models import NewsEvent, NewsWorkerState
from .sentiment import assess_news, fetch_rss_articles

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
# A PostgreSQL session-level advisory lock makes polling single-leader across
# Render replicas and multiple Uvicorn workers. It is not an order/execution lock.
_NEWS_WORKER_LOCK_KEY = 724_202_611


class NewsWorker:
    """RSS poller with durable status, idempotent events and a DB leader lock."""

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
            "last_heartbeat_at": None,
            "last_article_count": 0,
            "last_stored_count": 0,
            "last_errors": [],
            "last_error": None,
            "assessment_state": "UNKNOWN",
            "halt_new_entries": True,
            "halt_all_symbols": True,
            "blocked_symbols": [],
        }

    @property
    def status(self) -> dict[str, Any]:
        # Read the shared row so dashboard/API replicas do not report stale
        # process-local status after a restart or when another replica is leader.
        try:
            with SessionLocal() as session:
                row = session.get(NewsWorkerState, 1)
                if row is not None and isinstance(row.payload, dict):
                    with self._lock:
                        self._status.update(row.payload)
                        return dict(self._status)
        except Exception:
            logger.warning("Could not read durable news worker status; treating coverage as unsafe", exc_info=True)
            with self._lock:
                snapshot = dict(self._status)
            snapshot["status_store_unavailable"] = True
            snapshot["halt_new_entries"] = True
            snapshot["halt_all_symbols"] = True
            return snapshot
        with self._lock:
            return dict(self._status)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            try:
                Base.metadata.create_all(bind=engine)
                with SessionLocal() as session:
                    row = session.get(NewsWorkerState, 1)
                    if row is not None and isinstance(row.payload, dict):
                        self._status.update(row.payload)
            except Exception as exc:
                logger.exception("Could not initialize durable news worker state")
                self._status.update(
                    state="ERROR",
                    last_error=type(exc).__name__,
                    halt_new_entries=True,
                    halt_all_symbols=True,
                    message="News status storage is unavailable; new signal alerts remain paused.",
                )
            self._stop_event.clear()
            self._status.update(
                running=True,
                state="STARTING",
                message="News worker started; waiting for the database leader lock.",
            )
            thread = threading.Thread(
                target=self._loop,
                name="news-rss-worker",
                daemon=True,
            )
            self._thread = thread
        self._persist_status()
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        # Do not overwrite the shared worker state when a non-leader API
        # replica shuts down; another replica may still be polling.
        with self._lock:
            self._status.update(
                running=False,
                state="STOPPED",
                halt_new_entries=True,
                halt_all_symbols=True,
                message="Background news worker stopped on this application instance.",
            )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.exception("Background news worker cycle failed")
                self._update(
                    running=True,
                    state="ERROR",
                    last_error=type(exc).__name__,
                    last_heartbeat_at=datetime.now(IST).isoformat(),
                    halt_new_entries=True,
                    halt_all_symbols=True,
                    message="News ingestion failed; new signal alerts are paused until coverage recovers.",
                )
            self._stop_event.wait(max(60, settings.news_poll_interval_seconds))
        # Local shutdown must not overwrite the durable state owned by another
        # Render/Uvicorn replica.
        with self._lock:
            self._status.update(
                running=False,
                state="STOPPED",
                halt_new_entries=True,
                halt_all_symbols=True,
                message="Background news worker stopped.",
            )

    def run_once(self) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {**self.status, "message": "A news ingestion cycle is already running."}

        lock_connection = None
        advisory_lock_acquired = False
        try:
            # The schema is created before checking feed configuration so the
            # durable UNCONFIGURED state is still visible to every replica.
            Base.metadata.create_all(bind=engine)
            lock_connection = engine.connect()
            if engine.dialect.name == "postgresql":
                advisory_lock_acquired = bool(
                    lock_connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": _NEWS_WORKER_LOCK_KEY},
                    ).scalar()
                )
                if not advisory_lock_acquired:
                    # Another API process is already polling. Do not overwrite
                    # its shared status with a local "skipped" state.
                    return {
                        **self.status,
                        "running": bool(self._thread and self._thread.is_alive()),
                        "leader": False,
                        "message": "Another application instance currently owns the news worker lock.",
                    }

            urls = [
                value.strip()
                for value in settings.news_rss_feed_urls.split(",")
                if value.strip()
            ]
            now = datetime.now(IST)
            if not urls:
                result = {
                    "running": bool(self._thread and self._thread.is_alive()),
                    "leader": True,
                    "state": "UNCONFIGURED",
                    "message": "No RSS feeds are configured; news coverage is unavailable.",
                    "feed_count": 0,
                    "last_run_at": now.isoformat(),
                    "last_heartbeat_at": now.isoformat(),
                    "last_article_count": 0,
                    "last_stored_count": 0,
                    "last_errors": [],
                    "last_error": None,
                    "assessment_state": "UNAVAILABLE",
                    "halt_new_entries": True,
                    "halt_all_symbols": True,
                    "blocked_symbols": [],
                }
                self._update(**result)
                return result

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
                    digest = hashlib.sha256(
                        article.url.casefold().encode("utf-8")
                    ).hexdigest()[:16]
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

            if errors and not assessment.articles:
                state = "UNAVAILABLE"
            elif errors:
                state = "PARTIAL"
            elif not assessment.articles:
                state = "EMPTY"
            else:
                state = "AVAILABLE"

            # Missing fresh articles or any failed configured feed means the
            # coverage is incomplete. Never let EMPTY/PARTIAL accidentally
            # become an entry-permitting state.
            coverage_block = bool(errors) or not assessment.articles
            result = {
                "running": bool(self._thread and self._thread.is_alive()),
                "leader": True,
                "state": state,
                "message": (
                    "News ingestion completed."
                    if state == "AVAILABLE"
                    else "Some configured feeds failed; news coverage is incomplete."
                    if state == "PARTIAL"
                    else "All configured feeds failed; news coverage is unavailable."
                    if state == "UNAVAILABLE"
                    else "Feeds returned no fresh usable articles; new entries remain paused."
                ),
                "feed_count": len(urls),
                "last_run_at": now.isoformat(),
                "last_heartbeat_at": now.isoformat(),
                "last_success_at": now.isoformat() if not errors else self.status.get("last_success_at"),
                "last_article_count": len(assessment.articles),
                "last_stored_count": stored_count,
                "last_errors": errors,
                "last_error": None if not errors else "One or more RSS feeds failed.",
                "assessment_state": assessment.state.value,
                "halt_new_entries": assessment.halt_new_entries or coverage_block,
                "halt_all_symbols": assessment.halt_all_symbols or coverage_block,
                "blocked_symbols": list(assessment.blocked_symbols),
            }
            self._update(**result)
            return result
        except Exception as exc:
            self._update(
                running=bool(self._thread and self._thread.is_alive()),
                state="ERROR",
                last_run_at=datetime.now(IST).isoformat(),
                last_heartbeat_at=datetime.now(IST).isoformat(),
                last_error=type(exc).__name__,
                halt_new_entries=True,
                halt_all_symbols=True,
                message="News ingestion failed; new signal alerts are paused until coverage recovers.",
            )
            raise
        finally:
            if lock_connection is not None:
                try:
                    if advisory_lock_acquired:
                        lock_connection.execute(
                            text("SELECT pg_advisory_unlock(:lock_key)"),
                            {"lock_key": _NEWS_WORKER_LOCK_KEY},
                        )
                except Exception:
                    logger.exception("Could not release news worker advisory lock")
                lock_connection.close()
            self._run_lock.release()

    def _persist_status(self) -> None:
        try:
            with SessionLocal() as session:
                row = session.get(NewsWorkerState, 1)
                if row is None:
                    row = NewsWorkerState(id=1, payload={}, updated_at=datetime.now(IST))
                    session.add(row)
                with self._lock:
                    row.payload = dict(self._status)
                row.updated_at = datetime.now(IST)
                session.commit()
        except Exception:
            # Status persistence failure must not turn a feed failure into a
            # false healthy state. The in-memory gate remains fail-closed.
            logger.exception("Could not persist news worker status")

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)
        self._persist_status()


news_worker = NewsWorker()
