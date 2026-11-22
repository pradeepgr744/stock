from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class NewsEvent(Base):
    __tablename__ = "news_events"

    article_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sentiment_label: Mapped[str] = mapped_column(String(32), nullable=False, default="NEUTRAL_OR_UNCLEAR")
    event_severity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_trust: Mapped[str] = mapped_column(String(32), nullable=False, default="UNVERIFIED")
    event_level: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class NewsWorkerState(Base):
    """Single-row durable worker status shared by all API instances."""

    __tablename__ = "news_worker_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
