from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

os.environ.setdefault("APP_MASTER_KEY", "00" * 32)
os.environ.setdefault("DATA_ROOT", str(Path(os.environ.get("TEMP", ".")) / "yuque-backup-test-bootstrap"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_admin, require_csrf_admin
from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.core.models import (
    Admin,
    AppSetting,
    Asset,
    BackupJob,
    Base,
    DeletionTombstone,
    Document,
    DocumentVersion,
    Repository,
    RetentionPolicy,
    WorkerHeartbeat,
    YuqueCredential,
)
from app.modules.dashboard.router import router as dashboard_router
from app.modules.settings.router import router as settings_router
from app.modules.settings.service import calculate_next_runs
from app.modules.tombstones.router import router as tombstones_router

MASTER_KEY = "11" * 32


@pytest.fixture
def api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, dict[str, str]]]:
    database_path = tmp_path / "db" / "yuque-backup.sqlite3"
    database_path.parent.mkdir(parents=True)
    test_settings = Settings(
        app_master_key=MASTER_KEY,
        data_root=tmp_path,
        database_url=f"sqlite:///{database_path.as_posix()}",
        worker_heartbeat_seconds=10,
    )
    database_url = test_settings.database_url
    assert database_url is not None
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    now = datetime.now(UTC)
    ids = {
        "repository": str(uuid4()),
        "document": str(uuid4()),
        "source_job": str(uuid4()),
        "running_job": str(uuid4()),
        "queued_job": str(uuid4()),
        "partial_job": str(uuid4()),
        "failed_job": str(uuid4()),
        "version": str(uuid4()),
        "asset": str(uuid4()),
        "credential": str(uuid4()),
        "tombstone_new": str(uuid4()),
        "tombstone_old": str(uuid4()),
    }
    with session_factory.begin() as db:
        db.add_all(
            [
                AppSetting(
                    id=1,
                    cron="0 2 * * *",
                    timezone="Asia/Shanghai",
                    schedule_enabled=True,
                    max_asset_size_bytes=524_288_000,
                    version=1,
                    updated_at=now,
                ),
                RetentionPolicy(id=1, retention_days=15, updated_at=now),
                Repository(
                    id=ids["repository"],
                    normalized_base_url="https://www.yuque.com",
                    yuque_book_id="book-1",
                    name="测试知识库",
                    selected=True,
                ),
                BackupJob(
                    id=ids["source_job"],
                    trigger="cron",
                    scope={"type": "all"},
                    status="succeeded",
                    pending_slot=None,
                    finished_at=now - timedelta(hours=1),
                ),
                BackupJob(
                    id=ids["running_job"],
                    trigger="manual",
                    scope={"type": "all", "_target_repository_ids": [ids["repository"]]},
                    status="running",
                    active_slot=1,
                    pending_slot=None,
                    progress=0.5,
                    document_total=2,
                    document_succeeded=1,
                    cancel_requested_at=now,
                ),
                BackupJob(
                    id=ids["queued_job"],
                    trigger="manual",
                    scope={"type": "repository", "repository_id": ids["repository"]},
                    status="queued",
                    pending_slot=1,
                ),
                BackupJob(
                    id=ids["partial_job"],
                    trigger="cron",
                    scope={"type": "all"},
                    status="partial",
                    pending_slot=None,
                    finished_at=now - timedelta(days=1),
                ),
                BackupJob(
                    id=ids["failed_job"],
                    trigger="cron",
                    scope={"type": "all"},
                    status="failed",
                    pending_slot=None,
                    finished_at=now - timedelta(days=2),
                ),
                WorkerHeartbeat(
                    id=1,
                    worker_id="worker-test",
                    last_heartbeat_at=now,
                    started_at=now - timedelta(minutes=5),
                ),
                YuqueCredential(
                    id=ids["credential"],
                    name="等待额度凭据",
                    base_url="https://www.yuque.com",
                    encrypted_token=b"ciphertext",
                    token_nonce=b"0" * 12,
                    token_suffix="abcd",
                    status="waiting_quota",
                    enabled=True,
                ),
            ]
        )
        db.flush()
        db.add(
            Document(
                id=ids["document"],
                repository_id=ids["repository"],
                yuque_doc_id="doc-1",
                type="Doc",
                title="测试文档",
            )
        )
        db.flush()
        db.add_all(
            [
                DocumentVersion(
                    id=ids["version"],
                    document_id=ids["document"],
                    content_hash="a" * 64,
                    completeness="complete",
                    content_size_bytes=1_000,
                    source_job_id=ids["source_job"],
                ),
                Asset(
                    id=ids["asset"],
                    sha256="b" * 64,
                    size=2_000,
                    mime_type="image/png",
                    storage_path="content/assets/test",
                ),
                DeletionTombstone(
                    id=ids["tombstone_old"],
                    base_url="https://www.yuque.com",
                    repository_id=ids["repository"],
                    yuque_book_id="book-1",
                    yuque_doc_id="deleted-old",
                    title="旧文档 100%",
                    original_path="/归档/旧文档_100%",
                    deleted_at=now - timedelta(days=4),
                    purged_at=now - timedelta(days=1),
                    source_job_id=ids["source_job"],
                    cleanup_job_id=str(uuid4()),
                ),
                DeletionTombstone(
                    id=ids["tombstone_new"],
                    base_url="https://www.yuque.com",
                    repository_id=ids["repository"],
                    yuque_book_id="book-1",
                    yuque_doc_id="deleted-new",
                    title="最近删除文档",
                    original_path="/项目/最近删除文档",
                    deleted_at=now - timedelta(days=2),
                    purged_at=now,
                    source_job_id=ids["source_job"],
                    cleanup_job_id=str(uuid4()),
                ),
            ]
        )

    monkeypatch.setattr("app.modules.settings.service.get_settings", lambda: test_settings)
    monkeypatch.setattr("app.modules.dashboard.service.get_settings", lambda: test_settings)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(settings_router)
    app.include_router(dashboard_router)
    app.include_router(tombstones_router)

    admin = Admin(id=str(uuid4()), username="admin", password_hash="unused")

    def override_db() -> Generator[Session]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_admin] = lambda: admin
    app.dependency_overrides[require_csrf_admin] = lambda: admin

    with TestClient(app) as client:
        yield client, ids

    Base.metadata.drop_all(engine)
    engine.dispose()


def _assert_error(response: Any, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["code"] == code
    assert body["request_id"].startswith("req_")


def test_calculate_next_runs_are_utc_and_respect_timezone() -> None:
    runs = calculate_next_runs(
        "0 2 * * *",
        "Asia/Shanghai",
        now=datetime(2026, 7, 23, 14, 30, tzinfo=UTC),
    )

    assert runs == [
        datetime(2026, 7, 23, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 24, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 25, 18, 0, tzinfo=UTC),
    ]


def test_schedule_routes_validate_and_return_three_utc_runs(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, _ = api

    response = client.get("/api/v1/settings/schedule")
    assert response.status_code == 200
    assert response.json()["cron"] == "0 2 * * *"
    assert len(response.json()["next_runs"]) == 3
    assert all(value.endswith("Z") for value in response.json()["next_runs"])

    response = client.put(
        "/api/v1/settings/schedule",
        json={"cron": "30 6 * * 1", "timezone": "UTC"},
    )
    assert response.status_code == 200
    assert response.json()["cron"] == "30 6 * * 1"
    assert response.json()["timezone"] == "UTC"
    assert len(response.json()["next_runs"]) == 3

    _assert_error(
        client.put(
            "/api/v1/settings/schedule",
            json={"cron": "not-a-cron", "timezone": "UTC"},
        ),
        422,
        "INVALID_CRON",
    )
    _assert_error(
        client.put(
            "/api/v1/settings/schedule",
            json={"cron": "0 2 * * *", "timezone": "Not/A_Real_Zone"},
        ),
        422,
        "INVALID_TIMEZONE",
    )
    _assert_error(
        client.put(
            "/api/v1/settings/schedule",
            json={"cron": "0 2 * * *", "timezone": "UTC", "enabled": False},
        ),
        422,
        "VALIDATION_ERROR",
    )


def test_retention_and_storage_routes_are_strict_and_use_db_aggregates(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, _ = api

    response = client.get("/api/v1/settings/retention")
    assert response.status_code == 200
    assert response.json()["retention_days"] == 15

    response = client.put("/api/v1/settings/retention", json={"retention_days": 30})
    assert response.status_code == 200
    assert response.json()["retention_days"] == 30

    _assert_error(
        client.put("/api/v1/settings/retention", json={"retention_days": "30"}),
        422,
        "VALIDATION_ERROR",
    )
    _assert_error(
        client.put("/api/v1/settings/retention", json={"retention_days": 0}),
        422,
        "VALIDATION_ERROR",
    )

    response = client.get("/api/v1/settings/storage")
    assert response.status_code == 200
    storage = response.json()
    assert storage["usage"]["version_bytes"] == 1_000
    assert storage["usage"]["asset_bytes"] == 2_000
    assert storage["usage"]["database_bytes"] > 0
    assert storage["usage"]["total_bytes"] == storage["usage"]["database_bytes"] + 3_000
    assert storage["max_asset_size_unlimited"] is False

    response = client.put("/api/v1/settings/storage-limit", json={"max_asset_size_bytes": None})
    assert response.status_code == 200
    assert response.json()["max_asset_size_bytes"] is None
    assert response.json()["max_asset_size_unlimited"] is True

    _assert_error(
        client.put("/api/v1/settings/storage-limit", json={"max_asset_size_bytes": -1}),
        422,
        "VALIDATION_ERROR",
    )
    _assert_error(
        client.put("/api/v1/settings/storage-limit", json={}),
        422,
        "VALIDATION_ERROR",
    )
    _assert_error(
        client.put(
            "/api/v1/settings/storage-limit",
            json={"max_asset_size_bytes": 100, "content_path": "/tmp/other"},
        ),
        422,
        "VALIDATION_ERROR",
    )


def test_dashboard_summary_uses_database_counts_and_worker_heartbeat(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, ids = api

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    summary = response.json()
    assert summary["current_job"]["id"] == ids["running_job"]
    assert summary["current_job"]["scope"] == {"type": "all"}
    assert summary["current_job"]["can_cancel"] is False
    assert summary["job_counts"] == {"succeeded": 1, "partial": 1, "failed": 1}
    assert summary["waiting_quota_credentials"] == 1
    assert summary["repositories"] == 1
    assert summary["documents"] == 1
    assert summary["versions"] == 1
    assert summary["storage"]["content_bytes"] == 3_000
    assert summary["storage"]["asset_bytes"] == 2_000
    assert summary["worker"]["status"] == "online"
    assert summary["schedule"]["next_run_at"].endswith("Z")


def test_tombstone_routes_filter_paginate_escape_like_and_return_specific_404(
    api: tuple[TestClient, dict[str, str]],
) -> None:
    client, ids = api

    response = client.get("/api/v1/deletion-tombstones", params={"page": 1, "page_size": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["id"] == ids["tombstone_new"]
    assert body["items"][0]["repository"] == {"id": ids["repository"], "name": "测试知识库"}

    response = client.get("/api/v1/deletion-tombstones", params={"q": "100%"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [ids["tombstone_old"]]

    response = client.get(f"/api/v1/deletion-tombstones/{ids['tombstone_new']}")
    assert response.status_code == 200
    assert response.json()["yuque_doc_id"] == "deleted-new"

    _assert_error(
        client.get(f"/api/v1/deletion-tombstones/{uuid4()}"),
        404,
        "TOMBSTONE_NOT_FOUND",
    )
    _assert_error(
        client.get(
            "/api/v1/deletion-tombstones",
            params={"deleted_from": "2026-07-20T00:00:00"},
        ),
        422,
        "VALIDATION_ERROR",
    )
    _assert_error(
        client.get("/api/v1/deletion-tombstones", params={"page_size": 101}),
        422,
        "VALIDATION_ERROR",
    )
