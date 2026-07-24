from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import (
    Asset,
    BackupJob,
    BackupSubtask,
    Base,
    Document,
    DocumentVersion,
    Operation,
    QueueItem,
    Repository,
    RepositoryCredential,
    RetentionPolicy,
    SyncCheckpoint,
    VersionAsset,
    WorkerHeartbeat,
    YuqueCredential,
)
from app.core.security import encrypt_token
from app.integrations.yuque.client import YuqueQuotaError
from app.worker.coordinator import JobCoordinator
from app.worker.queue import PersistentQueue, QueueItemSnapshot, QueueLeaseLost
from app.worker.service import WorkerService


@dataclass
class Clock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


def make_session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_master_key="11" * 32,
        data_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        yuque_request_interval_seconds=0,
    )


def test_queue_recovers_leases_and_uses_documented_retry_delays(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    clock = Clock(datetime(2026, 7, 23, tzinfo=UTC))
    queue = PersistentQueue(sessions, now=clock.now)
    queued = queue.enqueue("test", idempotency_key="one")

    claimed = queue.claim("worker-a", lease_seconds=30)
    assert claimed and claimed.id == queued.id
    queue.record_attempt(claimed.id, "worker-a")
    next_retry = queue.retry_transient(claimed.id, "worker-a", code="TEMP", message="temporary")
    assert next_retry == clock.now() + timedelta(seconds=2)

    clock.advance(seconds=2)
    second = queue.claim("worker-a", lease_seconds=30)
    assert second and second.id == queued.id
    clock.advance(seconds=31)
    assert queue.recover_expired() == 1
    recovered = queue.claim("worker-b", lease_seconds=30)
    assert recovered and recovered.attempt_count == 1


def test_cancelled_running_job_item_never_reclaims_after_lease_expiry(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    clock = Clock(datetime(2026, 7, 23, tzinfo=UTC))
    queue = PersistentQueue(sessions, now=clock.now)
    coordinator = JobCoordinator(sessions, queue, now=clock.now)
    with sessions.begin() as session:
        job = BackupJob(
            trigger="manual",
            scope={"type": "all"},
            status="running",
            active_slot=1,
            cancel_requested_at=clock.now(),
        )
        session.add(job)
        session.flush()
        item = QueueItem(
            category="repository_sync",
            payload={"stage": "metadata"},
            status="running",
            lease_owner="dead-worker",
            lease_until=clock.now() + timedelta(minutes=5),
            idempotency_key="cancel-running-lease",
            job_id=job.id,
        )
        session.add(item)
        session.flush()
        job_id = job.id
        item_id = item.id

    assert coordinator.apply_cancellations() == 1
    with sessions() as session:
        job = session.get(BackupJob, job_id)
        item = session.get(QueueItem, item_id)
        assert job is not None and job.status == "cancelled"
        assert item is not None and item.status == "cancelled"
        assert item.lease_owner is None and item.lease_until is None

    clock.advance(minutes=6)
    assert queue.recover_expired() == 0
    assert queue.claim("replacement-worker", lease_seconds=30) is None
    with sessions() as session:
        item = session.get(QueueItem, item_id)
        assert item is not None and item.status == "cancelled"


def test_claim_cancels_work_for_disabled_credential(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    clock = Clock(datetime(2026, 7, 23, tzinfo=UTC))
    queue = PersistentQueue(sessions, now=clock.now)
    settings = make_settings(tmp_path)
    credential_id = "10101010-1010-4010-8010-101010101010"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        session.add(
            YuqueCredential(
                id=credential_id,
                name="disabled",
                base_url="https://www.yuque.com",
                encrypted_token=encrypted,
                token_nonce=nonce,
                token_suffix="oken",
                status="disabled",
                enabled=False,
                verification_valid=True,
            )
        )
        item = QueueItem(
            category="repository_discovery",
            payload={"stage": "start"},
            available_at=clock.now(),
            idempotency_key="disabled-credential-work",
            credential_id=credential_id,
        )
        session.add(item)
        session.flush()
        item_id = item.id

    assert queue.claim("worker", lease_seconds=30) is None
    with sessions() as session:
        item = session.get(QueueItem, item_id)
        assert item is not None and item.status == "cancelled"


@pytest.mark.asyncio
async def test_document_commit_recovery_is_idempotent_after_queue_completion_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    clock = Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    credential_id = "20202020-2020-4020-8020-202020202020"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="main",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            subject_type="user",
            subject_id="user-1",
            login="alice",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        repository = Repository(
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="book-crash",
            name="Crash recovery",
            selected=True,
        )
        session.add_all([credential, repository])
        session.flush()
        document = Document(
            repository_id=repository.id,
            yuque_doc_id="doc-crash",
            title="Crash recovery",
            slug="crash",
        )
        job = BackupJob(
            trigger="manual",
            scope={"type": "repository", "repository_id": repository.id},
            status="running",
            active_slot=1,
        )
        session.add_all([document, job])
        session.flush()
        subtask = BackupSubtask(
            job_id=job.id,
            repository_id=repository.id,
            credential_id=credential.id,
            status="running",
            document_total=1,
        )
        session.add(subtask)
        session.flush()
        item = QueueItem(
            category="document_sync",
            payload={},
            available_at=clock.now(),
            idempotency_key=f"job:{job.id}:document:{document.id}",
            job_id=job.id,
            subtask_id=subtask.id,
            credential_id=credential.id,
            repository_id=repository.id,
            document_id=document.id,
        )
        session.add(item)
        session.flush()
        item_id = item.id
        subtask_id = subtask.id

    detail_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_calls
        assert request.url.path == "/api/v2/repos/docs/doc-crash"
        detail_calls += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "doc-crash",
                    "title": "Crash recovery",
                    "slug": "crash",
                    "type": "Doc",
                    "format": "markdown",
                    "body": "# Durable",
                    "body_html": "<h1>Durable</h1>",
                    "latest_version_id": "remote-v1",
                }
            },
            headers={"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"},
        )

    class SimulatedCrash(BaseException):
        pass

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        worker_id="crash-worker",
        yuque_http_client=client,
        resource_http_client=client,
        now=clock.now,
    )
    original_complete = service.queue.complete

    def crash_before_queue_completion(_item_id: str, _worker_id: str) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(service.queue, "complete", crash_before_queue_completion)
    with pytest.raises(SimulatedCrash):
        await service.run_once()

    with sessions() as session:
        subtask = session.get(BackupSubtask, subtask_id)
        item = session.get(QueueItem, item_id)
        checkpoint = session.scalar(
            select(SyncCheckpoint).where(SyncCheckpoint.document_id == item.document_id)
        ) if item is not None else None
        assert subtask is not None
        assert subtask.document_completed == 1
        assert subtask.document_succeeded == 1
        assert item is not None and item.status == "running"
        assert checkpoint is not None and checkpoint.data["queue_item_id"] == item_id

    monkeypatch.setattr(service.queue, "complete", original_complete)
    clock.advance(seconds=settings.queue_lease_seconds + 1)
    assert await service.run_once() is True
    with sessions() as session:
        subtask = session.get(BackupSubtask, subtask_id)
        item = session.get(QueueItem, item_id)
        assert subtask is not None
        assert subtask.document_completed == 1
        assert subtask.document_succeeded == 1
        assert item is not None and item.status == "succeeded"
        assert session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == item.document_id))
    assert detail_calls == 1
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_idle_worker_writes_heartbeat_only_at_configured_interval(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    settings.worker_heartbeat_seconds = 10
    clock = Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    service = WorkerService(sessions, settings, worker_id="heartbeat-worker", now=clock.now)

    assert await service.run_once() is False
    with sessions() as session:
        first = session.get(WorkerHeartbeat, 1)
        assert first is not None
        first_heartbeat = first.last_heartbeat_at

    for _ in range(9):
        clock.advance(seconds=1)
        assert await service.run_once() is False
    with sessions() as session:
        unchanged = session.get(WorkerHeartbeat, 1)
        assert unchanged is not None and unchanged.last_heartbeat_at == first_heartbeat

    clock.advance(seconds=1)
    assert await service.run_once() is False
    with sessions() as session:
        updated = session.get(WorkerHeartbeat, 1)
        assert updated is not None and updated.last_heartbeat_at > first_heartbeat
    await service.aclose()


@pytest.mark.asyncio
async def test_lease_loss_inside_quota_handler_does_not_escape_worker_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    service = WorkerService(sessions, settings, worker_id="lease-race-worker")
    item = QueueItemSnapshot(
        id="30303030-3030-4030-8030-303030303030",
        category="repository_sync",
        payload={},
        priority=50,
        status="running",
        attempt_count=1,
        operation_id=None,
        job_id=None,
        subtask_id=None,
        credential_id=None,
        repository_id=None,
        document_id=None,
        lease_owner=service.worker_id,
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )

    async def quota_response(_item: QueueItemSnapshot, _worker_id: str) -> None:
        raise YuqueQuotaError("quota", retry_after_seconds=30)

    def cancelled_while_handling(
        _item: QueueItemSnapshot,
        _worker_id: str,
        _error: YuqueQuotaError,
    ) -> None:
        raise QueueLeaseLost("cancelled concurrently")

    monkeypatch.setattr(service.executor, "_handle_repository_sync", quota_response)
    monkeypatch.setattr(service.executor, "_handle_quota_error", cancelled_while_handling)
    await service.executor.handle(item, service.worker_id)
    assert await service.run_once() is False
    await service.aclose()


@pytest.mark.asyncio
async def test_first_backup_flow_commits_current_document(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    clock = Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    credential_id = "11111111-1111-4111-8111-111111111111"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="main",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            subject_type="user",
            subject_id="u1",
            login="alice",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        repository = Repository(
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="book-1",
            name="Book",
            selected=True,
        )
        session.add_all([credential, repository])
        session.flush()
        session.add(
            RepositoryCredential(
                repository_id=repository.id,
                credential_id=credential.id,
                is_primary=True,
            )
        )
        session.add(
            BackupJob(
                trigger="manual",
                scope={"type": "all"},
                status="queued",
                pending_slot=1,
                active_slot=None,
            )
        )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        headers = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"}
        if request.url.path == "/api/v2/repos/book-1":
            data = {"id": "book-1", "name": "Book", "type": "Book"}
        elif request.url.path == "/api/v2/repos/book-1/toc":
            data = [{"uuid": "toc-1", "doc_id": "doc-1", "type": "DOC", "title": "Hello"}]
        elif request.url.path == "/api/v2/repos/book-1/docs":
            data = [
                {
                    "id": "doc-1",
                    "title": "Hello",
                    "slug": "hello",
                    "type": "Doc",
                    "latest_version_id": "v1",
                }
            ]
        elif request.url.path == "/api/v2/repos/docs/doc-1":
            data = {
                "id": "doc-1",
                "title": "Hello",
                "slug": "hello",
                "type": "Doc",
                "format": "markdown",
                "body": "# Hello token",
                "body_html": "<h1>Hello token</h1>",
                "latest_version_id": "v1",
                "token-key": "safe-value",
            }
        else:
            return httpx.Response(404, json={"message": "not found"}, headers=headers)
        return httpx.Response(200, json={"data": data}, headers=headers)

    yuque_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    resource_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        worker_id="worker-test",
        yuque_http_client=yuque_client,
        resource_http_client=resource_client,
        now=clock.now,
    )
    for _ in range(20):
        handled = await service.run_once()
        clock.advance(milliseconds=20)
        if not handled:
            await service.run_once()

    with sessions() as session:
        job = session.scalar(select(BackupJob))
        document = session.scalar(select(Document))
        version = session.scalar(select(DocumentVersion))
        assert job and job.status == "succeeded"
        assert document and version
        assert document.latest_successful_version_id == version.id
        assert version.content_size_bytes > 0
        assert "token" not in str(version.normalized_metadata)
    for stored_file in (tmp_path / "content" / "versions").rglob("*"):
        if stored_file.is_file():
            assert b"token" not in stored_file.read_bytes()
    assert "/api/v2/doc_versions" not in calls
    assert calls.count("/api/v2/repos/docs/doc-1") == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_purged_version_is_restored_when_same_hash_becomes_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    clock = Clock(datetime(2026, 7, 24, 12, 0, tzinfo=UTC))
    credential_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="main",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            subject_type="user",
            subject_id="u1",
            login="alice",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        repository = Repository(
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="book-revival",
            name="Book",
            selected=True,
        )
        session.add_all([credential, repository, RetentionPolicy(id=1, retention_days=15)])
        session.flush()
        session.add(
            RepositoryCredential(
                repository_id=repository.id,
                credential_id=credential.id,
                is_primary=True,
            )
        )
        document = Document(
            repository_id=repository.id,
            yuque_doc_id="doc-revival",
            title="Alpha",
            slug="doc",
            type="Doc",
            path="/doc",
            original_path="/doc",
        )
        session.add(document)
        session.flush()
        repository_id = repository.id
        document_id = document.id

    state = {"content": "alpha", "remote_version_id": "remote-alpha-1"}
    detail_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_calls
        if request.url.host == "assets.example":
            name = request.url.path.removeprefix("/")
            return httpx.Response(
                200,
                content=f"asset:{name}".encode(),
                headers={"Content-Type": "image/png"},
            )
        assert request.url.path == "/api/v2/repos/docs/doc-revival"
        detail_calls += 1
        content = state["content"]
        asset_url = f"https://assets.example/{content}.png"
        data = {
            "id": "doc-revival",
            "title": content.title(),
            "slug": "doc",
            "type": "Doc",
            "format": "markdown",
            "body": f"# {content}",
            "body_html": f'<h1>{content}</h1><img src="{asset_url}">',
            "latest_version_id": state["remote_version_id"],
        }
        return httpx.Response(
            200,
            json={"data": data},
            headers={"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"},
        )

    def enqueue_document_sync(label: str) -> tuple[str, str]:
        with sessions.begin() as session:
            job = BackupJob(
                trigger="manual",
                scope={"type": "repository", "repository_id": repository_id},
                status="running",
                started_at=clock.now(),
            )
            session.add(job)
            session.flush()
            subtask = BackupSubtask(
                job_id=job.id,
                repository_id=repository_id,
                credential_id=credential_id,
                status="running",
                document_total=1,
                started_at=clock.now(),
            )
            session.add(subtask)
            session.flush()
            session.add(
                QueueItem(
                    category="document_sync",
                    payload={},
                    available_at=clock.now(),
                    idempotency_key=f"job:{job.id}:document:{label}",
                    job_id=job.id,
                    subtask_id=subtask.id,
                    credential_id=credential_id,
                    repository_id=repository_id,
                    document_id=document_id,
                )
            )
            return job.id, subtask.id

    async def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        worker_id="worker-revival",
        yuque_http_client=client,
        resource_http_client=client,
        now=clock.now,
    )
    monkeypatch.setattr(service.asset_downloader, "_resolver", public_resolver)

    enqueue_document_sync("alpha-first")
    assert await service.run_once() is True
    state.update({"content": "beta", "remote_version_id": "remote-beta"})
    clock.advance(seconds=1)
    enqueue_document_sync("beta")
    assert await service.run_once() is True

    with sessions.begin() as session:
        alpha_version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.remote_version_id == "remote-alpha-1")
        )
        assert alpha_version is not None
        alpha_version.created_at = clock.now() - timedelta(days=30)
        alpha_reference = session.scalar(
            select(VersionAsset).where(VersionAsset.version_id == alpha_version.id)
        )
        assert alpha_reference is not None and alpha_reference.asset_id is not None
        alpha_asset = session.get(Asset, alpha_reference.asset_id)
        assert alpha_asset is not None and alpha_asset.storage_path is not None
        alpha_version_id = alpha_version.id
        alpha_asset_id = alpha_asset.id
        old_version_relative_paths = {
            "raw_response_path": alpha_version.raw_response_path,
            "raw_body_path": alpha_version.raw_body_path,
            "preview_path": alpha_version.preview_path,
            "manifest_path": alpha_version.manifest_path,
        }
        old_version_paths = tuple(
            settings.data_root / path
            for path in old_version_relative_paths.values()
            if path is not None
        )
        old_asset_path = settings.data_root / alpha_asset.storage_path

    cleanup = service.retention.run()
    assert cleanup.versions == 1
    assert cleanup.resources == 1
    assert all(not path.exists() for path in old_version_paths)
    assert not old_asset_path.exists()
    with sessions() as session:
        purged_version = session.get(DocumentVersion, alpha_version_id)
        purged_asset = session.get(Asset, alpha_asset_id)
        assert purged_version is not None and purged_version.purged_at is not None
        assert purged_version.raw_response_path is None
        assert purged_version.raw_body_path is None
        assert purged_version.preview_path is None
        assert purged_version.manifest_path is None
        assert purged_asset is not None and purged_asset.purged_at is not None
        assert purged_asset.storage_path is None
        assert session.scalar(
            select(VersionAsset).where(VersionAsset.version_id == alpha_version_id)
        ) is None

    stale_relative_path = old_version_relative_paths["raw_response_path"]
    assert stale_relative_path is not None
    stale_path = settings.data_root / stale_relative_path
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_bytes(b"incomplete purge")
    with sessions.begin() as session:
        partially_purged = session.get(DocumentVersion, alpha_version_id)
        assert partially_purged is not None
        for field, relative_path in old_version_relative_paths.items():
            setattr(partially_purged, field, relative_path)

    state.update({"content": "alpha", "remote_version_id": "remote-alpha-2"})
    clock.advance(seconds=1)
    revived_job_id, revived_subtask_id = enqueue_document_sync("alpha-revived")
    assert await service.run_once() is True

    with sessions() as session:
        versions = list(
            session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.created_at.asc())
            )
        )
        assert len(versions) == 2
        revived = session.get(DocumentVersion, alpha_version_id)
        document = session.get(Document, document_id)
        assert revived is not None and document is not None
        assert document.latest_successful_version_id == revived.id
        assert revived.remote_version_id == "remote-alpha-2"
        assert revived.purged_at is None
        assert revived.source_job_id == revived_job_id
        assert revived.created_at == clock.now().replace(tzinfo=None)
        assert revived.completeness == "complete"
        assert revived.resource_total == 1
        assert revived.resource_downloaded == 1
        assert revived.issue_count == 0
        for relative_path in (
            revived.raw_response_path,
            revived.raw_body_path,
            revived.preview_path,
            revived.manifest_path,
        ):
            assert relative_path is not None
            assert (settings.data_root / relative_path).is_file()
        references = list(
            session.scalars(select(VersionAsset).where(VersionAsset.version_id == revived.id))
        )
        assert len(references) == 1
        assert references[0].status == "downloaded"
        assert references[0].asset_id == alpha_asset_id
        revived_asset = session.get(Asset, alpha_asset_id)
        assert revived_asset is not None and revived_asset.storage_path is not None
        assert revived_asset.purged_at is None
        assert (settings.data_root / revived_asset.storage_path).read_bytes() == b"asset:alpha.png"

    summary_item = service.queue.enqueue(
        "repository_sync",
        idempotency_key=f"job:{revived_job_id}:repository-summary-check",
        payload={"stage": "documents"},
        job_id=revived_job_id,
        subtask_id=revived_subtask_id,
        credential_id=credential_id,
        repository_id=repository_id,
    )
    specs = service.executor._upsert_document_summaries(
        summary_item,
        [
            {
                "id": "doc-revival",
                "title": "Alpha",
                "slug": "doc",
                "type": "Doc",
                "latest_version_id": "remote-alpha-2",
            }
        ],
    )
    assert specs == []
    assert detail_calls == 3
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_credential_verification_sets_durable_validity(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    credential_id = "22222222-2222-4222-8222-222222222222"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="candidate",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            status="unverified",
            enabled=False,
            verification_valid=False,
        )
        operation = Operation(type="credential_verify", credential_id=credential_id, status="queued")
        session.add_all([credential, operation])
        session.flush()
        session.add(
            QueueItem(
                category="credential_verify",
                payload={},
                idempotency_key=f"operation:{operation.id}",
                operation_id=operation.id,
                credential_id=credential_id,
            )
        )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v2/user"
        return httpx.Response(
            200,
            json={"data": {"id": "u1", "login": "alice", "type": "user"}},
            headers={"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        yuque_http_client=client,
        resource_http_client=client,
    )
    assert await service.run_once() is True
    with sessions() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.scalar(select(Operation))
        assert credential is not None
        assert credential.status == "valid"
        assert credential.verification_valid is True
        assert operation is not None and operation.status == "succeeded"
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_user_discovery_with_no_groups_finishes_without_querying_user_as_group(
    tmp_path: Path,
) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    credential_id = "23232323-2323-4232-8232-232323232323"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="personal",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            subject_type="user",
            subject_id="user-1",
            login="alice",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        operation = Operation(
            type="repository_discovery",
            credential_id=credential.id,
            status="queued",
        )
        session.add_all([credential, operation])
        session.flush()
        session.add(
            QueueItem(
                category="repository_discovery",
                payload={"stage": "start", "offset": 0, "counts": {}},
                idempotency_key=f"operation:{operation.id}",
                operation_id=operation.id,
                credential_id=credential.id,
            )
        )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        headers = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"}
        if request.url.path == "/api/v2/users/alice/repos":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "personal-book",
                            "name": "Personal Book",
                            "slug": "personal-book",
                            "namespace": "alice/personal-book",
                            "type": "Book",
                        }
                    ]
                },
                headers=headers,
            )
        if request.url.path == "/api/v2/users/user-1/groups":
            return httpx.Response(200, json={"data": []}, headers=headers)
        raise AssertionError(f"unexpected discovery request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        yuque_http_client=client,
        resource_http_client=client,
    )
    assert await service.run_until_idle() == 3
    with sessions() as session:
        operation = session.scalar(select(Operation))
        repositories = session.scalars(select(Repository)).all()
        assert operation is not None and operation.status == "succeeded"
        assert [repository.yuque_book_id for repository in repositories] == ["personal-book"]
    assert calls == ["/api/v2/users/alice/repos", "/api/v2/users/user-1/groups"]
    await service.aclose()
    await client.aclose()


def test_repository_scope_promotes_unselected_target_and_queues_atomically(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    credential_id = "33333333-3333-4333-8333-333333333333"
    settings = make_settings(tmp_path)
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="main",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        repository = Repository(
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="book-explicit",
            name="Explicit",
            selected=False,
        )
        session.add_all([credential, repository])
        session.flush()
        session.add(
            RepositoryCredential(
                repository_id=repository.id,
                credential_id=credential.id,
                is_primary=True,
            )
        )
        job = BackupJob(
            trigger="manual",
            scope={
                "type": "repository",
                "repository_id": repository.id,
                "_target_repository_ids": [repository.id],
            },
            status="queued",
            pending_slot=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    coordinator = JobCoordinator(sessions, PersistentQueue(sessions))
    assert coordinator.promote_pending_job() == job_id
    with sessions() as session:
        job = session.get(BackupJob, job_id)
        subtask = session.scalar(select(BackupSubtask).where(BackupSubtask.job_id == job_id))
        queue_item = session.scalar(select(QueueItem).where(QueueItem.job_id == job_id))
        assert job is not None and job.status == "running"
        assert job.pending_slot is None and job.active_slot == 1
        assert subtask is not None and subtask.repository_id == repository.id
        assert queue_item is not None and queue_item.subtask_id == subtask.id


@pytest.mark.asyncio
async def test_summary_timestamp_and_deleted_state_reschedule_without_advancing_checkpoint(
    tmp_path: Path,
) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    old_remote_time = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    new_remote_time = old_remote_time + timedelta(hours=1)
    credential_id = "34343434-3434-4434-8434-343434343434"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="main",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        repository = Repository(
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="incremental-book",
            name="Incremental",
            selected=True,
        )
        job = BackupJob(trigger="manual", scope={"type": "all"}, status="running", active_slot=1)
        session.add_all([credential, repository, job])
        session.flush()
        subtask = BackupSubtask(
            job_id=job.id,
            repository_id=repository.id,
            credential_id=credential.id,
            status="running",
        )
        changed = Document(
            repository_id=repository.id,
            yuque_doc_id="changed-without-version-id",
            title="Changed",
            type="Doc",
            slug="changed",
            path="/changed",
            original_path="/changed",
            remote_updated_at=old_remote_time,
        )
        restored = Document(
            repository_id=repository.id,
            yuque_doc_id="restored-with-same-version",
            title="Restored",
            type="Doc",
            slug="restored",
            path="/restored",
            original_path="/restored",
            remote_updated_at=old_remote_time,
            deleted_at=new_remote_time,
        )
        session.add_all([subtask, changed, restored])
        session.flush()
        versions = [
            DocumentVersion(
                document_id=document.id,
                remote_version_id=None,
                content_hash=str(index) * 64,
                completeness="complete",
                source_job_id=job.id,
            )
            for index, document in enumerate((changed, restored), start=1)
        ]
        session.add_all(versions)
        session.flush()
        changed.latest_successful_version_id = versions[0].id
        restored.latest_successful_version_id = versions[1].id
        snapshot = QueueItemSnapshot(
            id="summary-snapshot",
            category="repository_sync",
            payload={},
            priority=50,
            status="running",
            attempt_count=0,
            operation_id=None,
            job_id=job.id,
            subtask_id=subtask.id,
            credential_id=credential.id,
            repository_id=repository.id,
            document_id=None,
            lease_owner="worker",
            lease_until=None,
        )
        changed_id = changed.id
        restored_id = restored.id

    service = WorkerService(sessions, settings)
    specs = service.executor._upsert_document_summaries(
        snapshot,
        [
            {
                "id": "changed-without-version-id",
                "title": "Changed",
                "slug": "changed",
                "type": "Doc",
                "path": "/changed",
                "updated_at": new_remote_time.isoformat(),
            },
            {
                "id": "restored-with-same-version",
                "title": "Restored",
                "slug": "restored",
                "type": "Doc",
                "path": "/restored",
                "updated_at": old_remote_time.isoformat(),
            },
        ],
    )
    assert {document_id for document_id, _summary in specs} == {changed_id, restored_id}
    with sessions() as session:
        changed = session.get(Document, changed_id)
        restored = session.get(Document, restored_id)
        assert changed is not None and changed.remote_updated_at == old_remote_time.replace(tzinfo=None)
        assert restored is not None and restored.deleted_at is not None
        subtask = session.get(BackupSubtask, snapshot.subtask_id)
        assert subtask is not None and subtask.document_total == 2
    await service.aclose()


@pytest.mark.asyncio
async def test_failed_document_does_not_advance_safe_watermark(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    clock = Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    old_watermark = clock.now() - timedelta(days=1)
    candidate_watermark = clock.now()
    credential_id = "44444444-4444-4444-8444-444444444444"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="main",
            base_url="https://www.yuque.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        repository = Repository(
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="book-failure",
            name="Failure",
            selected=True,
            safe_watermark=old_watermark,
        )
        session.add_all([credential, repository])
        session.flush()
        session.add(
            RepositoryCredential(
                repository_id=repository.id,
                credential_id=credential.id,
                is_primary=True,
            )
        )
        job = BackupJob(
            trigger="manual",
            scope={"type": "all"},
            status="running",
            active_slot=1,
            started_at=clock.now(),
        )
        session.add(job)
        session.flush()
        subtask = BackupSubtask(
            job_id=job.id,
            repository_id=repository.id,
            credential_id=credential.id,
            status="running",
        )
        document = Document(
            repository_id=repository.id,
            yuque_doc_id="failed-doc",
            title="Failed",
            type="Doc",
        )
        session.add_all([subtask, document])
        session.flush()
        session.add_all(
            [
                QueueItem(
                    category="document_sync",
                    payload={},
                    status="failed",
                    idempotency_key=f"job:{job.id}:document:{document.id}",
                    job_id=job.id,
                    subtask_id=subtask.id,
                    credential_id=credential.id,
                    repository_id=repository.id,
                    document_id=document.id,
                ),
                QueueItem(
                    category="repository_sync",
                    payload={
                        "stage": "barrier",
                        "candidate_watermark": candidate_watermark.isoformat(),
                    },
                    priority=100,
                    available_at=clock.now(),
                    idempotency_key=f"job:{job.id}:repository:{repository.id}",
                    job_id=job.id,
                    subtask_id=subtask.id,
                    credential_id=credential.id,
                    repository_id=repository.id,
                ),
            ]
        )
        job_id = job.id
        repository_id = repository.id

    service = WorkerService(sessions, settings, now=clock.now)
    assert await service.run_once() is True
    with sessions() as session:
        repository = session.get(Repository, repository_id)
        checkpoint = session.scalar(
            select(SyncCheckpoint).where(
                SyncCheckpoint.checkpoint_key == f"repository:{repository_id}:watermark"
            )
        )
        job = session.get(BackupJob, job_id)
        assert repository is not None and repository.safe_watermark == old_watermark.replace(tzinfo=None)
        assert checkpoint is not None and checkpoint.completed is False
        assert job is not None and job.status == "failed"
    await service.aclose()
