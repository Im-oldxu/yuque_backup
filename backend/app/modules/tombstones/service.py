from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.models import DeletionTombstone, Repository
from app.modules.settings.service import as_utc
from app.modules.tombstones.schemas import (
    TombstonePageResponse,
    TombstoneRepositoryResponse,
    TombstoneResponse,
)


def _validated_datetime(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求参数不合法",
            field_errors=[{"field": field, "reason": "timezone_required"}],
        )
    return value.astimezone(UTC)


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _response(tombstone: DeletionTombstone, repository: Repository) -> TombstoneResponse:
    return TombstoneResponse(
        id=tombstone.id,
        base_url=tombstone.base_url,
        yuque_book_id=tombstone.yuque_book_id,
        yuque_doc_id=tombstone.yuque_doc_id,
        title=tombstone.title,
        original_path=tombstone.original_path,
        repository=TombstoneRepositoryResponse(id=repository.id, name=repository.name),
        deleted_at=as_utc(tombstone.deleted_at),
        purged_at=as_utc(tombstone.purged_at),
        source_job_id=tombstone.source_job_id,
        cleanup_job_id=tombstone.cleanup_job_id,
    )


def list_tombstones(
    db: Session,
    *,
    page: int,
    page_size: int,
    q: str | None = None,
    repository_id: str | None = None,
    deleted_from: datetime | None = None,
    deleted_to: datetime | None = None,
) -> TombstonePageResponse:
    deleted_from = _validated_datetime(deleted_from, "deleted_from")
    deleted_to = _validated_datetime(deleted_to, "deleted_to")
    if deleted_from is not None and deleted_to is not None and deleted_from > deleted_to:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求参数不合法",
            field_errors=[{"field": "deleted_from", "reason": "range"}],
        )

    filters = []
    if q is not None and q.strip():
        pattern = f"%{_escaped_like(q.strip())}%"
        filters.append(
            or_(
                DeletionTombstone.title.ilike(pattern, escape="\\"),
                DeletionTombstone.original_path.ilike(pattern, escape="\\"),
            )
        )
    if repository_id is not None:
        filters.append(DeletionTombstone.repository_id == repository_id)
    if deleted_from is not None:
        filters.append(DeletionTombstone.deleted_at >= deleted_from)
    if deleted_to is not None:
        filters.append(DeletionTombstone.deleted_at <= deleted_to)

    total = int(db.scalar(select(func.count(DeletionTombstone.id)).where(*filters)) or 0)
    rows = db.execute(
        select(DeletionTombstone, Repository)
        .join(Repository, Repository.id == DeletionTombstone.repository_id)
        .where(*filters)
        .order_by(DeletionTombstone.deleted_at.desc(), DeletionTombstone.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TombstonePageResponse(
        items=[_response(tombstone, repository) for tombstone, repository in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


def get_tombstone(db: Session, tombstone_id: str) -> TombstoneResponse:
    row = db.execute(
        select(DeletionTombstone, Repository)
        .join(Repository, Repository.id == DeletionTombstone.repository_id)
        .where(DeletionTombstone.id == tombstone_id)
    ).one_or_none()
    if row is None:
        raise AppError(404, "TOMBSTONE_NOT_FOUND", "删除墓碑不存在")
    tombstone, repository = row
    return _response(tombstone, repository)
