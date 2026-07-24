from __future__ import annotations

from typing import Any

from app.api.schemas import ErrorResponse

ResponseSpec = dict[int | str, dict[str, Any]]

REQUEST_ID_HEADER: dict[str, Any] = {
    "description": "与响应体 request_id 一致的请求追踪标识。",
    "schema": {"type": "string"},
}

_ERROR_DESCRIPTIONS = {
    400: "请求在业务上无效, 或缺少必需的幂等键。",
    401: "需要有效的管理员会话。",
    403: "CSRF 或同源校验失败。",
    404: "请求的本地资源不存在。",
    409: "资源状态、重复操作或幂等性冲突。",
    410: "记录存在, 但内容已按保留策略清理。",
    422: "请求字段、查询参数、Cron、时区或 URL 校验失败。",
    429: "登录请求受到本地速率限制。",
    500: "未预期的服务端错误。",
    503: "数据库、迁移或内容存储不可用。",
}


def documented_responses(
    *error_statuses: int,
    success_status: int = 200,
    success_description: str = "Successful Response",
    success_content: dict[str, Any] | None = None,
    success_headers: dict[str, Any] | None = None,
) -> ResponseSpec:
    headers = {"X-Request-ID": REQUEST_ID_HEADER, **(success_headers or {})}
    success: dict[str, Any] = {
        "description": success_description,
        "headers": headers,
    }
    if success_content is not None:
        success["content"] = success_content

    responses: ResponseSpec = {success_status: success}
    for status_code in dict.fromkeys((*error_statuses, 500)):
        responses[status_code] = {
            "model": ErrorResponse,
            "description": _ERROR_DESCRIPTIONS[status_code],
            "headers": {"X-Request-ID": REQUEST_ID_HEADER},
        }
    return responses


def _required_header(name: str, *, uuid_format: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if uuid_format:
        schema["format"] = "uuid"
    return {
        "name": name,
        "in": "header",
        "required": True,
        "schema": schema,
    }


CSRF_OPENAPI_EXTRA: dict[str, Any] = {
    "parameters": [_required_header("X-CSRF-Token")],
}

IDEMPOTENCY_OPENAPI_EXTRA: dict[str, Any] = {
    "parameters": [_required_header("Idempotency-Key", uuid_format=True)],
}

CSRF_IDEMPOTENCY_OPENAPI_EXTRA: dict[str, Any] = {
    "parameters": [
        _required_header("X-CSRF-Token"),
        _required_header("Idempotency-Key", uuid_format=True),
    ],
}


def binary_content(*media_types: str) -> dict[str, Any]:
    return {
        media_type: {"schema": {"type": "string", "format": "binary"}}
        for media_type in media_types
    }
