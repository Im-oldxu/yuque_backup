from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: list[dict[str, str]] | None = None,
        retry_after_seconds: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors
        self.retry_after_seconds = retry_after_seconds
        self.headers = dict(headers or {})


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    request_id = f"req_{uuid.uuid4().hex}"
    request.state.request_id = request_id
    return request_id


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    field_errors: list[dict[str, str]] | None = None,
    retry_after_seconds: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    content: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if field_errors:
        content["field_errors"] = field_errors
    if retry_after_seconds is not None:
        content["retry_after_seconds"] = retry_after_seconds

    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    if retry_after_seconds is not None:
        response_headers.setdefault("Retry-After", str(retry_after_seconds))
    return JSONResponse(status_code=status_code, content=content, headers=response_headers)


def _validation_field_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    reason_aliases = {
        "string_too_short": "min_length",
        "string_too_long": "max_length",
        "greater_than_equal": "minimum",
        "less_than_equal": "maximum",
    }
    field_errors: list[dict[str, str]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ())]
        if location and location[0] in {"body", "query", "path", "header", "cookie"}:
            location = location[1:]
        reason = str(error.get("type", "invalid"))
        field_errors.append(
            {
                "field": ".".join(location) or "request",
                "reason": reason_aliases.get(reason, reason),
            }
        )
    return field_errors


def _http_error_details(status_code: int) -> tuple[str, str]:
    errors = {
        400: ("VALIDATION_ERROR", "请求不合法"),
        401: ("AUTH_REQUIRED", "需要有效的管理员会话"),
        403: ("CSRF_INVALID", "CSRF 或同源校验失败"),
        404: ("RESOURCE_NOT_FOUND", "请求的资源不存在"),
        409: ("CONFLICT", "当前状态不允许此操作"),
        410: ("RESOURCE_GONE", "请求的内容已被清理"),
        422: ("VALIDATION_ERROR", "请求参数不合法"),
        503: ("SERVICE_UNAVAILABLE", "服务暂不可用"),
    }
    return errors.get(status_code, ("HTTP_ERROR", "请求处理失败"))


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        field_errors=exc.field_errors,
        retry_after_seconds=exc.retry_after_seconds,
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="请求参数不合法",
        field_errors=_validation_field_errors(exc),
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _http_error_details(exc.status_code)
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=exc.headers,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled server error",
        extra={"request_id": _request_id(request), "exception_type": type(exc).__name__},
    )
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="服务器内部错误",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
