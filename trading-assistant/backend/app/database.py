from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings


def _redact_password(database_url: str) -> str:
    if "://" not in database_url:
        return database_url

    scheme, remainder = database_url.split("://", 1)
    if "@" not in remainder:
        return database_url

    credentials, host = remainder.rsplit("@", 1)
    if ":" in credentials:
        username, _ = credentials.split(":", 1)
        return f"{scheme}://{username}:***@{host}"

    return f"{scheme}://{credentials}:***@{host}"


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_status() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "connected",
            "dialect": engine.dialect.name,
            "database_url": _redact_password(settings.database_url),
        }
    except Exception as exc:  # pragma: no cover - runtime health only
        return {
            "status": "disconnected",
            "database_url": _redact_password(settings.database_url),
            "error": str(exc),
        }
