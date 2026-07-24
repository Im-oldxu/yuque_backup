from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import (
    Asset,
    BackupJob,
    Base,
    DeletionTombstone,
    Document,
    DocumentVersion,
    Repository,
    RetentionPolicy,
    VersionAsset,
)
from app.modules.retention import RetentionService
from app.storage.content import ContentStore


@dataclass(slots=True)
class Clock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


def make_session_factory(tmp_path: Path, *, retention_days: int = 15) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(RetentionPolicy(id=1, retention_days=retention_days))
    return sessions


def add_repository_and_source_job(
    session: Session,
    *,
    now: datetime,
    book_id: str = "book-1",
) -> tuple[Repository, BackupJob]:
    repository = Repository(
        normalized_base_url="https://www.yuque.com",
        yuque_book_id=book_id,
        name=f"Repository {book_id}",
    )
    source_job = BackupJob(
        trigger="manual",
        scope={"type": "all"},
        status="succeeded",
        created_at=now - timedelta(days=60),
        started_at=now - timedelta(days=60),
        finished_at=now - timedelta(days=60),
    )
    session.add_all([repository, source_job])
    session.flush()
    return repository, source_job


def add_document(
    session: Session,
    repository: Repository,
    *,
    remote_id: str,
    deleted_at: datetime | None = None,
) -> Document:
    document = Document(
        repository_id=repository.id,
        yuque_doc_id=remote_id,
        title=f"Document {remote_id}",
        slug=remote_id,
        path=f"/{remote_id}",
        original_path=f"/{remote_id}",
        deleted_at=deleted_at,
        deleted_slug=f"deleted-{remote_id}" if deleted_at else None,
    )
    session.add(document)
    session.flush()
    return document


def add_version(
    session: Session,
    document: Document,
    source_job: BackupJob,
    *,
    content_hash: str,
    created_at: datetime,
    raw_response_path: str | None = None,
    raw_body_path: str | None = None,
) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id,
        content_hash=content_hash,
        completeness="complete",
        raw_response_path=raw_response_path,
        raw_body_path=raw_body_path,
        content_size_bytes=0,
        source_job_id=source_job.id,
        created_at=created_at,
    )
    session.add(version)
    session.flush()
    return version


def write_stored_file(store: ContentStore, relative_path: str, content: bytes) -> Path:
    path = store.resolve(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_active_latest_version_is_protected_while_expired_history_is_purged(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sessions = make_session_factory(tmp_path)
    store = ContentStore(tmp_path / "data")
    old_relative = "content/versions/document-1/old/body.md"
    latest_relative = "content/versions/document-1/latest/body.md"
    old_path = write_stored_file(store, old_relative, b"old content")
    latest_path = write_stored_file(store, latest_relative, b"latest content")

    with sessions.begin() as session:
        repository, source_job = add_repository_and_source_job(session, now=now)
        document = add_document(session, repository, remote_id="document-1")
        old_version = add_version(
            session,
            document,
            source_job,
            content_hash="1" * 64,
            created_at=now - timedelta(days=30),
            raw_body_path=old_relative,
        )
        latest_version = add_version(
            session,
            document,
            source_job,
            content_hash="2" * 64,
            created_at=now - timedelta(days=29),
            raw_body_path=latest_relative,
        )
        old_version.content_size_bytes = old_path.stat().st_size
        latest_version.content_size_bytes = latest_path.stat().st_size
        document.latest_successful_version_id = latest_version.id
        old_version_id = old_version.id
        latest_version_id = latest_version.id

    stats = RetentionService(sessions, store, now=lambda: now).run()

    assert stats.versions == 1
    assert not old_path.exists()
    assert latest_path.read_bytes() == b"latest content"
    with sessions() as session:
        old_version = session.get(DocumentVersion, old_version_id)
        latest_version = session.get(DocumentVersion, latest_version_id)
        assert old_version is not None
        assert old_version.purged_at is not None
        assert old_version.raw_body_path is None
        assert latest_version is not None
        assert latest_version.purged_at is None
        assert latest_version.raw_body_path == latest_relative


def test_deleted_document_expiry_uses_deleted_at_instead_of_version_age(tmp_path: Path) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    cutoff = now - timedelta(days=15)
    sessions = make_session_factory(tmp_path)
    store = ContentStore(tmp_path / "data")
    due_relative = "content/versions/deleted-due/body.md"
    retained_relative = "content/versions/deleted-retained/body.md"
    due_path = write_stored_file(store, due_relative, b"recent but deleted long enough")
    retained_path = write_stored_file(store, retained_relative, b"still inside deletion window")

    with sessions.begin() as session:
        repository, source_job = add_repository_and_source_job(session, now=now)
        due_document = add_document(
            session,
            repository,
            remote_id="deleted-due",
            deleted_at=cutoff,
        )
        retained_document = add_document(
            session,
            repository,
            remote_id="deleted-retained",
            deleted_at=cutoff + timedelta(seconds=1),
        )
        due_version = add_version(
            session,
            due_document,
            source_job,
            content_hash="3" * 64,
            created_at=now,
            raw_body_path=due_relative,
        )
        retained_version = add_version(
            session,
            retained_document,
            source_job,
            content_hash="4" * 64,
            created_at=now - timedelta(days=90),
            raw_body_path=retained_relative,
        )
        due_version.content_size_bytes = due_path.stat().st_size
        retained_version.content_size_bytes = retained_path.stat().st_size
        due_document.latest_successful_version_id = due_version.id
        retained_document.latest_successful_version_id = retained_version.id
        due_version_id = due_version.id
        retained_version_id = retained_version.id

    stats = RetentionService(sessions, store, now=lambda: now).run()

    assert stats.versions == 1
    assert not due_path.exists()
    assert retained_path.exists()
    with sessions() as session:
        due_version = session.get(DocumentVersion, due_version_id)
        retained_version = session.get(DocumentVersion, retained_version_id)
        assert due_version is not None and due_version.purged_at is not None
        assert retained_version is not None and retained_version.purged_at is None


def test_shared_asset_is_reclaimed_only_after_its_last_version_reference_is_removed(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sessions = make_session_factory(tmp_path)
    store = ContentStore(tmp_path / "data")
    asset_relative = "content/assets/sha256/aa/bb/shared"
    asset_path = write_stored_file(store, asset_relative, b"shared resource")

    with sessions.begin() as session:
        repository, source_job = add_repository_and_source_job(session, now=now)
        history_document = add_document(session, repository, remote_id="history-owner")
        protected_document = add_document(session, repository, remote_id="protected-owner")
        expired_version = add_version(
            session,
            history_document,
            source_job,
            content_hash="5" * 64,
            created_at=now - timedelta(days=30),
        )
        current_history_version = add_version(
            session,
            history_document,
            source_job,
            content_hash="6" * 64,
            created_at=now,
        )
        protected_version = add_version(
            session,
            protected_document,
            source_job,
            content_hash="7" * 64,
            created_at=now - timedelta(days=30),
        )
        history_document.latest_successful_version_id = current_history_version.id
        protected_document.latest_successful_version_id = protected_version.id
        asset = Asset(
            sha256="a" * 64,
            size=asset_path.stat().st_size,
            mime_type="application/octet-stream",
            storage_path=asset_relative,
        )
        session.add(asset)
        session.flush()
        session.add_all(
            [
                VersionAsset(
                    version_id=expired_version.id,
                    asset_id=asset.id,
                    original_url="https://cdn.example/shared",
                    normalized_url="https://cdn.example/shared",
                    name="shared.bin",
                    type="attachment",
                    status="downloaded",
                ),
                VersionAsset(
                    version_id=protected_version.id,
                    asset_id=asset.id,
                    original_url="https://cdn.example/shared-copy",
                    normalized_url="https://cdn.example/shared-copy",
                    name="shared-copy.bin",
                    type="attachment",
                    status="downloaded",
                ),
            ]
        )
        asset_id = asset.id
        protected_document_id = protected_document.id

    first_stats = RetentionService(sessions, store, now=lambda: now).run()

    assert first_stats.versions == 1
    assert first_stats.resources == 0
    assert asset_path.read_bytes() == b"shared resource"
    with sessions() as session:
        asset = session.get(Asset, asset_id)
        reference_count = session.scalar(
            select(func.count()).select_from(VersionAsset).where(VersionAsset.asset_id == asset_id)
        )
        assert asset is not None
        assert asset.storage_path == asset_relative
        assert asset.purged_at is None
        assert reference_count == 1

    with sessions.begin() as session:
        protected_document = session.get(Document, protected_document_id)
        assert protected_document is not None
        protected_document.deleted_at = now - timedelta(days=15)

    second_stats = RetentionService(sessions, store, now=lambda: now).run()

    assert second_stats.versions == 1
    assert second_stats.resources == 1
    assert not asset_path.exists()
    with sessions() as session:
        asset = session.get(Asset, asset_id)
        reference_count = session.scalar(
            select(func.count()).select_from(VersionAsset).where(VersionAsset.asset_id == asset_id)
        )
        assert asset is not None
        assert asset.storage_path is None
        assert asset.purged_at is not None
        assert reference_count == 0


def test_deleted_document_tombstone_is_idempotent_and_survives_later_cleanup_runs(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 7, 24, 12, tzinfo=UTC))
    sessions = make_session_factory(tmp_path)
    store = ContentStore(tmp_path / "data")
    relative_path = "content/versions/tombstone/body.md"
    stored_path = write_stored_file(store, relative_path, b"to be purged")

    with sessions.begin() as session:
        repository, source_job = add_repository_and_source_job(session, now=clock.now())
        document = add_document(
            session,
            repository,
            remote_id="deleted-forever",
            deleted_at=clock.now() - timedelta(days=16),
        )
        version = add_version(
            session,
            document,
            source_job,
            content_hash="8" * 64,
            created_at=clock.now(),
            raw_body_path=relative_path,
        )
        version.content_size_bytes = stored_path.stat().st_size
        document.latest_successful_version_id = version.id
        document_id = document.id
        source_job_id = source_job.id

    service = RetentionService(sessions, store, now=clock.now)
    first_cleanup_id = "11111111-1111-4111-8111-111111111111"
    service.run(cleanup_job_id=first_cleanup_id)

    with sessions() as session:
        tombstones = list(session.scalars(select(DeletionTombstone)))
        assert len(tombstones) == 1
        tombstone = tombstones[0]
        tombstone_id = tombstone.id
        original_purged_at = tombstone.purged_at
        assert tombstone.source_job_id == source_job_id
        assert tombstone.cleanup_job_id == first_cleanup_id
        assert tombstone.yuque_doc_id == "deleted-forever"

    clock.advance(days=3650)
    service.run(cleanup_job_id="22222222-2222-4222-8222-222222222222")
    service.run(cleanup_job_id="33333333-3333-4333-8333-333333333333")

    with sessions() as session:
        tombstones = list(session.scalars(select(DeletionTombstone)))
        document = session.get(Document, document_id)
        assert len(tombstones) == 1
        assert tombstones[0].id == tombstone_id
        assert tombstones[0].purged_at == original_purged_at
        assert tombstones[0].cleanup_job_id == first_cleanup_id
        assert document is not None


def test_partial_file_delete_failure_is_retried_without_losing_database_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sessions = make_session_factory(tmp_path)
    store = ContentStore(tmp_path / "data")
    first_relative = "content/versions/retry/old/raw-response.json"
    second_relative = "content/versions/retry/old/body.md"
    latest_relative = "content/versions/retry/latest/body.md"
    first_path = write_stored_file(store, first_relative, b"raw response")
    second_path = write_stored_file(store, second_relative, b"raw body")
    latest_path = write_stored_file(store, latest_relative, b"latest")

    with sessions.begin() as session:
        repository, source_job = add_repository_and_source_job(session, now=now)
        document = add_document(session, repository, remote_id="retry")
        expired_version = add_version(
            session,
            document,
            source_job,
            content_hash="9" * 64,
            created_at=now - timedelta(days=30),
            raw_response_path=first_relative,
            raw_body_path=second_relative,
        )
        latest_version = add_version(
            session,
            document,
            source_job,
            content_hash="a" * 64,
            created_at=now,
            raw_body_path=latest_relative,
        )
        expired_version.content_size_bytes = first_path.stat().st_size + second_path.stat().st_size
        latest_version.content_size_bytes = latest_path.stat().st_size
        document.latest_successful_version_id = latest_version.id
        expired_version_id = expired_version.id

    original_delete = store.delete_relative
    call_count = 0

    def fail_second_delete_once(relative_path: str | None) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("simulated transient filesystem failure")
        return original_delete(relative_path)

    monkeypatch.setattr(store, "delete_relative", fail_second_delete_once)
    service = RetentionService(sessions, store, now=lambda: now)

    failed_stats = service.run()

    assert failed_stats.failures == 1
    assert failed_stats.versions == 0
    assert not first_path.exists()
    assert second_path.exists()
    with sessions() as session:
        expired_version = session.get(DocumentVersion, expired_version_id)
        assert expired_version is not None
        assert expired_version.purged_at is not None
        assert expired_version.raw_response_path == first_relative
        assert expired_version.raw_body_path == second_relative

    retry_stats = service.run()

    assert retry_stats.failures == 0
    assert retry_stats.versions == 1
    assert not first_path.exists()
    assert not second_path.exists()
    assert latest_path.exists()
    with sessions() as session:
        expired_version = session.get(DocumentVersion, expired_version_id)
        assert expired_version is not None
        assert expired_version.raw_response_path is None
        assert expired_version.raw_body_path is None
        assert expired_version.content_size_bytes == 0


def test_failed_deleted_document_cleanup_defers_tombstone_until_retry_succeeds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sessions = make_session_factory(tmp_path)
    store = ContentStore(tmp_path / "data")
    raw_relative = "content/versions/deleted-retry/raw-response.json"
    body_relative = "content/versions/deleted-retry/body.md"
    raw_path = write_stored_file(store, raw_relative, b"response")
    body_path = write_stored_file(store, body_relative, b"body")
    raw_size = raw_path.stat().st_size
    body_size = body_path.stat().st_size

    with sessions.begin() as session:
        repository, source_job = add_repository_and_source_job(session, now=now)
        document = add_document(
            session,
            repository,
            remote_id="deleted-retry",
            deleted_at=now - timedelta(days=16),
        )
        version = add_version(
            session,
            document,
            source_job,
            content_hash="b" * 64,
            created_at=now,
            raw_response_path=raw_relative,
            raw_body_path=body_relative,
        )
        version.content_size_bytes = raw_size + body_size
        document.latest_successful_version_id = version.id

    original_delete = store.delete_relative
    call_count = 0

    def fail_second_delete_once(relative_path: str | None) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("simulated transient filesystem failure")
        return original_delete(relative_path)

    monkeypatch.setattr(store, "delete_relative", fail_second_delete_once)
    service = RetentionService(sessions, store, now=lambda: now)

    failed_stats = service.run(cleanup_job_id="44444444-4444-4444-8444-444444444444")

    assert failed_stats.failures == 1
    assert failed_stats.released_bytes == raw_size
    assert not raw_path.exists()
    assert body_path.exists()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DeletionTombstone)) == 0

    retry_stats = service.run(cleanup_job_id="55555555-5555-4555-8555-555555555555")

    assert retry_stats.failures == 0
    assert retry_stats.versions == 1
    assert retry_stats.released_bytes == body_size
    assert not body_path.exists()
    with sessions() as session:
        tombstones = list(session.scalars(select(DeletionTombstone)))
        assert len(tombstones) == 1
        assert tombstones[0].cleanup_job_id == "55555555-5555-4555-8555-555555555555"
