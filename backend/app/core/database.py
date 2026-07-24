from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    settings.ensure_database_directory()
    database_url = settings.database_url
    if database_url is None:
        raise RuntimeError("DATABASE_URL was not initialized")
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False, "autocommit": False}
    engine = create_engine(database_url, **kwargs)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
            previous_autocommit = getattr(dbapi_connection, "autocommit", None)
            if previous_autocommit is not None:
                dbapi_connection.autocommit = True
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=FULL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()
                if previous_autocommit is not None:
                    dbapi_connection.autocommit = previous_autocommit

    return engine


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session]:
    with SessionLocal.begin() as session:
        yield session


def ping_database(*, require_write: bool = False) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        if require_write:
            connection.execute(text("UPDATE app_setting SET version = version WHERE id = 1"))
            connection.rollback()
