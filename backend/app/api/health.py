from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.openapi import documented_responses
from app.api.schemas import LiveHealthResponse, ReadyHealthResponse
from app.core.config import get_settings
from app.core.database import engine, ping_database
from app.core.errors import AppError
from app.core.migrations import database_is_at_head

router = APIRouter(tags=["health"])


@router.get(
    "/health/live",
    response_model=LiveHealthResponse,
    status_code=200,
    responses=documented_responses(),
)
def live() -> LiveHealthResponse:
    return LiveHealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadyHealthResponse,
    status_code=200,
    responses=documented_responses(503),
)
def ready() -> ReadyHealthResponse | JSONResponse:
    settings = get_settings()
    try:
        ping_database(require_write=True)
        if not database_is_at_head(engine):
            raise RuntimeError("database migration is not at head")
        if not settings.content_root.is_dir():
            raise RuntimeError("content directory is unavailable")
        next(settings.content_root.iterdir(), None)
    except Exception as exc:
        raise AppError(503, "SERVICE_UNAVAILABLE", "服务尚未就绪") from exc
    return ReadyHealthResponse(status="ready")
