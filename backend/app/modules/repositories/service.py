from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.api.schemas import utc_datetime
from app.core.errors import AppError
from app.core.models import Document, Repository, RepositoryCredential, TocItem, YuqueCredential
from app.modules.repositories.schemas import (
    RepositoryCredentialSummary,
    RepositoryDetailResponse,
    RepositoryResponse,
    TocNode,
    TocTree,
)


def get_repository(db: Session, repository_id: str) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise AppError(404, "REPOSITORY_NOT_FOUND", "知识库不存在")
    return repository


def _credential_rows(
    db: Session,
    repository_id: str,
) -> list[tuple[RepositoryCredential, YuqueCredential]]:
    return list(
        db.execute(
            select(RepositoryCredential, YuqueCredential)
            .join(YuqueCredential, YuqueCredential.id == RepositoryCredential.credential_id)
            .where(
                RepositoryCredential.repository_id == repository_id,
                YuqueCredential.deleted_at.is_(None),
            )
            .order_by(YuqueCredential.created_at.asc(), YuqueCredential.id.asc())
        ).tuples()
    )


def _repository_response(
    repository: Repository,
    rows: list[tuple[RepositoryCredential, YuqueCredential]],
    document_count: int,
) -> RepositoryResponse:
    primary = next(((relation, credential) for relation, credential in rows if relation.is_primary), None)
    primary_id = primary[1].id if primary else None
    if primary is None:
        connection_status = "disabled"
    else:
        credential = primary[1]
        if credential.status == "action_required" or not credential.verification_valid:
            connection_status = "action_required"
        elif not credential.enabled or credential.status == "disabled":
            connection_status = "disabled"
        else:
            connection_status = "connected"
    return RepositoryResponse(
        id=repository.id,
        yuque_book_id=repository.yuque_book_id,
        base_url=repository.normalized_base_url,
        name=repository.name,
        slug=repository.slug,
        namespace=repository.namespace,
        selected=repository.selected,
        connection_status=connection_status,
        primary_credential_id=primary_id,
        credential_count=len(rows),
        document_count=document_count,
        last_success_at=utc_datetime(repository.last_success_at),
        content_updated_at=utc_datetime(repository.content_updated_at),
    )


def serialize_repository(
    db: Session,
    repository: Repository,
) -> RepositoryResponse:
    rows = _credential_rows(db, repository.id)
    document_count = (
        db.scalar(select(func.count(Document.id)).where(Document.repository_id == repository.id)) or 0
    )
    return _repository_response(repository, rows, document_count)


def serialize_repository_detail(
    db: Session,
    repository: Repository,
) -> RepositoryDetailResponse:
    rows = _credential_rows(db, repository.id)
    document_count = (
        db.scalar(select(func.count(Document.id)).where(Document.repository_id == repository.id)) or 0
    )
    response = _repository_response(repository, rows, document_count)
    return RepositoryDetailResponse(
        **response.model_dump(),
        credentials=[
            RepositoryCredentialSummary(
                id=credential.id,
                name=credential.name,
                status=credential.status,
                enabled=credential.enabled,
            )
            for _, credential in rows
        ],
    )


def serialize_repositories(
    db: Session,
    repositories: Sequence[Repository],
) -> list[RepositoryResponse]:
    if not repositories:
        return []
    repository_ids = [repository.id for repository in repositories]
    rows_by_repository: dict[str, list[tuple[RepositoryCredential, YuqueCredential]]] = defaultdict(
        list
    )
    rows = db.execute(
        select(RepositoryCredential, YuqueCredential)
        .join(YuqueCredential, YuqueCredential.id == RepositoryCredential.credential_id)
        .where(
            RepositoryCredential.repository_id.in_(repository_ids),
            YuqueCredential.deleted_at.is_(None),
        )
        .order_by(
            RepositoryCredential.repository_id.asc(),
            YuqueCredential.created_at.asc(),
            YuqueCredential.id.asc(),
        )
    ).tuples()
    for relation, credential in rows:
        rows_by_repository[relation.repository_id].append((relation, credential))

    document_counts = {
        repository_id: count
        for repository_id, count in db.execute(
            select(Document.repository_id, func.count(Document.id))
            .where(Document.repository_id.in_(repository_ids))
            .group_by(Document.repository_id)
        )
    }
    return [
        _repository_response(
            repository,
            rows_by_repository[repository.id],
            document_counts.get(repository.id, 0),
        )
        for repository in repositories
    ]


def connection_status_condition(
    connection_status: Literal["connected", "disabled", "action_required"],
) -> ColumnElement[bool]:
    primary_credential = (
        select(RepositoryCredential.id)
        .join(YuqueCredential, YuqueCredential.id == RepositoryCredential.credential_id)
        .where(
            RepositoryCredential.repository_id == Repository.id,
            RepositoryCredential.is_primary.is_(True),
            YuqueCredential.deleted_at.is_(None),
        )
    )
    action_required = primary_credential.where(
        or_(
            YuqueCredential.status == "action_required",
            YuqueCredential.verification_valid.is_(False),
        )
    ).exists()
    connected = primary_credential.where(
        YuqueCredential.status != "action_required",
        YuqueCredential.verification_valid.is_(True),
        YuqueCredential.enabled.is_(True),
        YuqueCredential.status != "disabled",
    ).exists()
    if connection_status == "action_required":
        return action_required
    if connection_status == "connected":
        return connected
    return and_(~action_required, ~connected)


def build_toc_tree(db: Session, repository: Repository) -> TocTree:
    items = db.scalars(
        select(TocItem)
        .where(TocItem.repository_id == repository.id)
        .order_by(TocItem.order_index.asc(), TocItem.id.asc())
    ).all()
    documents = {
        document.yuque_doc_id: document.id
        for document in db.scalars(select(Document).where(Document.repository_id == repository.id)).all()
    }
    children_by_parent: dict[str | None, list[TocItem]] = defaultdict(list)
    remote_ids = {item.remote_id for item in items}
    for item in items:
        parent = item.parent_remote_id if item.parent_remote_id in remote_ids else None
        children_by_parent[parent].append(item)

    visiting: set[str] = set()

    def build(item: TocItem) -> TocNode:
        if item.remote_id in visiting:
            return TocNode(
                id=item.id,
                type=item.type,
                title=item.title,
                document_id=documents.get(item.yuque_doc_id or ""),
                path=item.path,
                children=[],
            )
        visiting.add(item.remote_id)
        node = TocNode(
            id=item.id,
            type=item.type,
            title=item.title,
            document_id=documents.get(item.yuque_doc_id or ""),
            path=item.path,
            children=[build(child) for child in children_by_parent.get(item.remote_id, [])],
        )
        visiting.remove(item.remote_id)
        return node

    updated_at = repository.toc_updated_at or repository.updated_at or repository.created_at
    return TocTree(
        repository_id=repository.id,
        updated_at=utc_datetime(updated_at),
        items=[build(item) for item in children_by_parent.get(None, [])],
    )


def escaped_contains(column: InstrumentedAttribute[str | None], value: str) -> ColumnElement[bool]:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")
