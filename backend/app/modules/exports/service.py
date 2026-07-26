from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from markdownify import markdownify as html_to_markdown
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import (
    BackupJob,
    BackupSubtask,
    Document,
    DocumentVersion,
    Repository,
    RetentionPolicy,
    TocItem,
)
from app.storage.content import ContentStore, canonical_json_bytes

_TOC_LINE = re.compile(r"(?im)^\s*\[toc\]\s*$")
_MARKDOWN_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]+')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def normalize_document_markdown(document: dict[str, Any], *, title: str) -> str:
    """Convert a redacted Yuque document payload into portable Markdown."""
    document_type = str(document.get("type") or "unknown")
    body = document.get("body")
    body_html = document.get("body_html")

    if document_type in {"Sheet", "Table"}:
        field = "body_sheet" if document_type == "Sheet" else "body_table"
        structured = document.get(field, body)
        markdown = _structured_markdown(structured, title=title, sheet=document_type == "Sheet")
    elif document_type == "Board":
        raw = body if body not in (None, "") else document.get("body_lake")
        markdown = _board_markdown(raw, title=title)
    elif document_type == "HtmlDoc" and isinstance(body_html, str) and body_html.strip():
        markdown = html_to_markdown(body_html, heading_style="ATX", bullets="-")
    elif isinstance(body, str) and body.strip():
        markdown = body
    elif isinstance(body_html, str) and body_html.strip():
        markdown = html_to_markdown(body_html, heading_style="ATX", bullets="-")
    else:
        raw = _first_body(document)
        markdown = raw if isinstance(raw, str) else _json_text(raw)

    markdown = _TOC_LINE.sub("", markdown).strip()
    if not _MARKDOWN_HEADING.search(markdown):
        heading = f"# {title.strip() or '未命名文章'}"
        markdown = f"{heading}\n\n{markdown}" if markdown else heading
    return f"{markdown.rstrip()}\n"


def markdown_for_version(version: DocumentVersion, document: Document, settings: Settings) -> str:
    if version.purged_at is not None:
        raise FileNotFoundError("version content was purged")
    if version.markdown_path:
        candidate = (settings.data_root / version.markdown_path).resolve()
        if candidate.is_relative_to(settings.content_root.resolve()) and candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    metadata = dict(version.normalized_metadata or {})
    metadata.setdefault("type", document.type)
    metadata.setdefault("title", document.title)
    return normalize_document_markdown(metadata, title=document.title)


class ExportService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        store: ContentStore | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._store = store or ContentStore(settings.data_root)
        self._now = now or (lambda: datetime.now(UTC))
        self._settings.ensure_export_directories()

    def prepare(self, *, create_initial_snapshot: bool = False) -> int:
        backfilled = self.backfill_markdown()
        self.rebuild_latest()
        snapshots = self._settings.exports_root / "snapshots"
        has_snapshots = snapshots.exists() and any(snapshots.iterdir())
        if create_initial_snapshot and not has_snapshots:
            self._create_backfill_snapshot()
        self.prune_snapshots()
        return backfilled

    def backfill_markdown(self) -> int:
        with self._session_factory() as session:
            rows = list(
                session.execute(
                    select(DocumentVersion, Document)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(DocumentVersion.purged_at.is_(None))
                    .order_by(DocumentVersion.created_at.asc())
                ).tuples()
            )
        backfilled = 0
        for version, document in rows:
            if version.markdown_path and self._stored_markdown_exists(version.markdown_path):
                continue
            relative_path = self._version_markdown_path(version)
            if relative_path is None:
                continue
            markdown = markdown_for_version(version, document, self._settings)
            target = (self._settings.data_root / relative_path).resolve()
            if not target.is_relative_to(self._settings.content_root.resolve()):
                continue
            _atomic_write(target, markdown.encode("utf-8"))
            with self._session_factory.begin() as session:
                current = session.get(DocumentVersion, version.id)
                if current is None or current.purged_at is not None:
                    target.unlink(missing_ok=True)
                    continue
                current.markdown_path = relative_path
                current.content_size_bytes += len(markdown.encode("utf-8"))
            backfilled += 1
        return backfilled

    def rebuild_latest(self) -> None:
        target = self._settings.exports_root / "latest"
        staging = self._new_staging("latest")
        try:
            self._build_tree(staging, repository_ids=None)
            _replace_directory(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def export_job(self, job_id: str) -> Path | None:
        self.backfill_markdown()
        with self._session_factory() as session:
            job = session.get(BackupJob, job_id)
            if job is None or job.status not in {"succeeded", "partial"} or job.finished_at is None:
                return None
            repository_ids = list(
                session.scalars(
                    select(BackupSubtask.repository_id).where(BackupSubtask.job_id == job_id)
                )
            )
            finished_at = _as_utc(job.finished_at)
        local_finished = finished_at.astimezone(ZoneInfo(self._settings.tz))
        folder = f"{local_finished:%Y-%m-%d_%H%M%S}-{job_id[:8]}"
        target = self._settings.exports_root / "snapshots" / folder
        if target.exists():
            self.rebuild_latest()
            return target
        staging = self._new_staging(f"snapshot-{job_id[:8]}")
        try:
            entries = self._build_tree(staging, repository_ids=set(repository_ids))
            manifest = {
                "schema_version": 1,
                "kind": "backup-job",
                "job_id": job_id,
                "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
                "images_downloaded": False,
                "documents": entries,
            }
            _atomic_write(staging / "manifest.json", canonical_json_bytes(manifest))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
            self.rebuild_latest()
            self.prune_snapshots()
            return target
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def prune_snapshots(self) -> int:
        with self._session_factory() as session:
            retention_days = session.scalar(
                select(RetentionPolicy.retention_days).where(RetentionPolicy.id == 1)
            ) or 15
        cutoff = _as_utc(self._now()) - timedelta(days=retention_days)
        snapshots = self._settings.exports_root / "snapshots"
        if not snapshots.exists():
            return 0
        removed = 0
        for path in snapshots.iterdir():
            if not path.is_dir():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified <= cutoff:
                shutil.rmtree(path)
                removed += 1
        return removed

    def _build_tree(self, root: Path, repository_ids: set[str] | None) -> list[dict[str, Any]]:
        root.mkdir(parents=True, exist_ok=True)
        with self._session_factory() as session:
            repository_query = select(Repository)
            if repository_ids is not None:
                repository_query = repository_query.where(Repository.id.in_(repository_ids))
            repositories = list(
                session.scalars(repository_query.order_by(Repository.name.asc(), Repository.id.asc()))
            )
            rows = list(
                session.execute(
                    select(Document, DocumentVersion)
                    .join(DocumentVersion, DocumentVersion.id == Document.latest_successful_version_id)
                    .where(
                        Document.deleted_at.is_(None),
                        DocumentVersion.purged_at.is_(None),
                        Document.repository_id.in_([repository.id for repository in repositories]),
                    )
                    .order_by(Document.repository_id.asc(), Document.path.asc(), Document.title.asc())
                ).tuples()
            ) if repositories else []
            toc_items = list(
                session.scalars(
                    select(TocItem).where(
                        TocItem.repository_id.in_([repository.id for repository in repositories])
                    )
                )
            ) if repositories else []

        repo_names = _unique_repository_names(repositories)
        toc_by_id = {item.id: item for item in toc_items}
        toc_by_remote = {(item.repository_id, item.remote_id): item for item in toc_items}
        used_paths: set[str] = set()
        entries: list[dict[str, Any]] = []
        for document, version in rows:
            repository = next(item for item in repositories if item.id == document.repository_id)
            directory = root / repo_names[repository.id]
            for group in _document_groups(document, toc_by_id, toc_by_remote):
                directory /= sanitize_path_segment(group, "未命名分组")
            filename = f"{sanitize_path_segment(document.title, '未命名文章')}.md"
            relative = (directory / filename).relative_to(root)
            collision_key = relative.as_posix().casefold()
            if collision_key in used_paths:
                filename = f"{Path(filename).stem}-{document.id[:8]}.md"
                relative = (directory / filename).relative_to(root)
                collision_key = relative.as_posix().casefold()
            used_paths.add(collision_key)
            markdown = markdown_for_version(version, document, self._settings)
            _atomic_write(root / relative, markdown.encode("utf-8"))
            entries.append(
                {
                    "repository_id": repository.id,
                    "document_id": document.id,
                    "version_id": version.id,
                    "content_hash": version.content_hash,
                    "source_job_id": version.source_job_id,
                    "path": relative.as_posix(),
                }
            )
        return entries

    def _create_backfill_snapshot(self) -> Path:
        current = _as_utc(self._now()).astimezone(ZoneInfo(self._settings.tz))
        target = self._settings.exports_root / "snapshots" / f"{current:%Y-%m-%d_%H%M%S}-backfill"
        staging = self._new_staging("backfill")
        try:
            entries = self._build_tree(staging, repository_ids=None)
            manifest = {
                "schema_version": 1,
                "kind": "historical-backfill",
                "created_at": _as_utc(self._now()).isoformat().replace("+00:00", "Z"),
                "images_downloaded": False,
                "documents": entries,
            }
            _atomic_write(staging / "manifest.json", canonical_json_bytes(manifest))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
            return target
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _stored_markdown_exists(self, relative_path: str) -> bool:
        candidate = (self._settings.data_root / relative_path).resolve()
        return candidate.is_relative_to(self._settings.content_root.resolve()) and candidate.is_file()

    def _version_markdown_path(self, version: DocumentVersion) -> str | None:
        anchor = version.raw_response_path or version.raw_body_path or version.manifest_path
        if not anchor:
            return None
        return (PurePosixPath(anchor).parent / "export.md").as_posix()

    def _new_staging(self, name: str) -> Path:
        staging = self._settings.exports_root / ".tmp" / f"{name}-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        return staging


def sanitize_path_segment(value: str, fallback: str) -> str:
    segment = _INVALID_PATH_CHARS.sub("_", value).strip(" .")
    if not segment:
        segment = fallback
    if segment.upper() in _WINDOWS_RESERVED:
        segment = f"_{segment}"
    return segment[:120].rstrip(" .") or fallback


def _unique_repository_names(repositories: Iterable[Repository]) -> dict[str, str]:
    repositories = list(repositories)
    base_names = {item.id: sanitize_path_segment(item.name, "未命名知识库") for item in repositories}
    counts = Counter(value.casefold() for value in base_names.values())
    return {
        item.id: (
            f"{base_names[item.id]}-{item.id[:8]}"
            if counts[base_names[item.id].casefold()] > 1
            else base_names[item.id]
        )
        for item in repositories
    }


def _document_groups(
    document: Document,
    toc_by_id: dict[str, TocItem],
    toc_by_remote: dict[tuple[str, str], TocItem],
) -> list[str]:
    item = toc_by_id.get(document.toc_item_id or "")
    groups: list[str] = []
    seen: set[str] = set()
    parent_remote_id = item.parent_remote_id if item else None
    while parent_remote_id and parent_remote_id not in seen:
        seen.add(parent_remote_id)
        parent = toc_by_remote.get((document.repository_id, parent_remote_id))
        if parent is None:
            break
        groups.append(parent.title)
        parent_remote_id = parent.parent_remote_id
    if groups:
        return list(reversed(groups))
    parts = [part for part in PurePosixPath(document.path or "/").parts if part not in {"/", "."}]
    return parts[:-1]


def _structured_markdown(value: Any, *, title: str, sheet: bool) -> str:
    parsed = _parse_json(value)
    if sheet:
        sheets = parsed.get("sheets") if isinstance(parsed, dict) else parsed
        if not isinstance(sheets, list):
            sheets = [parsed]
        sections: list[str] = [f"# {title}"]
        for index, item in enumerate(sheets):
            name = item.get("name") if isinstance(item, dict) else None
            rows = item.get("rows", item.get("data", [])) if isinstance(item, dict) else item
            sections.extend([f"## {name or f'Sheet {index + 1}'}", _rows_markdown(rows)])
        return "\n\n".join(section for section in sections if section)
    if isinstance(parsed, dict) and "pages" in parsed:
        values = [_parse_json(parsed.get("body_table"))]
        values.extend(_parse_json(page) for page in parsed.get("pages", []))
        rows = _merge_structured_rows(values)
    elif isinstance(parsed, dict):
        rows = parsed.get("records", parsed.get("rows", parsed.get("data", parsed)))
    else:
        rows = parsed
    return f"# {title}\n\n{_rows_markdown(rows)}"


def _merge_structured_rows(values: list[Any]) -> list[Any]:
    rows: list[Any] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("records", value.get("rows", value.get("data", [])))
        if isinstance(value, list):
            rows.extend(value)
    return rows


def _rows_markdown(rows: Any) -> str:
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        return "_暂无表格数据_"
    if all(isinstance(row, dict) for row in rows):
        columns = list(dict.fromkeys(str(key) for row in rows for key in row))
        header = "| " + " | ".join(_cell(column) for column in columns) + " |"
        divider = "| " + " | ".join("---" for _ in columns) + " |"
        body = [
            "| " + " | ".join(_cell(row.get(column)) for column in columns) + " |"
            for row in rows
        ]
        return "\n".join([header, divider, *body])
    normalized = [row if isinstance(row, list) else [row] for row in rows]
    width = max(len(row) for row in normalized)
    header = "| " + " | ".join(f"列 {index + 1}" for index in range(width)) + " |"
    divider = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = [
        "| " + " | ".join(_cell(row[index] if index < len(row) else "") for index in range(width)) + " |"
        for row in normalized
    ]
    return "\n".join([header, divider, *body])


def _board_markdown(value: Any, *, title: str) -> str:
    raw = _parse_json(value)
    return (
        f"# {title}\n\n"
        "> 此内容来自语雀画板, Markdown 无法完整表达其画布结构; 原始数据仍保存在版本归档中.\n\n"
        "```json\n"
        f"{json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        "```"
    )


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _first_body(document: dict[str, Any]) -> Any:
    for key in ("body", "body_html", "body_lake", "body_sheet", "body_table"):
        value = document.get(key)
        if value not in (None, ""):
            return value
    return ""


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) if value is not None else ""


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_directory(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.with_name(f".{target.name}.old-{uuid.uuid4().hex}")
    if target.exists():
        os.replace(target, previous)
    try:
        os.replace(source, target)
    except BaseException:
        if previous.exists() and not target.exists():
            os.replace(previous, target)
        raise
    shutil.rmtree(previous, ignore_errors=True)
