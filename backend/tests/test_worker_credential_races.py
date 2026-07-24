from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import (
    BackupIssue,
    BackupJob,
    BackupSubtask,
    Base,
    Document,
    Operation,
    QueueItem,
    RateLimitBucket,
    Repository,
    YuqueCredential,
)
from app.core.security import decrypt_token, encrypt_token
from app.worker.service import WorkerService

RATE_HEADERS = {
    "X-RateLimit-Limit": "5000",
    "X-RateLimit-Remaining": "4999",
}


def make_session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'credential-races.sqlite3'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_master_key="11" * 32,
        data_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'credential-races.sqlite3'}",
        yuque_request_interval_seconds=0,
    )


def seed_verification(
    sessions: sessionmaker[Session],
    settings: Settings,
) -> tuple[str, str, str]:
    credential_id = "11111111-1111-4111-8111-111111111111"
    encrypted, nonce = encrypt_token("old-token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="candidate",
            base_url="https://old.example.com",
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="oken",
            status="unverified",
            enabled=False,
            verification_valid=False,
        )
        operation = Operation(
            type="credential_verify",
            credential_id=credential_id,
            status="queued",
        )
        session.add_all([credential, operation])
        session.flush()
        queue_item = QueueItem(
            category="credential_verify",
            payload={"credential_id": credential_id},
            priority=10,
            idempotency_key=f"operation:{operation.id}",
            operation_id=operation.id,
            credential_id=credential_id,
        )
        session.add(queue_item)
        session.flush()
        return credential_id, operation.id, queue_item.id


async def start_blocked_verification(
    sessions: sessionmaker[Session],
    settings: Settings,
) -> tuple[WorkerService, httpx.AsyncClient, asyncio.Task[bool], asyncio.Event]:
    request_started = asyncio.Event()
    release_response = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "old.example.com"
        assert request.url.path == "/api/v2/user"
        assert request.headers["X-Auth-Token"] == "old-token"
        request_started.set()
        await release_response.wait()
        return httpx.Response(
            200,
            json={"data": {"id": "old-user", "login": "old-login", "type": "user"}},
            headers=RATE_HEADERS,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        worker_id="verification-worker",
        yuque_http_client=client,
        resource_http_client=client,
    )
    task = asyncio.create_task(service.run_once())
    await asyncio.wait_for(request_started.wait(), timeout=2)
    return service, client, task, release_response


@pytest.mark.asyncio
async def test_inflight_verification_cannot_validate_patched_secret(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    credential_id, operation_id, queue_item_id = seed_verification(sessions, settings)
    service, client, task, release_response = await start_blocked_verification(sessions, settings)

    encrypted, nonce = encrypt_token("new-token", credential_id, settings)
    with sessions.begin() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.get(Operation, operation_id)
        assert credential is not None
        assert operation is not None and operation.status == "running"
        operation.status = "cancelled"
        operation.finished_at = datetime.now(UTC)
        credential.base_url = "https://new.example.com"
        credential.encrypted_token = encrypted
        credential.token_nonce = nonce
        credential.token_suffix = "oken"
        credential.key_version = 1
        credential.subject_type = "unknown"
        credential.subject_id = None
        credential.login = None
        credential.status = "unverified"
        credential.verification_valid = False
        credential.enabled = False
        credential.last_verified_at = None
        credential.rate_limit_limit = None
        credential.rate_limit_remaining = None
        credential.rate_limit_observed_at = None
        credential.next_retry_at = None
        credential.last_error_code = None

    release_response.set()
    assert await asyncio.wait_for(task, timeout=2) is True
    await service.aclose()
    await client.aclose()

    with sessions() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.get(Operation, operation_id)
        queue_item = session.get(QueueItem, queue_item_id)
        assert credential is not None
        assert decrypt_token(credential, settings) == "new-token"
        assert credential.base_url == "https://new.example.com"
        assert credential.status == "unverified"
        assert credential.verification_valid is False
        assert credential.subject_type == "unknown"
        assert credential.subject_id is None and credential.login is None
        assert credential.rate_limit_limit is None
        assert credential.rate_limit_remaining is None
        assert credential.rate_limit_observed_at is None
        assert operation is not None and operation.status == "cancelled"
        assert operation.result is None
        assert queue_item is not None and queue_item.status == "cancelled"
        assert queue_item.last_error_code == "CREDENTIAL_VERIFICATION_STALE"
        assert session.scalar(select(func.count()).select_from(RateLimitBucket)) == 0


@pytest.mark.asyncio
async def test_inflight_verification_honors_terminal_operation_state(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    credential_id, operation_id, queue_item_id = seed_verification(sessions, settings)
    service, client, task, release_response = await start_blocked_verification(sessions, settings)

    with sessions.begin() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None and operation.status == "running"
        operation.status = "cancelled"
        operation.finished_at = datetime.now(UTC)

    release_response.set()
    assert await asyncio.wait_for(task, timeout=2) is True
    await service.aclose()
    await client.aclose()

    with sessions() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.get(Operation, operation_id)
        queue_item = session.get(QueueItem, queue_item_id)
        assert credential is not None
        assert decrypt_token(credential, settings) == "old-token"
        assert credential.status == "unverified"
        assert credential.verification_valid is False
        assert credential.subject_id is None and credential.login is None
        assert credential.rate_limit_limit is None
        assert operation is not None and operation.status == "cancelled"
        assert operation.result is None
        assert queue_item is not None and queue_item.status == "cancelled"
        assert queue_item.last_error_code == "CREDENTIAL_VERIFICATION_STALE"


def add_credential(
    session: Session,
    settings: Settings,
    *,
    credential_id: str,
    name: str,
    token: str,
    subject_id: str,
) -> YuqueCredential:
    encrypted, nonce = encrypt_token(token, credential_id, settings)
    credential = YuqueCredential(
        id=credential_id,
        name=name,
        base_url="https://www.yuque.com",
        encrypted_token=encrypted,
        token_nonce=nonce,
        token_suffix=token[-4:],
        subject_type="user",
        subject_id=subject_id,
        login=name,
        status="valid",
        enabled=True,
        verification_valid=True,
    )
    session.add(credential)
    return credential


@pytest.mark.asyncio
async def test_auth_failure_stops_only_failed_credential_subqueues(tmp_path: Path) -> None:
    sessions = make_session_factory(tmp_path)
    settings = make_settings(tmp_path)
    credential_a_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    credential_b_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    with sessions.begin() as session:
        credential_a = add_credential(
            session,
            settings,
            credential_id=credential_a_id,
            name="credential-a",
            token="token-a",
            subject_id="user-a",
        )
        credential_b = add_credential(
            session,
            settings,
            credential_id=credential_b_id,
            name="credential-b",
            token="token-b",
            subject_id="user-b",
        )
        repositories = [
            Repository(
                normalized_base_url="https://www.yuque.com",
                yuque_book_id=book_id,
                name=book_id,
                selected=True,
            )
            for book_id in ("book-a-current", "book-a-pending", "book-b")
        ]
        session.add_all([*repositories, credential_a, credential_b])
        session.flush()
        job = BackupJob(
            trigger="manual",
            scope={"type": "all"},
            status="running",
            active_slot=1,
            started_at=datetime.now(UTC),
        )
        session.add(job)
        session.flush()
        subtasks = [
            BackupSubtask(
                job_id=job.id,
                repository_id=repository.id,
                credential_id=credential_id,
                status=status,
                document_total=document_total,
            )
            for repository, credential_id, status, document_total in (
                (repositories[0], credential_a_id, "running", 1),
                (repositories[1], credential_a_id, "queued", 0),
                (repositories[2], credential_b_id, "queued", 0),
            )
        ]
        session.add_all(subtasks)
        session.flush()
        pending_document = Document(
            repository_id=repositories[0].id,
            yuque_doc_id="pending-doc",
            title="Pending",
        )
        session.add(pending_document)
        session.flush()
        queue_items = [
            QueueItem(
                category="repository_sync",
                payload={"stage": "metadata"},
                priority=1,
                idempotency_key="auth-current",
                job_id=job.id,
                subtask_id=subtasks[0].id,
                credential_id=credential_a_id,
                repository_id=repositories[0].id,
            ),
            QueueItem(
                category="document_sync",
                payload={},
                priority=10,
                status="retry_wait",
                next_retry_at=datetime.now(UTC),
                idempotency_key="auth-retry-wait",
                job_id=job.id,
                subtask_id=subtasks[0].id,
                credential_id=credential_a_id,
                repository_id=repositories[0].id,
                document_id=pending_document.id,
            ),
            QueueItem(
                category="repository_sync",
                payload={"stage": "metadata"},
                priority=20,
                idempotency_key="auth-pending-a",
                job_id=job.id,
                subtask_id=subtasks[1].id,
                credential_id=credential_a_id,
                repository_id=repositories[1].id,
            ),
            QueueItem(
                category="repository_sync",
                payload={"stage": "metadata"},
                priority=30,
                idempotency_key="auth-pending-b",
                job_id=job.id,
                subtask_id=subtasks[2].id,
                credential_id=credential_b_id,
                repository_id=repositories[2].id,
            ),
        ]
        session.add_all(queue_items)
        session.flush()
        job_id = job.id
        subtask_ids = [subtask.id for subtask in subtasks]
        queue_item_ids = [queue_item.id for queue_item in queue_items]

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["X-Auth-Token"]
        calls.append(token)
        assert token == "token-a"
        assert request.url.path == "/api/v2/repos/book-a-current"
        return httpx.Response(401, json={"message": "unauthorized"}, headers=RATE_HEADERS)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        service = WorkerService(
            sessions,
            settings,
            worker_id="auth-worker",
            yuque_http_client=client,
            resource_http_client=client,
        )
        assert await service.run_once() is True
        await service.aclose()

    assert calls == ["token-a"]
    with sessions() as session:
        stored_credential_a = session.get(YuqueCredential, credential_a_id)
        stored_credential_b = session.get(YuqueCredential, credential_b_id)
        stored_job = session.get(BackupJob, job_id)
        stored_subtasks = [session.get(BackupSubtask, subtask_id) for subtask_id in subtask_ids]
        stored_queue_items = [session.get(QueueItem, queue_item_id) for queue_item_id in queue_item_ids]
        assert stored_credential_a is not None
        assert stored_credential_a.status == "action_required"
        assert stored_credential_a.enabled is False
        assert stored_credential_a.verification_valid is False
        assert stored_credential_b is not None
        assert stored_credential_b.status == "valid"
        assert stored_credential_b.enabled is True
        assert stored_credential_b.verification_valid is True
        assert [queue_item.status if queue_item else None for queue_item in stored_queue_items] == [
            "failed",
            "cancelled",
            "cancelled",
            "pending",
        ]
        assert stored_subtasks[0] is not None and stored_subtasks[0].status == "failed"
        assert stored_subtasks[0].document_completed == 1
        assert stored_subtasks[0].document_failed == 1
        assert stored_subtasks[0].finished_at is not None
        assert stored_subtasks[1] is not None and stored_subtasks[1].status == "failed"
        assert stored_subtasks[1].finished_at is not None
        assert stored_subtasks[2] is not None and stored_subtasks[2].status == "queued"
        assert stored_job is not None and stored_job.status == "running"
        assert stored_job.active_slot == 1 and stored_job.finished_at is None
        assert stored_job.issue_count == 2
        assert session.scalar(select(func.count()).select_from(BackupIssue)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(QueueItem)
                .where(
                    QueueItem.credential_id == credential_a_id,
                    QueueItem.status.in_(("pending", "retry_wait")),
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(QueueItem)
                .where(
                    QueueItem.credential_id == credential_b_id,
                    QueueItem.status == "pending",
                )
            )
            == 1
        )
