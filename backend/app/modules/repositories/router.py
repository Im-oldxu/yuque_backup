from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CsrfAdmin, CurrentAdmin, DbSession
from app.api.openapi import CSRF_OPENAPI_EXTRA, documented_responses
from app.api.schemas import Page
from app.core.errors import AppError
from app.core.models import Repository, RepositoryCredential, YuqueCredential, utcnow
from app.modules.repositories.schemas import (
    PrimaryCredentialRequest,
    RepositoryDetailResponse,
    RepositoryResponse,
    RepositorySelection,
    TocTree,
)
from app.modules.repositories.service import (
    build_toc_tree,
    connection_status_condition,
    escaped_contains,
    get_repository,
    serialize_repositories,
    serialize_repository,
    serialize_repository_detail,
)

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])


@router.get(
    "",
    response_model=Page[RepositoryResponse],
    status_code=200,
    responses=documented_responses(401, 422),
)
def list_repositories(
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: str | None = None,
    selected: bool | None = None,
    connection_status: Literal["connected", "disabled", "action_required"] | None = None,
    credential_id: uuid.UUID | None = None,
) -> Page[RepositoryResponse]:
    statement = select(Repository)
    if credential_id is not None:
        statement = statement.where(
            select(RepositoryCredential.id)
            .where(
                RepositoryCredential.repository_id == Repository.id,
                RepositoryCredential.credential_id == str(credential_id),
            )
            .exists()
        )
    if q:
        statement = statement.where(
            or_(escaped_contains(Repository.name, q), escaped_contains(Repository.namespace, q))
        )
    if selected is not None:
        statement = statement.where(Repository.selected.is_(selected))
    if connection_status is not None:
        statement = statement.where(connection_status_condition(connection_status))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    offset = (page - 1) * page_size
    repositories = db.scalars(
        statement
        .order_by(Repository.name.asc(), Repository.id.asc())
        .offset(offset)
        .limit(page_size)
    ).all()
    return Page(
        items=serialize_repositories(db, repositories),
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{repository_id}",
    response_model=RepositoryDetailResponse,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def repository_detail(
    repository_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> RepositoryDetailResponse:
    return serialize_repository_detail(db, get_repository(db, str(repository_id)))


@router.patch(
    "/{repository_id}/selection",
    response_model=RepositoryResponse,
    status_code=200,
    responses=documented_responses(401, 403, 404, 422),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def update_selection(
    repository_id: uuid.UUID,
    payload: RepositorySelection,
    db: DbSession,
    _admin: CsrfAdmin,
) -> RepositoryResponse:
    repository = get_repository(db, str(repository_id))
    repository.selected = payload.selected
    repository.updated_at = utcnow()
    db.commit()
    return serialize_repository(db, repository)


@router.put(
    "/{repository_id}/primary-credential",
    response_model=RepositoryResponse,
    status_code=200,
    responses=documented_responses(401, 403, 404, 409, 422),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def set_primary_credential(
    repository_id: uuid.UUID,
    payload: PrimaryCredentialRequest,
    db: DbSession,
    _admin: CsrfAdmin,
) -> RepositoryResponse:
    repository = get_repository(db, str(repository_id))
    credential_id = str(payload.credential_id)
    credential = db.scalar(
        select(YuqueCredential).where(
            YuqueCredential.id == credential_id,
            YuqueCredential.deleted_at.is_(None),
        )
    )
    if credential is None:
        raise AppError(404, "CREDENTIAL_NOT_FOUND", "语雀凭据不存在")
    relation = db.scalar(
        select(RepositoryCredential).where(
            RepositoryCredential.repository_id == repository.id,
            RepositoryCredential.credential_id == credential.id,
        )
    )
    if relation is None:
        raise AppError(
            409,
            "CREDENTIAL_CANNOT_ACCESS_REPOSITORY",
            "该凭据不能访问此知识库",
        )
    try:
        db.execute(
            update(RepositoryCredential)
            .where(
                RepositoryCredential.repository_id == repository.id,
                RepositoryCredential.id != relation.id,
                RepositoryCredential.is_primary.is_(True),
            )
            .values(is_primary=False)
        )
        db.flush()
        relation.is_primary = True
        repository.updated_at = utcnow()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409,
            "CREDENTIAL_CANNOT_ACCESS_REPOSITORY",
            "主凭据切换发生并发冲突, 请重试",
        ) from exc
    return serialize_repository(db, repository)


@router.get(
    "/{repository_id}/toc",
    response_model=TocTree,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def repository_toc(
    repository_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> TocTree:
    return build_toc_tree(db, get_repository(db, str(repository_id)))
