from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response

from app.core.config import get_settings
from app.core.database import engine, ping_database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.migrations import database_is_at_head


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _master_key = settings.master_key
    settings.ensure_database_directory()
    settings.content_root.mkdir(parents=True, exist_ok=True)
    ping_database()
    if not database_is_at_head(engine):
        raise RuntimeError("database schema is not at the Alembic head; run migrations before startup")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    application = FastAPI(
        title="Yuque-Backup API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
    )

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    register_exception_handlers(application)

    from app.api.router import router

    application.include_router(router)
    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, workers=1)
