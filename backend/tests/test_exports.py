from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.models import (
    BackupJob,
    Base,
    Document,
    DocumentVersion,
    Repository,
    RetentionPolicy,
    TocItem,
)
from app.modules.exports import ExportService, normalize_document_markdown
from app.modules.exports.service import sanitize_path_segment


def test_normalizes_yuque_documents_without_localizing_images() -> None:
    markdown = normalize_document_markdown(
        {
            "type": "Doc",
            "format": "lake",
            "body": "[TOC]\n\n## 安装\n\n![架构图](https://cdn.example/architecture.png)",
            "body_lake": "<!doctype lake><p>not the export source</p>",
        },
        title="部署说明",
    )

    assert "[TOC]" not in markdown
    assert "## 安装" in markdown
    assert "https://cdn.example/architecture.png" in markdown
    assert "doctype lake" not in markdown


def test_converts_html_tables_and_boards_to_readable_markdown() -> None:
    html = normalize_document_markdown(
        {"type": "HtmlDoc", "body_html": "<h2>标题</h2><p><strong>正文</strong></p>"},
        title="HTML 文档",
    )
    table = normalize_document_markdown(
        {"type": "Table", "body_table": [{"姓名": "Alice", "状态": "正常"}]},
        title="成员",
    )
    board = normalize_document_markdown(
        {"type": "Board", "body": {"nodes": [{"id": 1}]}},
        title="画板",
    )

    assert "## 标题" in html and "**正文**" in html
    assert "| 姓名 | 状态 |" in table and "| Alice | 正常 |" in table
    assert "```json" in board and '"nodes"' in board


def test_backfills_markdown_and_builds_readable_latest_and_initial_snapshot(tmp_path: Path) -> None:
    settings = Settings(app_master_key="6d" * 32, data_root=tmp_path)
    engine = create_engine(f"sqlite:///{(tmp_path / 'exports.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    with sessions.begin() as session:
        repository = Repository(
            id="repo-1",
            normalized_base_url="https://www.yuque.com",
            yuque_book_id="book-1",
            name="平台知识库",
        )
        group = TocItem(
            id="toc-group",
            repository_id=repository.id,
            remote_id="group-1",
            type="TITLE",
            title="部署手册",
            path="/部署手册",
        )
        article = TocItem(
            id="toc-article",
            repository_id=repository.id,
            remote_id="doc-remote",
            parent_remote_id=group.remote_id,
            yuque_doc_id="doc-remote",
            type="DOC",
            title="安装指南",
            path="/部署手册/安装指南",
        )
        document = Document(
            id="doc-1",
            repository_id=repository.id,
            yuque_doc_id="doc-remote",
            type="Doc",
            title="安装指南",
            path="/部署手册/安装指南",
            original_path="/部署手册/安装指南",
            toc_item_id=article.id,
        )
        job = BackupJob(
            id="job-12345678",
            trigger="manual",
            scope={"type": "all"},
            status="succeeded",
            finished_at=now,
        )
        version = DocumentVersion(
            id="version-1",
            document_id=document.id,
            format="lake",
            content_hash="a" * 64,
            completeness="complete",
            raw_response_path="content/versions/repo-1/doc-1/hash/raw-response.json",
            content_size_bytes=10,
            normalized_metadata={
                "type": "Doc",
                "title": "安装指南",
                "body": "# 安装指南\n\n执行安装。\n\n![图](https://cdn.example/a.png)",
            },
            source_job_id=job.id,
            created_at=now,
        )
        document.latest_successful_version_id = version.id
        session.add_all([
            repository,
            group,
            article,
            document,
            job,
            version,
            RetentionPolicy(id=1, retention_days=15),
        ])

    service = ExportService(sessions, settings, now=lambda: now)
    assert service.prepare(create_initial_snapshot=True) == 1

    latest = settings.exports_root / "latest" / "平台知识库" / "部署手册" / "安装指南.md"
    assert latest.read_text(encoding="utf-8").endswith(
        "![图](https://cdn.example/a.png)\n"
    )
    snapshots = list((settings.exports_root / "snapshots").iterdir())
    assert len(snapshots) == 1
    manifest = json.loads((snapshots[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["images_downloaded"] is False
    assert manifest["documents"][0]["path"] == "平台知识库/部署手册/安装指南.md"
    with sessions() as session:
        stored = session.scalar(select(DocumentVersion).where(DocumentVersion.id == "version-1"))
        assert stored is not None and stored.markdown_path is not None
        assert (settings.data_root / stored.markdown_path).is_file()


def test_snapshot_retention_and_windows_path_safety(tmp_path: Path) -> None:
    settings = Settings(app_master_key="6e" * 32, data_root=tmp_path)
    engine = create_engine(f"sqlite:///{(tmp_path / 'retention.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    with sessions.begin() as session:
        session.add(RetentionPolicy(id=1, retention_days=15))
    old = settings.exports_root / "snapshots" / "old"
    old.mkdir(parents=True)
    timestamp = (now - timedelta(days=16)).timestamp()
    os.utime(old, (timestamp, timestamp))

    assert ExportService(sessions, settings, now=lambda: now).prune_snapshots() == 1
    assert sanitize_path_segment("CON", "fallback") == "_CON"
    assert sanitize_path_segment("a<b>:c?. ", "fallback") == "a_b_c_"
