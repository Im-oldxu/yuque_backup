from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import (
    BackupIssue,
    BackupJob,
    BackupSubtask,
    Base,
    Document,
    DocumentVersion,
    QueueItem,
    Repository,
    RepositoryCredential,
    VersionAsset,
    YuqueCredential,
)
from app.core.security import encrypt_token
from app.worker.service import WorkerService


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_master_key="22" * 32,
        data_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'table.sqlite3'}",
        yuque_request_interval_seconds=0,
    )


def _sessions(settings: Settings) -> sessionmaker[Session]:
    assert settings.database_url is not None
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed_document_sync(
    sessions: sessionmaker[Session], settings: Settings, now: datetime
) -> tuple[str, str, str, str]:
    credential_id = "89898989-8989-4989-8989-898989898989"
    encrypted, nonce = encrypt_token("token", credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="table-reader",
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
            yuque_book_id="table-book",
            name="Tables",
            selected=True,
        )
        job = BackupJob(
            trigger="manual",
            scope={"type": "repository"},
            status="running",
            active_slot=1,
            started_at=now,
        )
        session.add_all([credential, repository, job])
        session.flush()
        session.add(
            RepositoryCredential(
                repository_id=repository.id,
                credential_id=credential.id,
                is_primary=True,
            )
        )
        subtask = BackupSubtask(
            job_id=job.id,
            repository_id=repository.id,
            credential_id=credential.id,
            status="running",
            document_total=1,
            started_at=now,
        )
        document = Document(
            repository_id=repository.id,
            yuque_doc_id="table-doc",
            title="Inventory",
            slug="inventory",
            type="Table",
            path="/inventory",
            original_path="/inventory",
        )
        session.add_all([subtask, document])
        session.flush()
        queue_item = QueueItem(
            category="document_sync",
            payload={},
            available_at=now,
            idempotency_key=f"job:{job.id}:document:{document.id}",
            job_id=job.id,
            subtask_id=subtask.id,
            credential_id=credential.id,
            repository_id=repository.id,
            document_id=document.id,
        )
        session.add(queue_item)
        session.flush()
        return document.id, subtask.id, queue_item.id, job.id


def _table_page(page: int, records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": "table-doc",
        "title": "Inventory",
        "slug": "inventory",
        "type": "Table",
        "format": "json",
        "latest_version_id": "table-v1",
        "body_table": {
            "meta": {
                "totalCount": 401,
                "columns": [{"key": "name"}, {"key": "file"}],
            },
            "page": page,
            "records": records,
        },
    }


async def _run_table_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[Settings, sessionmaker[Session], str, str, str, httpx.AsyncClient]:
    settings = _settings(tmp_path)
    sessions = _sessions(settings)
    now = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    document_id, subtask_id, queue_item_id, _job_id = _seed_document_sync(
        sessions, settings, now
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = WorkerService(
        sessions,
        settings,
        worker_id="table-worker",
        yuque_http_client=client,
        resource_http_client=client,
        now=lambda: now,
    )

    async def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(service.asset_downloader, "_resolver", public_resolver)
    assert await service.run_once() is True
    await service.aclose()
    return settings, sessions, document_id, subtask_id, queue_item_id, client


@pytest.mark.asyncio
async def test_table_pagination_persists_every_page_without_downloading_image_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_pages: list[int] = []
    page_records: dict[int, list[dict[str, object]]] = {
        1: [{"name": "row-one", "file": None}],
        2: [
            {
                "name": "row-two",
                "file": {
                    "name": "late.png",
                    "src": "https://assets.example/late.png",
                    "size": 10,
                },
            }
        ],
        3: [{"name": "row-three", "file": None}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/repos/docs/table-doc"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        return httpx.Response(
            200,
            json={"data": _table_page(page, page_records[page])},
            headers={"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": str(5000 - page)},
        )

    settings, sessions, document_id, subtask_id, queue_item_id, client = await _run_table_sync(
        tmp_path, monkeypatch, handler
    )
    try:
        assert requested_pages == [1, 2, 3]
        with sessions() as session:
            document = session.get(Document, document_id)
            version = session.scalar(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            )
            subtask = session.get(BackupSubtask, subtask_id)
            queue_item = session.get(QueueItem, queue_item_id)
            assert document is not None and version is not None
            references = list(
                session.scalars(select(VersionAsset).where(VersionAsset.version_id == version.id))
            )
            assert document.latest_successful_version_id == version.id
            assert version.completeness == "complete"
            assert version.issue_count == 0
            assert version.resource_total == 0
            assert version.resource_downloaded == 0
            assert subtask is not None and subtask.document_succeeded == 1
            assert queue_item is not None and queue_item.status == "succeeded"
            assert references == []
            assert version.raw_response_path is not None
            assert version.raw_body_path is not None
            assert version.preview_path is not None
            raw_response_path = settings.data_root / version.raw_response_path
            raw_body_path = settings.data_root / version.raw_body_path
            preview_path = settings.data_root / version.preview_path

        raw_response = json.loads(raw_response_path.read_text(encoding="utf-8"))
        raw_body = json.loads(raw_body_path.read_text(encoding="utf-8"))
        preview = preview_path.read_text(encoding="utf-8")
        assert [page["data"]["body_table"]["page"] for page in raw_response["pages"]] == [
            1,
            2,
            3,
        ]
        assert [page["page"] for page in raw_body["pages"]] == [1, 2, 3]
        assert [record["name"] for record in raw_body["body_table"]["records"]] == [
            "row-one",
            "row-two",
            "row-three",
        ]
        assert all(name in preview for name in ("row-one", "row-two", "row-three", "late.png"))
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_table_later_page_failure_commits_retrieved_pages_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/repos/docs/table-doc"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": str(5000 - page),
        }
        if page == 3:
            return httpx.Response(503, json={"message": "temporary"}, headers=headers)
        return httpx.Response(
            200,
            json={"data": _table_page(page, [{"name": f"retained-{page}"}])},
            headers=headers,
        )

    settings, sessions, document_id, subtask_id, queue_item_id, client = await _run_table_sync(
        tmp_path, monkeypatch, handler
    )
    try:
        assert requested_pages == [1, 2, 3]
        with sessions() as session:
            document = session.get(Document, document_id)
            version = session.scalar(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            )
            assert document is not None and version is not None
            issue = session.scalar(
                select(BackupIssue).where(BackupIssue.version_id == version.id)
            )
            subtask = session.get(BackupSubtask, subtask_id)
            queue_item = session.get(QueueItem, queue_item_id)
            assert document.latest_successful_version_id == version.id
            assert version.completeness == "partial"
            assert version.issue_count == 1
            assert issue is not None
            assert issue.level == "warning"
            assert issue.code == "TABLE_PAGE_FETCH_FAILED"
            assert issue.http_status == 503
            assert "page 3 of 3" in issue.message
            assert "2 page(s) were retained" in issue.message
            assert subtask is not None and subtask.document_partial == 1
            assert subtask.issue_count == 1
            assert queue_item is not None and queue_item.status == "succeeded"
            assert version.raw_response_path is not None
            assert version.raw_body_path is not None
            assert version.preview_path is not None
            raw_response_path = settings.data_root / version.raw_response_path
            raw_body_path = settings.data_root / version.raw_body_path
            preview_path = settings.data_root / version.preview_path

        raw_response = json.loads(raw_response_path.read_text(encoding="utf-8"))
        raw_body = json.loads(raw_body_path.read_text(encoding="utf-8"))
        preview = preview_path.read_text(encoding="utf-8")
        assert len(raw_response["pages"]) == 2
        assert [page["page"] for page in raw_body["pages"]] == [1, 2]
        assert [record["name"] for record in raw_body["body_table"]["records"]] == [
            "retained-1",
            "retained-2",
        ]
        assert "retained-1" in preview
        assert "retained-2" in preview
    finally:
        await client.aclose()
