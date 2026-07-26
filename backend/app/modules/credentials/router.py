from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from app.api.dependencies import CsrfAdmin, CurrentAdmin, DbSession
from app.api.openapi import CSRF_OPENAPI_EXTRA, documented_responses
from app.api.schemas import CredentialStatus, Page, PageParams
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.models import Operation, YuqueCredential, utcnow
from app.core.security import encrypt_token, mask_token
from app.modules.credentials.schemas import (
    CredentialCreate,
    CredentialCreated,
    CredentialPatch,
    CredentialResponse,
    OperationResponse,
)
from app.modules.credentials.service import (
    cancel_credential_work,
    commit_or_conflict,
    create_credential,
    enqueue_operation,
    get_credential,
    normalize_base_url,
    serialize_credential,
    serialize_operation,
)

router = APIRouter(prefix="/api/v1", tags=["credentials"])


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


@router.get(
    "/operations/{operation_id}",
    response_model=OperationResponse,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def get_operation(
    operation_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> OperationResponse:
    operation = db.get(Operation, str(operation_id))
    if operation is None:
        raise AppError(404, "OPERATION_NOT_FOUND", "异步操作不存在")
    return serialize_operation(operation)


@router.get(
    "/credentials",
    response_model=Page[CredentialResponse],
    status_code=200,
    responses=documented_responses(401, 422),
)
def list_credentials(
    db: DbSession,
    _admin: CurrentAdmin,
    pagination: Annotated[PageParams, Depends(page_params)],
    status: CredentialStatus | None = None,
    enabled: bool | None = None,
) -> Page[CredentialResponse]:
    filters: list[ColumnElement[bool]] = [YuqueCredential.deleted_at.is_(None)]
    if status is not None:
        filters.append(YuqueCredential.status == status)
    if enabled is not None:
        filters.append(YuqueCredential.enabled.is_(enabled))
    total = db.scalar(select(func.count(YuqueCredential.id)).where(*filters)) or 0
    credentials = db.scalars(
        select(YuqueCredential)
        .where(*filters)
        .order_by(YuqueCredential.created_at.asc(), YuqueCredential.id.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return Page(
        items=[serialize_credential(db, credential) for credential in credentials],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )


@router.post(
    "/credentials",
    response_model=CredentialCreated,
    status_code=202,
    responses=documented_responses(401, 403, 409, 422, success_status=202),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def add_credential(payload: CredentialCreate, db: DbSession, _admin: CsrfAdmin) -> CredentialCreated:
    credential, operation = create_credential(
        db,
        name=payload.name,
        base_url=payload.base_url,
        token=payload.token,
        settings=get_settings(),
    )
    commit_or_conflict(db)
    return CredentialCreated(
        credential=serialize_credential(db, credential),
        operation=serialize_operation(operation),
    )


@router.get(
    "/credentials/{credential_id}",
    response_model=CredentialResponse,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def credential_detail(
    credential_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> CredentialResponse:
    return serialize_credential(db, get_credential(db, str(credential_id)))


@router.patch(
    "/credentials/{credential_id}",
    response_model=CredentialResponse,
    status_code=200,
    responses=documented_responses(401, 403, 404, 409, 422),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def update_credential(
    credential_id: uuid.UUID,
    payload: CredentialPatch,
    db: DbSession,
    _admin: CsrfAdmin,
) -> CredentialResponse:
    credential = get_credential(db, str(credential_id))
    if "name" in payload.model_fields_set and payload.name is not None:
        duplicate = db.scalar(
            select(YuqueCredential.id).where(
                YuqueCredential.name == payload.name,
                YuqueCredential.id != credential.id,
                YuqueCredential.deleted_at.is_(None),
            )
        )
        if duplicate:
            raise AppError(409, "CREDENTIAL_NAME_EXISTS", "凭据名称已存在")
        credential.name = payload.name

    security_changed = False
    if "base_url" in payload.model_fields_set and payload.base_url is not None:
        normalized = normalize_base_url(payload.base_url)
        if normalized != credential.base_url:
            credential.base_url = normalized
            security_changed = True
    if "token" in payload.model_fields_set and payload.token is not None:
        encrypted, nonce = encrypt_token(payload.token, credential.id, get_settings())
        credential.encrypted_token = encrypted
        credential.token_nonce = nonce
        credential.token_suffix = mask_token(payload.token)[12:]
        credential.key_version = 1
        security_changed = True
    if security_changed:
        cancel_credential_work(db, credential.id)
        credential.subject_type = "unknown"
        credential.subject_id = None
        credential.login = None
        credential.status = "unverified"
        credential.verification_valid = False
        credential.enabled = False
        credential.last_verified_at = None
        credential.next_retry_at = None
        credential.last_error_code = None
    credential.updated_at = utcnow()
    commit_or_conflict(db)
    return serialize_credential(db, credential)


@router.post(
    "/credentials/{credential_id}/verify",
    response_model=OperationResponse,
    status_code=202,
    responses=documented_responses(401, 403, 404, 409, 422, success_status=202),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def verify_credential(
    credential_id: uuid.UUID,
    db: DbSession,
    _admin: CsrfAdmin,
) -> OperationResponse:
    credential = get_credential(db, str(credential_id))
    operation = enqueue_operation(db, credential, "credential_verify", wake_waiting=True)
    commit_or_conflict(db)
    return serialize_operation(operation)


@router.post(
    "/credentials/{credential_id}/discover-repositories",
    response_model=OperationResponse,
    status_code=202,
    responses=documented_responses(401, 403, 404, 409, 422, success_status=202),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def discover_repositories(
    credential_id: uuid.UUID,
    db: DbSession,
    _admin: CsrfAdmin,
) -> OperationResponse:
    credential = get_credential(db, str(credential_id))
    if not credential.verification_valid or credential.status not in {"valid", "waiting_quota"}:
        raise AppError(409, "CREDENTIAL_NOT_VALID", "凭据尚未通过验证")
    operation = enqueue_operation(db, credential, "repository_discovery")
    commit_or_conflict(db)
    return serialize_operation(operation)


@router.post(
    "/credentials/{credential_id}/enable",
    response_model=CredentialResponse,
    status_code=200,
    responses=documented_responses(401, 403, 404, 409, 422),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def enable_credential(
    credential_id: uuid.UUID,
    db: DbSession,
    _admin: CsrfAdmin,
) -> CredentialResponse:
    credential = get_credential(db, str(credential_id))
    if credential.status != "valid" or not credential.verification_valid:
        raise AppError(409, "CREDENTIAL_NOT_VALID", "凭据尚未通过验证")
    credential.enabled = True
    credential.status = "valid"
    credential.pause_reason = None
    credential.next_retry_at = None
    credential.updated_at = utcnow()
    db.commit()
    return serialize_credential(db, credential)


@router.post(
    "/credentials/{credential_id}/disable",
    response_model=CredentialResponse,
    status_code=200,
    responses=documented_responses(401, 403, 404, 422),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def disable_credential(
    credential_id: uuid.UUID,
    db: DbSession,
    _admin: CsrfAdmin,
) -> CredentialResponse:
    credential = get_credential(db, str(credential_id))
    cancel_credential_work(db, credential.id)
    credential.enabled = False
    credential.status = "disabled"
    credential.next_retry_at = None
    credential.updated_at = utcnow()
    db.commit()
    return serialize_credential(db, credential)


@router.delete(
    "/credentials/{credential_id}",
    status_code=204,
    response_class=Response,
    responses=documented_responses(401, 403, 404, 422, success_status=204),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def delete_credential(
    credential_id: uuid.UUID,
    db: DbSession,
    _admin: CsrfAdmin,
) -> Response:
    credential = get_credential(db, str(credential_id))
    cancel_credential_work(db, credential.id)
    credential.enabled = False
    credential.status = "disabled"
    credential.deleted_at = utcnow()
    credential.updated_at = credential.deleted_at
    db.commit()
    return Response(status_code=204)
