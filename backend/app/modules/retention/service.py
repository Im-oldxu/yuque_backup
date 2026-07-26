from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import (
    Asset,
    BackupJob,
    DeletionTombstone,
    Document,
    DocumentVersion,
    Repository,
    RetentionPolicy,
    VersionAsset,
)
from app.storage.content import ContentStore


@dataclass(slots=True)
class CleanupStats:
    versions: int = 0
    resources: int = 0
    failures: int = 0
    released_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class RetentionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: ContentStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))

    def run(self, *, cleanup_job_id: str | None = None) -> CleanupStats:
        current = self._now()
        cleanup_id = cleanup_job_id or str(uuid.uuid4())
        with self._session_factory() as session:
            retention_days = (
                session.scalar(select(RetentionPolicy.retention_days).where(RetentionPolicy.id == 1)) or 15
            )
        cutoff = current - timedelta(days=retention_days)
        version_ids = self._candidate_version_ids(cutoff)
        stats = CleanupStats()
        for version_id in version_ids:
            self._purge_version(version_id, current, stats)
        self._purge_zero_reference_assets(current, stats)
        self._ensure_tombstones(cutoff, current, cleanup_id)
        if cleanup_job_id:
            with self._session_factory.begin() as session:
                job = session.get(BackupJob, cleanup_job_id)
                if job is not None:
                    job.cleanup_stats = stats.as_dict()
        return stats

    def _candidate_version_ids(self, cutoff: datetime) -> list[str]:
        with self._session_factory() as session:
            due = or_(
                (
                    Document.deleted_at.is_(None)
                    & Document.latest_successful_version_id.is_not(None)
                    & (DocumentVersion.id != Document.latest_successful_version_id)
                    & (DocumentVersion.created_at <= cutoff)
                ),
                (Document.deleted_at.is_not(None) & (Document.deleted_at <= cutoff)),
            )
            unfinished_purge = DocumentVersion.purged_at.is_not(None) & or_(
                DocumentVersion.raw_response_path.is_not(None),
                DocumentVersion.raw_body_path.is_not(None),
                DocumentVersion.markdown_path.is_not(None),
                DocumentVersion.preview_path.is_not(None),
                DocumentVersion.manifest_path.is_not(None),
            )
            return list(
                session.scalars(
                    select(DocumentVersion.id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(or_(due & DocumentVersion.purged_at.is_(None), unfinished_purge))
                    .order_by(DocumentVersion.created_at.asc())
                )
            )

    def _purge_version(self, version_id: str, current: datetime, stats: CleanupStats) -> None:
        with self._session_factory.begin() as session:
            version = session.get(DocumentVersion, version_id)
            if version is None:
                return
            if version.purged_at is None:
                version.purged_at = current
            paths = (
                version.raw_response_path,
                version.raw_body_path,
                version.markdown_path,
                version.preview_path,
                version.manifest_path,
            )
            expected_bytes = version.content_size_bytes

        released = 0
        try:
            for path in dict.fromkeys(item for item in paths if item):
                released += self._store.delete_relative(path)
        except (OSError, ValueError):
            stats.failures += 1
            stats.released_bytes += released
            return

        with self._session_factory.begin() as session:
            version = session.get(DocumentVersion, version_id)
            if version is None:
                return
            asset_ids = list(
                session.scalars(
                    select(VersionAsset.asset_id).where(
                        VersionAsset.version_id == version_id,
                        VersionAsset.asset_id.is_not(None),
                    )
                )
            )
            session.execute(delete(VersionAsset).where(VersionAsset.version_id == version_id))
            version.raw_response_path = None
            version.raw_body_path = None
            version.markdown_path = None
            version.preview_path = None
            version.manifest_path = None
            version.content_size_bytes = 0
        stats.versions += 1
        stats.released_bytes += max(released, expected_bytes if released == 0 else released)
        for asset_id in dict.fromkeys(item for item in asset_ids if item):
            self._purge_asset_if_unreferenced(asset_id, current, stats)

    def _purge_zero_reference_assets(self, current: datetime, stats: CleanupStats) -> None:
        with self._session_factory() as session:
            asset_ids = list(
                session.scalars(
                    select(Asset.id).where(
                        ~select(VersionAsset.id).where(VersionAsset.asset_id == Asset.id).exists(),
                        Asset.storage_path.is_not(None),
                    )
                )
            )
        for asset_id in asset_ids:
            self._purge_asset_if_unreferenced(asset_id, current, stats)

    def _purge_asset_if_unreferenced(
        self,
        asset_id: str,
        current: datetime,
        stats: CleanupStats,
    ) -> None:
        with self._session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.storage_path is None:
                return
            reference_count = session.scalar(
                select(func.count()).select_from(VersionAsset).where(VersionAsset.asset_id == asset_id)
            )
            if reference_count:
                return
            path = asset.storage_path
            expected_size = asset.size
        try:
            released = self._store.delete_relative(path)
        except (OSError, ValueError):
            stats.failures += 1
            return
        with self._session_factory.begin() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                return
            reference_count = session.scalar(
                select(func.count()).select_from(VersionAsset).where(VersionAsset.asset_id == asset_id)
            )
            if reference_count:
                return
            asset.storage_path = None
            asset.purged_at = current
        stats.resources += 1
        stats.released_bytes += released or expected_size

    def _ensure_tombstones(
        self,
        cutoff: datetime,
        current: datetime,
        cleanup_job_id: str,
    ) -> None:
        with self._session_factory.begin() as session:
            documents = list(
                session.scalars(
                    select(Document)
                    .where(Document.deleted_at.is_not(None), Document.deleted_at <= cutoff)
                    .order_by(Document.deleted_at.asc())
                )
            )
            for document in documents:
                versions = list(
                    session.scalars(
                        select(DocumentVersion)
                        .where(DocumentVersion.document_id == document.id)
                        .order_by(DocumentVersion.created_at.desc())
                    )
                )
                if not versions or any(
                    version.purged_at is None
                    or any(
                        path is not None
                        for path in (
                            version.raw_response_path,
                            version.raw_body_path,
                            version.markdown_path,
                            version.preview_path,
                            version.manifest_path,
                        )
                    )
                    for version in versions
                ):
                    continue
                repository = session.get(Repository, document.repository_id)
                if repository is None or document.deleted_at is None:
                    continue
                existing = session.scalar(
                    select(DeletionTombstone).where(
                        DeletionTombstone.base_url == repository.normalized_base_url,
                        DeletionTombstone.yuque_book_id == repository.yuque_book_id,
                        DeletionTombstone.yuque_doc_id == document.yuque_doc_id,
                        DeletionTombstone.deleted_at == document.deleted_at,
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    DeletionTombstone(
                        base_url=repository.normalized_base_url,
                        repository_id=repository.id,
                        yuque_book_id=repository.yuque_book_id,
                        yuque_doc_id=document.yuque_doc_id,
                        title=document.title,
                        original_path=document.original_path,
                        deleted_slug=document.deleted_slug,
                        deleted_at=document.deleted_at,
                        purged_at=current,
                        source_job_id=versions[0].source_job_id,
                        cleanup_job_id=cleanup_job_id,
                    )
                )
