from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status

from app.api.dependencies import (
    AppSettings,
    CsrfAdmin,
    CurrentAdmin,
    DbSession,
    PublicWriteSecurity,
)
from app.api.openapi import (
    CSRF_OPENAPI_EXTRA,
    IDEMPOTENCY_OPENAPI_EXTRA,
    documented_responses,
)
from app.core.errors import AppError
from app.core.models import AdminSession
from app.core.security import clear_auth_cookies, set_auth_cookies
from app.modules.auth.schemas import (
    AdminResponse,
    InitializationStatus,
    InitializeRequest,
    LoginRequest,
    PasswordChangeRequest,
)
from app.modules.auth.service import (
    change_password,
    initialize_admin,
    is_initialized,
    login_admin,
    logout_admin,
)

router = APIRouter(tags=["authentication"])


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key 请求头")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求参数不合法",
            field_errors=[{"field": "Idempotency-Key", "reason": "uuid"}],
        ) from None


@router.get(
    "/api/v1/system/initialization",
    response_model=InitializationStatus,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(),
)
def initialization_status(db: DbSession) -> InitializationStatus:
    return InitializationStatus(initialized=is_initialized(db))


@router.post(
    "/api/v1/system/initialize",
    response_model=AdminResponse,
    status_code=status.HTTP_201_CREATED,
    responses=documented_responses(400, 403, 409, 422, success_status=201),
    openapi_extra=IDEMPOTENCY_OPENAPI_EXTRA,
)
def initialize(
    payload: InitializeRequest,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    _security: PublicWriteSecurity,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", include_in_schema=False),
    ] = None,
) -> AdminResponse:
    result = initialize_admin(
        db,
        username=payload.username,
        password=payload.password,
        idempotency_key=_idempotency_key(idempotency_key),
        settings=settings,
    )
    set_auth_cookies(
        response,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        settings=settings,
    )
    return AdminResponse.model_validate(result.admin)


@router.post(
    "/api/v1/auth/login",
    response_model=AdminResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 403, 422, 429),
)
def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    _security: PublicWriteSecurity,
) -> AdminResponse:
    source_ip = request.client.host if request.client is not None else "unknown"
    result = login_admin(
        db,
        username=payload.username,
        password=payload.password,
        source_ip=source_ip,
        settings=settings,
    )
    set_auth_cookies(
        response,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        settings=settings,
    )
    return AdminResponse.model_validate(result.admin)


@router.get(
    "/api/v1/auth/me",
    response_model=AdminResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401),
)
def current_admin(admin: CurrentAdmin) -> AdminResponse:
    return AdminResponse.model_validate(admin)


@router.post(
    "/api/v1/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=documented_responses(401, 403, 422, success_status=204),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def logout(
    request: Request,
    _admin: CsrfAdmin,
    db: DbSession,
    settings: AppSettings,
) -> Response:
    admin_session = getattr(request.state, "admin_session", None)
    if not isinstance(admin_session, AdminSession):
        raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")
    logout_admin(db, admin_session)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.put(
    "/api/v1/auth/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=documented_responses(400, 401, 403, 422, success_status=204),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def update_password(
    request: Request,
    payload: PasswordChangeRequest,
    admin: CsrfAdmin,
    db: DbSession,
) -> Response:
    admin_session = getattr(request.state, "admin_session", None)
    if not isinstance(admin_session, AdminSession):
        raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")
    change_password(
        db,
        admin=admin,
        current_session=admin_session,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
