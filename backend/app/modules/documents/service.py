from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.api.schemas import utc_datetime
from app.core.config import Settings
from app.core.errors import AppError
from app.core.models import (
    Asset,
    BackupIssue,
    Document,
    DocumentVersion,
    Repository,
    RetentionPolicy,
    VersionAsset,
)
from app.modules.documents.schemas import (
    AssetReferenceResponse,
    AssetSummary,
    BackupIssueResponse,
    DocumentDetail,
    DocumentSummary,
    DownloadAvailability,
    RepositorySummary,
    VersionDetail,
    VersionSummary,
)

INLINE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"})


def get_document(db: Session, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在")
    return document


def get_version(db: Session, document_id: str, version_id: str) -> DocumentVersion:
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document_id,
        )
    )
    if version is None:
        raise AppError(404, "VERSION_NOT_FOUND", "文档版本不存在")
    return version


def get_asset(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise AppError(404, "ASSET_NOT_FOUND", "资源不存在")
    return asset


def _retention_days(db: Session) -> int:
    policy = db.get(RetentionPolicy, 1)
    return policy.retention_days if policy is not None else 15


def _purge_at(db: Session, document: Document) -> datetime | None:
    deleted_at = utc_datetime(document.deleted_at)
    return deleted_at + timedelta(days=_retention_days(db)) if deleted_at else None


def serialize_version(version: DocumentVersion, document: Document) -> VersionSummary:
    preview_available = bool(
        version.completeness != "failed" and version.preview_path and version.purged_at is None
    )
    return VersionSummary(
        id=version.id,
        remote_version_id=version.remote_version_id,
        format=version.format,
        content_hash=version.content_hash,
        completeness=version.completeness,
        is_latest=document.latest_successful_version_id == version.id,
        preview_available=preview_available,
        resource_total=version.resource_total,
        resource_downloaded=version.resource_downloaded,
        issue_count=version.issue_count,
        source_job_id=version.source_job_id,
        remote_updated_at=utc_datetime(version.remote_updated_at),
        created_at=utc_datetime(version.created_at),
    )


def serialize_document(db: Session, document: Document) -> DocumentSummary:
    latest = (
        db.get(DocumentVersion, document.latest_successful_version_id)
        if document.latest_successful_version_id
        else None
    )
    return DocumentSummary(
        id=document.id,
        repository_id=document.repository_id,
        yuque_doc_id=document.yuque_doc_id,
        type=document.type
        if document.type in {"Doc", "Sheet", "Thread", "Board", "Table", "HtmlDoc"}
        else "unknown",
        title=document.title,
        slug=document.slug,
        path=document.path,
        deleted_at=utc_datetime(document.deleted_at),
        purge_at=_purge_at(db, document),
        latest_version_id=latest.id if latest else None,
        latest_version_completeness=latest.completeness if latest else None,
        updated_at=utc_datetime(document.updated_at),
    )


def serialize_document_detail(db: Session, document: Document) -> DocumentDetail:
    summary = serialize_document(db, document)
    repository = db.get(Repository, document.repository_id)
    if repository is None:
        raise AppError(503, "SERVICE_UNAVAILABLE", "文档索引不完整")
    latest = (
        db.get(DocumentVersion, document.latest_successful_version_id)
        if document.latest_successful_version_id
        else None
    )
    remaining = None
    purge_at = _purge_at(db, document)
    if purge_at is not None:
        remaining = max(0, int((purge_at - datetime.now(UTC)).total_seconds()))
    version_count = (
        db.scalar(select(func.count(DocumentVersion.id)).where(DocumentVersion.document_id == document.id))
        or 0
    )
    return DocumentDetail(
        **summary.model_dump(),
        repository=RepositorySummary(id=repository.id, name=repository.name, namespace=repository.namespace),
        original_path=document.original_path,
        remaining_retention_seconds=remaining,
        latest_successful_version=serialize_version(latest, document) if latest else None,
        version_count=version_count,
    )


def serialize_version_detail(db: Session, version: DocumentVersion, document: Document) -> VersionDetail:
    counts: dict[str, int] = {
        status: count
        for status, count in db.execute(
            select(VersionAsset.status, func.count(VersionAsset.id))
            .where(VersionAsset.version_id == version.id)
            .group_by(VersionAsset.status)
        ).tuples()
    }
    summary = serialize_version(version, document)
    return VersionDetail(
        **summary.model_dump(),
        document_id=document.id,
        downloads=DownloadAvailability(
            raw_response=bool(version.raw_response_path and version.purged_at is None),
            raw_body=bool(version.raw_body_path and version.purged_at is None),
            markdown=bool(version.purged_at is None),
            offline_html=bool(version.preview_path and version.purged_at is None),
            pdf=bool(version.purged_at is None),
        ),
        asset_summary=AssetSummary(
            total=sum(counts.values()),
            downloaded=counts.get("downloaded", 0),
            failed=counts.get("failed", 0),
            skipped=counts.get("skipped", 0),
        ),
        metadata=version.normalized_metadata,
    )


def serialize_asset_reference(reference: VersionAsset, asset: Asset | None) -> AssetReferenceResponse:
    available = bool(
        asset and asset.storage_path and asset.purged_at is None and reference.status == "downloaded"
    )
    mime_type = asset.mime_type if asset else reference.mime_type
    return AssetReferenceResponse(
        asset_id=asset.id if asset else None,
        name=reference.name,
        type=reference.type,
        mime_type=mime_type,
        size=asset.size if asset else reference.declared_size,
        status=reference.status,
        inline_available=bool(available and mime_type in INLINE_MIME_TYPES),
        download_available=available,
        issue_code=reference.issue_code,
    )


def serialize_issue(issue: BackupIssue) -> BackupIssueResponse:
    return BackupIssueResponse(
        id=issue.id,
        level=issue.level,
        code=issue.code,
        message=issue.message,
        credential_id=issue.credential_id,
        repository_id=issue.repository_id,
        document_id=issue.document_id,
        document_title=issue.document_title,
        asset_id=issue.asset_id,
        asset_type=issue.asset_type,
        safe_url=issue.safe_url,
        http_status=issue.http_status,
        attempt_count=issue.attempt_count,
        first_occurred_at=utc_datetime(issue.first_occurred_at),
        last_occurred_at=utc_datetime(issue.last_occurred_at),
    )


def resolve_content_path(settings: Settings, relative_path: str | None, *, purged: bool) -> Path:
    if purged or not relative_path:
        raise AppError(410, "VERSION_CONTENT_PURGED", "版本内容已按保留策略清理")
    candidate = (settings.data_root / relative_path).resolve()
    root = settings.content_root.resolve()
    if not candidate.is_relative_to(root):
        raise AppError(503, "SERVICE_UNAVAILABLE", "内容存储不可用")
    if not candidate.is_file():
        raise AppError(503, "SERVICE_UNAVAILABLE", "内容存储不可用")
    return candidate


def resolve_asset_path(settings: Settings, asset: Asset) -> Path:
    if asset.purged_at is not None or not asset.storage_path:
        raise AppError(410, "ASSET_CONTENT_PURGED", "资源内容已按保留策略清理")
    candidate = (settings.data_root / asset.storage_path).resolve()
    if not candidate.is_relative_to(settings.content_root.resolve()) or not candidate.is_file():
        raise AppError(503, "SERVICE_UNAVAILABLE", "内容存储不可用")
    return candidate


def safe_filename(value: str, fallback: str) -> str:
    sanitized = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", "_", value).strip(" .")
    return (sanitized or fallback)[:180]


def escaped_contains(column: InstrumentedAttribute[str | None], value: str) -> ColumnElement[bool]:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")
