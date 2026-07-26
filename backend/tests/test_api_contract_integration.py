from __future__ import annotations

import base64
import os
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("APP_MASTER_KEY", base64.urlsafe_b64encode(b"t" * 32).decode())

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.core.models import (
    Asset,
    BackupJob,
    BackupSubtask,
    Base,
    Document,
    DocumentVersion,
    IdempotencyRecord,
    JobTrigger,
    Operation,
    QueueItem,
    Repository,
    RepositoryCredential,
    VersionAsset,
    YuqueCredential,
)
from app.core.security import CSRF_COOKIE, decrypt_token
from app.modules.auth import router as auth_router
from app.modules.backups.router import router as backups_router
from app.modules.credentials.router import router as credentials_router
from app.modules.documents.router import router as documents_router
from app.modules.repositories.router import router as repositories_router
from app.worker.coordinator import JobCoordinator
from app.worker.queue import PersistentQueue

ORIGIN = "http://testserver"
PASSWORD = "integration-password-123"


@dataclass
class ApiHarness:
    app: FastAPI
    client: TestClient
    sessions: sessionmaker[Session]
    settings: Settings

    @property
    def csrf_headers(self) -> dict[str, str]:
        token = self.client.cookies.get(CSRF_COOKIE)
        assert token is not None
        return {"X-CSRF-Token": token}


@pytest.fixture
def api_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[ApiHarness]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    database_path = data_root / "api-integration.sqlite3"
    settings = Settings(
        _env_file=None,
        app_master_key=base64.urlsafe_b64encode(b"i" * 32).decode(),
        data_root=data_root,
        database_url=f"sqlite:///{database_path.as_posix()}",
        trusted_origins=ORIGIN,
        secure_cookies=False,
        session_ttl_seconds=3600,
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db() -> Generator[Session]:
        with sessions() as db:
            yield db

    app = FastAPI()
    register_exception_handlers(app)
    for router in (
        auth_router,
        credentials_router,
        repositories_router,
        documents_router,
        backups_router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("app.modules.credentials.router.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.documents.router.get_settings", lambda: settings)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            initialized = client.post(
                "/api/v1/system/initialize",
                headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
                json={"username": "admin", "password": PASSWORD},
            )
            assert initialized.status_code == 201, initialized.text
            yield ApiHarness(app=app, client=client, sessions=sessions, settings=settings)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_repository_credentials(
    harness: ApiHarness,
    *,
    repository_count: int = 1,
    credential_count: int = 1,
) -> tuple[list[str], list[str]]:
    credential_ids = [str(uuid.uuid4()) for _ in range(credential_count)]
    repository_ids = [str(uuid.uuid4()) for _ in range(repository_count)]
    with harness.sessions.begin() as db:
        db.add_all(
            [
                YuqueCredential(
                    id=credential_id,
                    name=f"credential-{index}",
                    base_url="https://www.yuque.com",
                    encrypted_token=b"encrypted-test-token",
                    token_nonce=b"n" * 12,
                    token_suffix=f"{index:04d}"[-4:],
                    status="valid",
                    verification_valid=True,
                    enabled=True,
                )
                for index, credential_id in enumerate(credential_ids)
            ]
        )
        db.add_all(
            [
                Repository(
                    id=repository_id,
                    normalized_base_url="https://www.yuque.com",
                    yuque_book_id=f"book-{index}",
                    name=f"repository-{index}",
                    namespace=f"team/repository-{index}",
                    selected=True,
                )
                for index, repository_id in enumerate(repository_ids)
            ]
        )
        db.flush()
        db.add_all(
            [
                RepositoryCredential(
                    id=str(
                        uuid.UUID(int=(1 << 128) - 1 - repository_index)
                        if credential_index == 0
                        else uuid.UUID(
                            int=1 + repository_index * credential_count + credential_index
                        )
                    ),
                    repository_id=repository_id,
                    credential_id=credential_id,
                    is_primary=credential_index == 0,
                )
                for repository_index, repository_id in enumerate(repository_ids)
                for credential_index, credential_id in enumerate(credential_ids)
            ]
        )
    return repository_ids, credential_ids


def test_credentials_require_auth_and_csrf_encrypt_token_and_enqueue_durably(
    api_harness: ApiHarness,
) -> None:
    payload = {
        "name": "personal-yuque",
        "base_url": "https://WWW.YUQUE.COM:443/",
        "token": "full-token-that-must-never-leak",
    }
    with TestClient(api_harness.app) as anonymous:
        response = anonymous.get("/api/v1/credentials")
        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_REQUIRED"

    missing_csrf = api_harness.client.post("/api/v1/credentials", json=payload)
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "CSRF_INVALID"

    response = api_harness.client.post(
        "/api/v1/credentials",
        headers=api_harness.csrf_headers,
        json=payload,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert payload["token"] not in response.text
    assert body["credential"]["base_url"] == "https://www.yuque.com"
    assert body["credential"]["token_masked"] == "************leak"
    assert body["credential"]["status"] == "unverified"
    assert body["credential"]["enabled"] is False
    assert body["operation"]["type"] == "credential_verify"
    assert body["operation"]["status"] == "queued"

    credential_id = body["credential"]["id"]
    operation_id = body["operation"]["id"]
    with api_harness.sessions() as db:
        credential = db.get(YuqueCredential, credential_id)
        assert credential is not None
        assert credential.encrypted_token != payload["token"].encode()
        assert payload["token"].encode() not in credential.encrypted_token
        assert decrypt_token(credential, api_harness.settings) == payload["token"]
        operation = db.get(Operation, operation_id)
        assert operation is not None and operation.status == "queued"
        queue_item = db.scalar(select(QueueItem).where(QueueItem.operation_id == operation_id))
        assert queue_item is not None
        assert queue_item.status == "pending"
        assert queue_item.payload == {"credential_id": credential_id}

    detail = api_harness.client.get(f"/api/v1/credentials/{credential_id}")
    assert detail.status_code == 200
    assert payload["token"] not in detail.text
    duplicate_verify = api_harness.client.post(
        f"/api/v1/credentials/{credential_id}/verify",
        headers=api_harness.csrf_headers,
    )
    assert duplicate_verify.status_code == 409
    assert duplicate_verify.json()["code"] == "OPERATION_ALREADY_RUNNING"

    extra_field = api_harness.client.post(
        "/api/v1/credentials",
        headers=api_harness.csrf_headers,
        json={**payload, "enabled": True},
    )
    assert extra_field.status_code == 422
    assert extra_field.json()["code"] == "VALIDATION_ERROR"


def test_enable_credential_requires_valid_public_status(api_harness: ApiHarness) -> None:
    credential_id = str(uuid.uuid4())
    with api_harness.sessions.begin() as db:
        db.add(
            YuqueCredential(
                id=credential_id,
                name="enable-status-credential",
                base_url="https://www.yuque.com",
                encrypted_token=b"encrypted-test-token",
                token_nonce=b"n" * 12,
                token_suffix="0000",
                status="disabled",
                verification_valid=True,
                enabled=False,
            )
        )

    for credential_status in ("disabled", "waiting_quota"):
        with api_harness.sessions.begin() as db:
            credential = db.get(YuqueCredential, credential_id)
            assert credential is not None
            credential.status = credential_status

        rejected = api_harness.client.post(
            f"/api/v1/credentials/{credential_id}/enable",
            headers=api_harness.csrf_headers,
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "CREDENTIAL_NOT_VALID"

    with api_harness.sessions.begin() as db:
        credential = db.get(YuqueCredential, credential_id)
        assert credential is not None
        credential.status = "valid"

    enabled = api_harness.client.post(
        f"/api/v1/credentials/{credential_id}/enable",
        headers=api_harness.csrf_headers,
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "valid"
    assert enabled.json()["enabled"] is True


def test_manual_verify_wakes_waiting_quota_operation(api_harness: ApiHarness) -> None:
    credential_id = str(uuid.uuid4())
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    with api_harness.sessions.begin() as db:
        credential = YuqueCredential(
            id=credential_id,
            name="quota-check",
            base_url="https://www.yuque.com",
            encrypted_token=b"encrypted-test-token",
            token_nonce=b"n" * 12,
            token_suffix="test",
            subject_type="user",
            subject_id="quota-subject",
            status="waiting_quota",
            verification_valid=True,
            enabled=True,
            next_retry_at=retry_at,
        )
        operation = Operation(
            type="credential_verify",
            credential_id=credential_id,
            status="waiting_quota",
            next_retry_at=retry_at,
        )
        db.add_all([credential, operation])
        db.flush()
        operation_id = operation.id
        db.add(
            QueueItem(
                category="credential_verify",
                payload={"credential_id": credential_id, "_quota_attempt": 4},
                status="retry_wait",
                available_at=retry_at,
                next_retry_at=retry_at,
                idempotency_key=f"operation:{operation_id}",
                operation_id=operation_id,
                credential_id=credential_id,
            )
        )

    response = api_harness.client.post(
        f"/api/v1/credentials/{credential_id}/verify",
        headers=api_harness.csrf_headers,
    )

    assert response.status_code == 202
    assert response.json()["id"] == operation_id
    assert response.json()["status"] == "queued"
    with api_harness.sessions() as db:
        stored_credential = db.get(YuqueCredential, credential_id)
        stored_operation = db.get(Operation, operation_id)
        queue_item = db.scalar(select(QueueItem).where(QueueItem.operation_id == operation_id))
        assert stored_credential is not None and stored_credential.next_retry_at is None
        assert stored_operation is not None and stored_operation.status == "queued"
        assert queue_item is not None and queue_item.status == "pending"
        assert queue_item.next_retry_at is None
        assert queue_item.payload == {"credential_id": credential_id, "_force_quota_probe": True}


def test_manual_verify_new_waiting_operation_forces_quota_probe(api_harness: ApiHarness) -> None:
    credential_id = str(uuid.uuid4())
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    with api_harness.sessions.begin() as db:
        db.add(
            YuqueCredential(
                id=credential_id,
                name="new-quota-check",
                base_url="https://www.yuque.com",
                encrypted_token=b"encrypted-test-token",
                token_nonce=b"n" * 12,
                token_suffix="test",
                subject_type="user",
                subject_id="quota-subject",
                status="waiting_quota",
                verification_valid=True,
                enabled=True,
                next_retry_at=retry_at,
            )
        )

    response = api_harness.client.post(
        f"/api/v1/credentials/{credential_id}/verify",
        headers=api_harness.csrf_headers,
    )

    assert response.status_code == 202
    operation_id = response.json()["id"]
    with api_harness.sessions() as db:
        stored_credential = db.get(YuqueCredential, credential_id)
        queue_item = db.scalar(select(QueueItem).where(QueueItem.operation_id == operation_id))
        assert stored_credential is not None and stored_credential.status == "waiting_quota"
        assert stored_credential.next_retry_at == retry_at.replace(tzinfo=None)
        assert queue_item is not None and queue_item.status == "pending"
        assert queue_item.payload == {"credential_id": credential_id, "_force_quota_probe": True}


@pytest.mark.parametrize("mutation", ["disable", "delete", "token", "base_url"])
def test_credential_mutations_terminalize_cancelled_backup_work(
    api_harness: ApiHarness,
    mutation: str,
) -> None:
    repository_ids, credential_ids = _seed_repository_credentials(api_harness)
    repository_id = repository_ids[0]
    credential_id = credential_ids[0]
    now = datetime.now(UTC)
    with api_harness.sessions.begin() as db:
        job = BackupJob(
            trigger="manual",
            scope={"type": "all", "_target_repository_ids": repository_ids},
            status="waiting_quota",
            active_slot=1,
            started_at=now,
            next_retry_at=now + timedelta(hours=1),
            waiting_quota_credentials=1,
        )
        db.add(job)
        db.flush()
        subtask = BackupSubtask(
            job_id=job.id,
            credential_id=credential_id,
            repository_id=repository_id,
            status="waiting_quota",
            next_retry_at=now + timedelta(hours=1),
        )
        db.add(subtask)
        db.flush()
        queue_item = QueueItem(
            category="repository_sync",
            payload={"stage": "metadata"},
            status="retry_wait",
            available_at=now,
            next_retry_at=now + timedelta(hours=1),
            idempotency_key=f"job:{job.id}:repository:{repository_id}",
            job_id=job.id,
            subtask_id=subtask.id,
            credential_id=credential_id,
            repository_id=repository_id,
        )
        db.add(queue_item)
        db.flush()
        job_id = job.id
        subtask_id = subtask.id
        queue_item_id = queue_item.id

    path = f"/api/v1/credentials/{credential_id}"
    if mutation == "disable":
        response = api_harness.client.post(
            f"{path}/disable",
            headers=api_harness.csrf_headers,
        )
    elif mutation == "delete":
        response = api_harness.client.delete(path, headers=api_harness.csrf_headers)
    elif mutation == "token":
        response = api_harness.client.patch(
            path,
            headers=api_harness.csrf_headers,
            json={"token": "replacement-secret-token"},
        )
    else:
        response = api_harness.client.patch(
            path,
            headers=api_harness.csrf_headers,
            json={"base_url": "https://enterprise.yuque.example"},
        )
    assert response.status_code == (204 if mutation == "delete" else 200), response.text

    with api_harness.sessions() as db:
        queue_item = db.get(QueueItem, queue_item_id)
        subtask = db.get(BackupSubtask, subtask_id)
        job = db.get(BackupJob, job_id)
        assert queue_item is not None and queue_item.status == "cancelled"
        assert queue_item.next_retry_at is None
        assert subtask is not None and subtask.status == "failed"
        assert subtask.finished_at is not None and subtask.next_retry_at is None
        assert job is not None and job.status == "failed"
        assert job.active_slot is None and job.finished_at is not None
        assert job.waiting_quota_credentials == 0 and job.next_retry_at is None


def test_disabling_one_credential_preserves_other_active_repository_work(
    api_harness: ApiHarness,
) -> None:
    repository_ids, credential_ids = _seed_repository_credentials(
        api_harness,
        repository_count=2,
        credential_count=2,
    )
    disabled_credential_id, active_credential_id = credential_ids
    now = datetime.now(UTC)
    with api_harness.sessions.begin() as db:
        first_default = db.scalar(
            select(RepositoryCredential).where(
                RepositoryCredential.repository_id == repository_ids[1],
                RepositoryCredential.credential_id == disabled_credential_id,
            )
        )
        second_primary = db.scalar(
            select(RepositoryCredential).where(
                RepositoryCredential.repository_id == repository_ids[1],
                RepositoryCredential.credential_id == active_credential_id,
            )
        )
        assert first_default is not None and second_primary is not None
        first_default.is_primary = False
        db.flush()
        second_primary.is_primary = True

        job = BackupJob(
            trigger="manual",
            scope={"type": "all", "_target_repository_ids": repository_ids},
            status="running",
            active_slot=1,
            started_at=now,
        )
        db.add(job)
        db.flush()
        disabled_subtask = BackupSubtask(
            job_id=job.id,
            credential_id=disabled_credential_id,
            repository_id=repository_ids[0],
            status="running",
        )
        active_subtask = BackupSubtask(
            job_id=job.id,
            credential_id=active_credential_id,
            repository_id=repository_ids[1],
            status="running",
        )
        db.add_all([disabled_subtask, active_subtask])
        db.flush()
        cancelled_item = QueueItem(
            category="document_sync",
            payload={},
            status="pending",
            available_at=now,
            idempotency_key=f"job:{job.id}:cancelled-document",
            job_id=job.id,
            subtask_id=disabled_subtask.id,
            credential_id=disabled_credential_id,
            repository_id=repository_ids[0],
        )
        running_item = QueueItem(
            category="repository_sync",
            payload={"stage": "barrier"},
            status="running",
            available_at=now,
            lease_owner="worker-a",
            lease_until=now + timedelta(minutes=1),
            idempotency_key=f"job:{job.id}:running-repository",
            job_id=job.id,
            subtask_id=disabled_subtask.id,
            credential_id=disabled_credential_id,
            repository_id=repository_ids[0],
        )
        other_item = QueueItem(
            category="repository_sync",
            payload={"stage": "metadata"},
            status="pending",
            available_at=now,
            idempotency_key=f"job:{job.id}:other-repository",
            job_id=job.id,
            subtask_id=active_subtask.id,
            credential_id=active_credential_id,
            repository_id=repository_ids[1],
        )
        db.add_all([cancelled_item, running_item, other_item])
        db.flush()
        job_id = job.id
        disabled_subtask_id = disabled_subtask.id
        active_subtask_id = active_subtask.id
        cancelled_item_id = cancelled_item.id
        running_item_id = running_item.id
        other_item_id = other_item.id

    response = api_harness.client.post(
        f"/api/v1/credentials/{disabled_credential_id}/disable",
        headers=api_harness.csrf_headers,
    )
    assert response.status_code == 200, response.text
    with api_harness.sessions() as db:
        cancelled_item = db.get(QueueItem, cancelled_item_id)
        running_item = db.get(QueueItem, running_item_id)
        other_item = db.get(QueueItem, other_item_id)
        disabled_subtask = db.get(BackupSubtask, disabled_subtask_id)
        assert cancelled_item is not None and cancelled_item.status == "cancelled"
        assert running_item is not None and running_item.status == "cancelled"
        assert running_item.lease_owner is None and running_item.lease_until is None
        assert other_item is not None and other_item.status == "pending"
        assert disabled_subtask is not None and disabled_subtask.status == "failed"
        job = db.get(BackupJob, job_id)
        assert job is not None and job.status == "running" and job.active_slot == 1

    coordinator = JobCoordinator(api_harness.sessions, PersistentQueue(api_harness.sessions))
    assert coordinator.aggregate_job(job_id) == "running"
    with api_harness.sessions() as db:
        disabled_subtask = db.get(BackupSubtask, disabled_subtask_id)
        active_subtask = db.get(BackupSubtask, active_subtask_id)
        job = db.get(BackupJob, job_id)
        assert disabled_subtask is not None and disabled_subtask.status == "failed"
        assert active_subtask is not None and active_subtask.status == "running"
        assert job is not None and job.status == "running" and job.active_slot == 1

    with api_harness.sessions.begin() as db:
        other_item = db.get(QueueItem, other_item_id)
        active_subtask = db.get(BackupSubtask, active_subtask_id)
        assert other_item is not None and active_subtask is not None
        other_item.status = "succeeded"
        other_item.finished_at = now
        active_subtask.status = "succeeded"
        active_subtask.finished_at = now
    assert coordinator.aggregate_job(job_id) == "partial"
    with api_harness.sessions() as db:
        job = db.get(BackupJob, job_id)
        assert job is not None and job.status == "partial"
        assert job.active_slot is None and job.finished_at is not None


def test_repository_selection_and_primary_credential_are_csrf_protected(
    api_harness: ApiHarness,
) -> None:
    repository_ids, credential_ids = _seed_repository_credentials(
        api_harness,
        credential_count=3,
    )
    repository_id = repository_ids[0]
    original_primary, replacement_primary, inaccessible = credential_ids

    detail = api_harness.client.get(f"/api/v1/repositories/{repository_id}")
    assert detail.status_code == 200
    assert detail.json()["primary_credential_id"] == original_primary
    assert detail.json()["credential_count"] == 3
    assert detail.json()["connection_status"] == "connected"
    assert isinstance(detail.json()["id"], str)
    assert uuid.UUID(detail.json()["id"]) == uuid.UUID(repository_id)
    assert {item["id"] for item in detail.json()["credentials"]} == set(credential_ids)

    no_csrf = api_harness.client.patch(
        f"/api/v1/repositories/{repository_id}/selection",
        json={"selected": False},
    )
    assert no_csrf.status_code == 403
    selected = api_harness.client.patch(
        f"/api/v1/repositories/{repository_id}/selection",
        headers=api_harness.csrf_headers,
        json={"selected": False},
    )
    assert selected.status_code == 200
    assert selected.json()["selected"] is False
    assert "credentials" not in selected.json()
    filtered = api_harness.client.get("/api/v1/repositories?selected=false")
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["items"]] == [repository_id]

    switched = api_harness.client.put(
        f"/api/v1/repositories/{repository_id}/primary-credential",
        headers=api_harness.csrf_headers,
        json={"credential_id": replacement_primary},
    )
    assert switched.status_code == 200
    assert switched.json()["primary_credential_id"] == replacement_primary
    assert "credentials" not in switched.json()
    with api_harness.sessions() as db:
        primary_ids = db.scalars(
            select(RepositoryCredential.credential_id).where(
                RepositoryCredential.repository_id == repository_id,
                RepositoryCredential.is_primary.is_(True),
            )
        ).all()
        assert primary_ids == [replacement_primary]

    with api_harness.sessions.begin() as db:
        relation = db.scalar(
            select(RepositoryCredential).where(
                RepositoryCredential.repository_id == repository_id,
                RepositoryCredential.credential_id == inaccessible,
            )
        )
        assert relation is not None
        db.delete(relation)
    inaccessible_response = api_harness.client.put(
        f"/api/v1/repositories/{repository_id}/primary-credential",
        headers=api_harness.csrf_headers,
        json={"credential_id": inaccessible},
    )
    assert inaccessible_response.status_code == 409
    assert inaccessible_response.json()["code"] == "CREDENTIAL_CANNOT_ACCESS_REPOSITORY"


def test_repository_list_paginates_in_sql_and_filters_connection_status(
    api_harness: ApiHarness,
) -> None:
    specifications = [
        ("repo-a-connected", "valid", True, True, True, 1),
        ("repo-b-disabled", "disabled", True, True, True, 2),
        ("repo-c-action", "action_required", False, True, True, 3),
        ("repo-d-no-primary", "valid", True, True, False, 4),
    ]
    repository_ids: dict[str, str] = {}
    credential_ids: dict[str, str] = {}
    with api_harness.sessions.begin() as db:
        for name, status, verification_valid, enabled, is_primary, document_count in specifications:
            repository_id = str(uuid.uuid4())
            credential_id = str(uuid.uuid4())
            repository_ids[name] = repository_id
            credential_ids[name] = credential_id
            db.add(
                Repository(
                    id=repository_id,
                    normalized_base_url="https://www.yuque.com",
                    yuque_book_id=f"book-{name}",
                    name=name,
                    namespace=f"team/{name}",
                    selected=True,
                )
            )
            db.add(
                YuqueCredential(
                    id=credential_id,
                    name=f"credential-{name}",
                    base_url="https://www.yuque.com",
                    encrypted_token=b"encrypted-test-token",
                    token_nonce=b"n" * 12,
                    token_suffix="test",
                    status=status,
                    verification_valid=verification_valid,
                    enabled=enabled,
                )
            )
            db.flush()
            db.add(
                RepositoryCredential(
                    repository_id=repository_id,
                    credential_id=credential_id,
                    is_primary=is_primary,
                )
            )
            db.add_all(
                [
                    Document(
                        repository_id=repository_id,
                        yuque_doc_id=f"{name}-document-{index}",
                        type="Doc",
                        title=f"Document {index}",
                        path=f"/{index}",
                        original_path=f"/{index}",
                    )
                    for index in range(document_count)
                ]
            )

    engine = api_harness.sessions.kw["bind"]
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        response = api_harness.client.get(
            "/api/v1/repositories",
            params={"page": 2, "page_size": 2},
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "id": repository_ids["repo-c-action"],
                "yuque_book_id": "book-repo-c-action",
                "base_url": "https://www.yuque.com",
                "name": "repo-c-action",
                "slug": None,
                "namespace": "team/repo-c-action",
                "selected": True,
                "connection_status": "action_required",
                "primary_credential_id": credential_ids["repo-c-action"],
                "credential_count": 1,
                "document_count": 3,
                "last_success_at": None,
                "content_updated_at": None,
            },
            {
                "id": repository_ids["repo-d-no-primary"],
                "yuque_book_id": "book-repo-d-no-primary",
                "base_url": "https://www.yuque.com",
                "name": "repo-d-no-primary",
                "slug": None,
                "namespace": "team/repo-d-no-primary",
                "selected": True,
                "connection_status": "disabled",
                "primary_credential_id": None,
                "credential_count": 1,
                "document_count": 4,
                "last_success_at": None,
                "content_updated_at": None,
            },
        ],
        "page": 2,
        "page_size": 2,
        "total": 4,
    }
    assert any(
        "FROM repository ORDER BY repository.name ASC, repository.id ASC LIMIT ? OFFSET ?"
        in statement
        for statement in statements
    )
    assert sum(
        "FROM repository_credential JOIN yuque_credential" in statement
        for statement in statements
    ) == 1
    assert sum(
        "FROM document" in statement and "GROUP BY document.repository_id" in statement
        for statement in statements
    ) == 1

    expected_by_status = {
        "connected": ["repo-a-connected"],
        "disabled": ["repo-b-disabled", "repo-d-no-primary"],
        "action_required": ["repo-c-action"],
    }
    for connection_status, expected_names in expected_by_status.items():
        filtered = api_harness.client.get(
            "/api/v1/repositories",
            params={"connection_status": connection_status},
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == len(expected_names)
        assert [item["name"] for item in filtered.json()["items"]] == expected_names

    by_credential = api_harness.client.get(
        "/api/v1/repositories",
        params={"credential_id": credential_ids["repo-b-disabled"]},
    )
    assert by_credential.status_code == 200, by_credential.text
    assert [item["name"] for item in by_credential.json()["items"]] == ["repo-b-disabled"]
    assert all("credentials" not in item for item in by_credential.json()["items"])


def test_backup_job_list_rejects_inverted_created_range(api_harness: ApiHarness) -> None:
    response = api_harness.client.get(
        "/api/v1/backup-jobs",
        params={
            "created_from": "2026-07-24T00:00:00Z",
            "created_to": "2026-07-23T00:00:00Z",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["field_errors"] == [
        {"field": "created_from", "reason": "range"}
    ]


def test_backup_job_progress_is_exposed_as_percentage(api_harness: ApiHarness) -> None:
    job_id = str(uuid.uuid4())
    with api_harness.sessions.begin() as db:
        db.add(
            BackupJob(
                id=job_id,
                trigger="manual",
                scope={"type": "all"},
                status="running",
                progress=0.5,
                active_slot=1,
                pending_slot=None,
            )
        )

    response = api_harness.client.get(f"/api/v1/backup-jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["progress"] == 50.0


def test_backup_subtask_exposes_current_document_resource_activity(
    api_harness: ApiHarness,
) -> None:
    repository_ids, credential_ids = _seed_repository_credentials(api_harness)
    job_id = str(uuid.uuid4())
    subtask_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    with api_harness.sessions.begin() as db:
        db.add(
            BackupJob(
                id=job_id,
                trigger="manual",
                scope={"type": "repository", "repository_id": repository_ids[0]},
                status="running",
                active_slot=1,
            )
        )
        db.add(
            BackupSubtask(
                id=subtask_id,
                job_id=job_id,
                credential_id=credential_ids[0],
                repository_id=repository_ids[0],
                status="running",
                document_total=3,
            )
        )
        db.add(
            Document(
                id=document_id,
                repository_id=repository_ids[0],
                yuque_doc_id="active-document",
                title="Active document",
                type="Doc",
                path="/active-document",
                original_path="/active-document",
            )
        )
        db.add(
            QueueItem(
                category="document_sync",
                status="running",
                lease_owner="worker-test",
                lease_until=now + timedelta(minutes=5),
                idempotency_key=f"job:{job_id}:document:{document_id}",
                payload={
                    "_activity": {
                        "stage": "resource_retry",
                        "document_title": "Active document",
                        "resource_name": "diagram.png",
                        "resource_completed": 1,
                        "resource_total": 3,
                        "attempt": 2,
                        "max_attempts": 4,
                        "retry_in_seconds": 10,
                        "last_error_code": "RESOURCE_NETWORK_ERROR",
                        "updated_at": now.isoformat(),
                    }
                },
                job_id=job_id,
                subtask_id=subtask_id,
                credential_id=credential_ids[0],
                repository_id=repository_ids[0],
                document_id=document_id,
            )
        )

    response = api_harness.client.get(f"/api/v1/backup-jobs/{job_id}/subtasks")

    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["activity"] == {
        "stage": "resource_retry",
        "document_title": "Active document",
        "resource_name": "diagram.png",
        "resource_completed": 1,
        "resource_total": 3,
        "attempt": 2,
        "max_attempts": 4,
        "retry_in_seconds": 10,
        "last_error_code": "RESOURCE_NETWORK_ERROR",
        "updated_at": now.isoformat().replace("+00:00", "Z"),
    }


def test_backup_job_repository_filter_includes_scope_targets_and_persisted_subtasks(
    api_harness: ApiHarness,
) -> None:
    repository_ids, credential_ids = _seed_repository_credentials(
        api_harness,
        repository_count=2,
    )
    now = datetime.now(UTC)
    historical_job_id = str(uuid.uuid4())
    queued_job_id = str(uuid.uuid4())
    unrelated_job_id = str(uuid.uuid4())
    with api_harness.sessions.begin() as db:
        db.add_all(
            [
                BackupJob(
                    id=historical_job_id,
                    trigger="cron",
                    scope={"type": "all"},
                    status="succeeded",
                    created_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=1),
                ),
                BackupJob(
                    id=queued_job_id,
                    trigger="manual",
                    scope={
                        "type": "repository",
                        "repository_id": repository_ids[0],
                        "_target_repository_ids": [repository_ids[0]],
                    },
                    status="queued",
                    created_at=now,
                ),
                BackupJob(
                    id=unrelated_job_id,
                    trigger="manual",
                    scope={
                        "type": "repository",
                        "repository_id": repository_ids[1],
                        "_target_repository_ids": [repository_ids[1]],
                    },
                    status="failed",
                    created_at=now - timedelta(minutes=3),
                    finished_at=now - timedelta(minutes=2),
                ),
            ]
        )
        db.flush()
        db.add(
            BackupSubtask(
                job_id=historical_job_id,
                credential_id=credential_ids[0],
                repository_id=repository_ids[0],
                status="succeeded",
            )
        )

    response = api_harness.client.get(
        "/api/v1/backup-jobs",
        params={"repository_id": repository_ids[0], "page": 1, "page_size": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert [item["id"] for item in response.json()["items"]] == [queued_job_id]

    second_page = api_harness.client.get(
        "/api/v1/backup-jobs",
        params={"repository_id": repository_ids[0], "page": 2, "page_size": 1},
    )
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()["total"] == 2
    assert [item["id"] for item in second_page.json()["items"]] == [historical_job_id]

    unrelated = api_harness.client.get(
        "/api/v1/backup-jobs",
        params={"repository_id": repository_ids[1]},
    )
    assert unrelated.status_code == 200, unrelated.text
    assert [item["id"] for item in unrelated.json()["items"]] == [unrelated_job_id]


def test_backup_job_credential_filter_includes_queued_target_snapshot(
    api_harness: ApiHarness,
) -> None:
    _repository_ids, credential_ids = _seed_repository_credentials(api_harness)
    accepted = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={
            **api_harness.csrf_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={"scope": {"type": "credential", "credential_id": credential_ids[0]}},
    )
    assert accepted.status_code == 202, accepted.text

    filtered = api_harness.client.get(
        "/api/v1/backup-jobs",
        params={"credential_id": credential_ids[0]},
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == accepted.json()["job"]["id"]

    unrelated = api_harness.client.get(
        "/api/v1/backup-jobs",
        params={"credential_id": str(uuid.uuid4())},
    )
    assert unrelated.status_code == 200, unrelated.text
    assert unrelated.json()["total"] == 0


def test_backup_job_idempotency_merges_into_one_pending_slot_beside_active_job(
    api_harness: ApiHarness,
) -> None:
    repository_ids, _ = _seed_repository_credentials(api_harness, repository_count=2)
    active_id = str(uuid.uuid4())
    with api_harness.sessions.begin() as db:
        db.add(
            BackupJob(
                id=active_id,
                trigger="manual",
                scope={"type": "all", "_target_repository_ids": repository_ids},
                status="running",
                active_slot=1,
                pending_slot=None,
            )
        )

    first_key = str(uuid.uuid4())
    first_payload = {"scope": {"type": "repository", "repository_id": repository_ids[0]}}
    missing_key = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers=api_harness.csrf_headers,
        json=first_payload,
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    first = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={**api_harness.csrf_headers, "Idempotency-Key": first_key},
        json=first_payload,
    )
    assert first.status_code == 202, first.text
    assert first.json()["merged"] is False
    queued_id = first.json()["job"]["id"]
    assert first.json()["job"]["scope"] == first_payload["scope"]
    assert not any(key.startswith("_") for key in first.json()["job"]["scope"])

    replay = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={**api_harness.csrf_headers, "Idempotency-Key": first_key},
        json=first_payload,
    )
    assert replay.status_code == 202
    assert replay.json() == first.json()

    conflict = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={**api_harness.csrf_headers, "Idempotency-Key": first_key},
        json={"scope": {"type": "repository", "repository_id": repository_ids[1]}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    second_key = str(uuid.uuid4())
    merged = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={**api_harness.csrf_headers, "Idempotency-Key": second_key},
        json={"scope": {"type": "repository", "repository_id": repository_ids[1]}},
    )
    assert merged.status_code == 202, merged.text
    assert merged.json()["merged"] is True
    assert merged.json()["job"]["id"] == queued_id
    assert merged.json()["job"]["scope"] == {"type": "all"}

    with api_harness.sessions() as db:
        assert db.scalar(select(func.count(BackupJob.id)).where(BackupJob.active_slot == 1)) == 1
        assert db.scalar(select(func.count(BackupJob.id)).where(BackupJob.pending_slot == 1)) == 1
        assert db.scalar(select(func.count(BackupJob.id))) == 2
        queued = db.get(BackupJob, queued_id)
        assert queued is not None
        assert queued.status == "queued"
        assert queued.active_slot is None
        assert queued.pending_slot == 1
        assert set(queued.scope["_target_repository_ids"]) == set(repository_ids)
        assert (
            db.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.path == "/api/v1/backup-jobs"
                )
            )
            == 2
        )
        triggers = db.scalars(select(JobTrigger).order_by(JobTrigger.created_at.asc())).all()
        assert [trigger.status for trigger in triggers] == ["accepted", "merged"]


def test_backup_job_estimate_and_selected_repository_quota_gate(
    api_harness: ApiHarness,
) -> None:
    repository_ids, credential_ids = _seed_repository_credentials(
        api_harness,
        repository_count=2,
    )
    observed_at = datetime.now(UTC)
    with api_harness.sessions.begin() as db:
        credential = db.get(YuqueCredential, credential_ids[0])
        first_repository = db.get(Repository, repository_ids[0])
        second_repository = db.get(Repository, repository_ids[1])
        assert credential is not None and first_repository is not None and second_repository is not None
        credential.rate_limit_limit = 5000
        credential.rate_limit_remaining = 4
        credential.rate_limit_observed_at = observed_at
        first_repository.safe_watermark = observed_at
        second_repository.safe_watermark = observed_at
        db.add_all(
            [
                Document(
                    repository_id=first_repository.id,
                    yuque_doc_id=f"estimate-doc-{index}",
                    type="Doc",
                    title=f"Estimate {index}",
                    path=f"/estimate/{index}",
                    original_path=f"/estimate/{index}",
                )
                for index in range(2)
            ]
        )

    scope = {
        "type": "repositories",
        "credential_id": credential_ids[0],
        "repository_ids": repository_ids,
    }
    estimate = api_harness.client.post(
        "/api/v1/backup-jobs/estimate",
        headers=api_harness.csrf_headers,
        json={"scope": scope},
    )
    assert estimate.status_code == 200, estimate.text
    payload = estimate.json()
    assert payload["is_precise"] is False
    assert payload["repository_count"] == 2
    assert payload["document_count"] == 2
    assert payload["estimated_api_calls"] == 10
    assert payload["credentials"][0]["rate_limit_remaining"] == 4
    assert payload["credentials"][0]["snapshot_fresh"] is True
    assert payload["credentials"][0]["sufficient"] is False

    blocked = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={**api_harness.csrf_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"scope": scope},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "RATE_LIMIT_INSUFFICIENT"

    with api_harness.sessions.begin() as db:
        credential = db.get(YuqueCredential, credential_ids[0])
        assert credential is not None
        credential.rate_limit_remaining = 100

    accepted_scope = {**scope, "repository_ids": [repository_ids[0]]}
    accepted = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={**api_harness.csrf_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"scope": accepted_scope},
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["job"]["scope"] == accepted_scope
    with api_harness.sessions() as db:
        job = db.get(BackupJob, accepted.json()["job"]["id"])
        assert job is not None
        assert job.scope["_target_repository_ids"] == [repository_ids[0]]


def test_asset_download_enforces_auth_range_gone_not_found_and_path_boundary(
    api_harness: ApiHarness,
) -> None:
    repository_id = str(uuid.uuid4())
    source_job_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    purged_version_id = str(uuid.uuid4())
    misplaced_version_id = str(uuid.uuid4())
    available_asset_id = str(uuid.uuid4())
    purged_asset_id = str(uuid.uuid4())
    unsafe_asset_id = str(uuid.uuid4())
    misplaced_asset_id = str(uuid.uuid4())
    content_path = api_harness.settings.data_root / "content" / "assets" / "blob.bin"
    content_path.parent.mkdir(parents=True)
    content_path.write_bytes(b"0123456789")
    outside_path = api_harness.settings.data_root.parent / "outside.bin"
    outside_path.write_bytes(b"must-not-be-served")
    misplaced_path = api_harness.settings.data_root / "db" / "must-not-be-served.bin"
    misplaced_path.parent.mkdir(parents=True, exist_ok=True)
    misplaced_path.write_bytes(b"data-root-is-not-content-root")
    now = datetime.now(UTC)

    with api_harness.sessions.begin() as db:
        db.add(
            Repository(
                id=repository_id,
                normalized_base_url="https://www.yuque.com",
                yuque_book_id="download-book",
                name="download-repository",
                selected=True,
            )
        )
        db.add(
            BackupJob(
                id=source_job_id,
                trigger="manual",
                scope={"type": "repository", "repository_id": repository_id},
                status="succeeded",
                active_slot=None,
                pending_slot=None,
                finished_at=now,
            )
        )
        db.flush()
        db.add(
            Document(
                id=document_id,
                repository_id=repository_id,
                yuque_doc_id="download-document",
                type="Doc",
                title="unsafe/name",
                path="/unsafe/name",
                original_path="/unsafe/name",
            )
        )
        db.flush()
        db.add_all(
            [
                DocumentVersion(
                    id=version_id,
                    document_id=document_id,
                    content_hash="a" * 64,
                    completeness="complete",
                    raw_response_path="content/assets/blob.bin",
                    source_job_id=source_job_id,
                ),
                DocumentVersion(
                    id=purged_version_id,
                    document_id=document_id,
                    content_hash="b" * 64,
                    completeness="complete",
                    source_job_id=source_job_id,
                    purged_at=now,
                ),
                DocumentVersion(
                    id=misplaced_version_id,
                    document_id=document_id,
                    content_hash="f" * 64,
                    completeness="complete",
                    raw_response_path="db/must-not-be-served.bin",
                    source_job_id=source_job_id,
                ),
                Asset(
                    id=available_asset_id,
                    sha256="c" * 64,
                    size=10,
                    mime_type="application/octet-stream",
                    storage_path="content/assets/blob.bin",
                ),
                Asset(
                    id=purged_asset_id,
                    sha256="d" * 64,
                    size=10,
                    mime_type="application/octet-stream",
                    storage_path=None,
                    purged_at=now,
                ),
                Asset(
                    id=unsafe_asset_id,
                    sha256="e" * 64,
                    size=18,
                    mime_type="application/octet-stream",
                    storage_path="../outside.bin",
                ),
                Asset(
                    id=misplaced_asset_id,
                    sha256="1" * 64,
                    size=29,
                    mime_type="application/octet-stream",
                    storage_path="db/must-not-be-served.bin",
                ),
            ]
        )
        db.flush()
        db.add(
            VersionAsset(
                version_id=version_id,
                asset_id=available_asset_id,
                original_url="https://cdn.example.invalid/blob.bin",
                normalized_url="https://cdn.example.invalid/blob.bin",
                name="../../secret.txt",
                type="attachment",
                status="downloaded",
            )
        )

    with TestClient(api_harness.app) as anonymous:
        unauthorized = anonymous.get(f"/api/v1/assets/{available_asset_id}/download")
        assert unauthorized.status_code == 401

    ranged = api_harness.client.get(
        f"/api/v1/assets/{available_asset_id}/download",
        headers={"Range": "bytes=2-5"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert ranged.headers["content-range"] == "bytes 2-5/10"
    assert ranged.headers["accept-ranges"] == "bytes"
    disposition = ranged.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "../" not in disposition
    assert "\\" not in disposition

    malformed_range = api_harness.client.get(
        f"/api/v1/assets/{available_asset_id}/download",
        headers={"Range": "items=0-1"},
    )
    assert malformed_range.status_code == 422
    assert malformed_range.json()["code"] == "VALIDATION_ERROR"
    assert malformed_range.json()["field_errors"] == [
        {"field": "Range", "reason": "invalid"}
    ]
    unsatisfiable_range = api_harness.client.get(
        f"/api/v1/assets/{available_asset_id}/download",
        headers={"Range": "bytes=99-100"},
    )
    assert unsatisfiable_range.status_code == 422
    assert unsatisfiable_range.json()["code"] == "VALIDATION_ERROR"

    missing = api_harness.client.get(f"/api/v1/assets/{uuid.uuid4()}/download")
    assert missing.status_code == 404
    assert missing.json()["code"] == "ASSET_NOT_FOUND"
    purged = api_harness.client.get(f"/api/v1/assets/{purged_asset_id}/download")
    assert purged.status_code == 410
    assert purged.json()["code"] == "ASSET_CONTENT_PURGED"
    unsafe = api_harness.client.get(f"/api/v1/assets/{unsafe_asset_id}/download")
    assert unsafe.status_code == 503
    assert unsafe.json()["code"] == "SERVICE_UNAVAILABLE"
    misplaced_asset = api_harness.client.get(f"/api/v1/assets/{misplaced_asset_id}/download")
    assert misplaced_asset.status_code == 503
    assert misplaced_asset.json()["code"] == "SERVICE_UNAVAILABLE"
    purged_version = api_harness.client.get(
        f"/api/v1/documents/{document_id}/versions/{purged_version_id}/downloads/raw-response"
    )
    assert purged_version.status_code == 410
    assert purged_version.json()["code"] == "VERSION_CONTENT_PURGED"
    misplaced_version = api_harness.client.get(
        f"/api/v1/documents/{document_id}/versions/{misplaced_version_id}/downloads/raw-response"
    )
    assert misplaced_version.status_code == 503
    assert misplaced_version.json()["code"] == "SERVICE_UNAVAILABLE"


def test_manual_repository_merge_does_not_narrow_queued_cron_targets(
    api_harness: ApiHarness,
) -> None:
    repository_ids, _credential_ids = _seed_repository_credentials(
        api_harness,
        repository_count=2,
    )
    coordinator = JobCoordinator(api_harness.sessions, PersistentQueue(api_harness.sessions))
    cron_job_id = coordinator.enqueue_cron_job(idempotency_key="cron:2026-07-23T12:00:00Z")

    with api_harness.sessions() as db:
        queued = db.get(BackupJob, cron_job_id)
        assert queued is not None
        assert set(queued.scope["_target_repository_ids"]) == set(repository_ids)

    merged = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={
            **api_harness.csrf_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={"scope": {"type": "repository", "repository_id": repository_ids[0]}},
    )
    assert merged.status_code == 202, merged.text
    assert merged.json()["merged"] is True
    assert merged.json()["job"]["id"] == cron_job_id

    assert coordinator.promote_pending_job() == cron_job_id
    with api_harness.sessions() as db:
        subtasks = db.scalars(
            select(BackupSubtask).where(BackupSubtask.job_id == cron_job_id)
        ).all()
        assert {subtask.repository_id for subtask in subtasks} == set(repository_ids)


def test_request_body_uuid_validation_and_repository_nullable_fields(
    api_harness: ApiHarness,
) -> None:
    repository_id = str(uuid.uuid4())
    with api_harness.sessions.begin() as db:
        db.add(
            Repository(
                id=repository_id,
                normalized_base_url="https://www.yuque.com",
                yuque_book_id="nullable-fields-book",
                name="nullable-fields-repository",
                selected=True,
            )
        )

    invalid_scope = api_harness.client.post(
        "/api/v1/backup-jobs",
        headers={
            **api_harness.csrf_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={"scope": {"type": "credential", "credential_id": "not-a-uuid"}},
    )
    assert invalid_scope.status_code == 422
    assert invalid_scope.json()["code"] == "VALIDATION_ERROR"

    invalid_primary = api_harness.client.put(
        f"/api/v1/repositories/{repository_id}/primary-credential",
        headers=api_harness.csrf_headers,
        json={"credential_id": "not-a-uuid"},
    )
    assert invalid_primary.status_code == 422
    assert invalid_primary.json()["code"] == "VALIDATION_ERROR"

    listed = api_harness.client.get("/api/v1/repositories", params={"q": "nullable-fields"})
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    for field in (
        "slug",
        "namespace",
        "primary_credential_id",
        "last_success_at",
        "content_updated_at",
    ):
        assert field in item
        assert item[field] is None
