from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_BODY_EXTENSIONS = {
    "html": "html",
    "lake": "lake",
    "lakesheet": "json",
    "markdown": "md",
}


@dataclass(frozen=True, slots=True)
class CommittedVersion:
    raw_response_path: str
    raw_body_path: str
    markdown_path: str
    preview_path: str | None
    manifest_path: str
    content_size_bytes: int


@dataclass(frozen=True, slots=True)
class CommittedAsset:
    storage_path: str
    size: int
    sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalized_content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ContentStore:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.expanduser().resolve()
        self.content_root = self.data_root / "content"
        self.temp_root = self.content_root / ".tmp"
        self.content_root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    def new_temp_path(self, job_id: str, *, suffix: str = ".part") -> Path:
        job_dir = self.temp_root / _segment(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir / f"{uuid.uuid4().hex}{suffix}"

    def commit_version(
        self,
        *,
        job_id: str,
        repository_id: str,
        document_id: str,
        content_hash: str,
        raw_response: Any,
        raw_body: str | bytes,
        body_format: str | None,
        markdown: str,
        preview_html: str | None,
        normalized_metadata: dict[str, Any],
        resources: list[dict[str, Any]],
    ) -> CommittedVersion:
        for value in (job_id, repository_id, document_id, content_hash):
            _segment(value)
        if not re.fullmatch(r"[a-f0-9]{64}", content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")

        staging = self.temp_root / _segment(job_id) / f"version-{uuid.uuid4().hex}"
        final_dir = self.content_root / "versions" / repository_id / document_id / content_hash
        staging.mkdir(parents=True, exist_ok=False)
        body_extension = _BODY_EXTENSIONS.get((body_format or "").lower(), "txt")
        body_bytes = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
        files: dict[str, bytes] = {
            "raw-response.json": canonical_json_bytes(raw_response),
            f"body.{body_extension}": body_bytes,
            "export.md": markdown.encode("utf-8"),
        }
        if preview_html is not None:
            files["preview.html"] = preview_html.encode("utf-8")

        try:
            manifest_files: list[dict[str, Any]] = []
            for name, data in files.items():
                _write_durable(staging / name, data)
                manifest_files.append(
                    {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                )
            manifest = {
                "content_hash": content_hash,
                "metadata": normalized_metadata,
                "files": manifest_files,
                "resources": resources,
            }
            manifest_bytes = canonical_json_bytes(manifest)
            _write_durable(staging / "manifest.json", manifest_bytes)
            files["manifest.json"] = manifest_bytes
            _fsync_directory(staging)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_dir.exists():
                shutil.rmtree(staging)
            else:
                os.replace(staging, final_dir)
                _fsync_directory(final_dir.parent)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        prefix = final_dir.relative_to(self.data_root).as_posix()
        preview_path = f"{prefix}/preview.html" if preview_html is not None else None
        return CommittedVersion(
            raw_response_path=f"{prefix}/raw-response.json",
            raw_body_path=f"{prefix}/body.{body_extension}",
            markdown_path=f"{prefix}/export.md",
            preview_path=preview_path,
            manifest_path=f"{prefix}/manifest.json",
            content_size_bytes=sum(len(value) for value in files.values()),
        )

    def commit_asset(self, temp_path: Path, *, sha256: str, size: int) -> CommittedAsset:
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        temp_path = temp_path.resolve()
        if not temp_path.is_relative_to(self.temp_root):
            raise ValueError("asset temporary file is outside the content temporary directory")
        if temp_path.stat().st_size != size:
            raise ValueError("asset size changed before commit")
        final_path = self.content_root / "assets" / "sha256" / sha256[:2] / sha256[2:4] / sha256
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            temp_path.unlink(missing_ok=True)
        else:
            os.replace(temp_path, final_path)
            _fsync_directory(final_path.parent)
        return CommittedAsset(
            storage_path=final_path.relative_to(self.data_root).as_posix(),
            size=size,
            sha256=sha256,
        )

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.data_root / relative_path).resolve()
        if not candidate.is_relative_to(self.content_root):
            raise ValueError("stored path escapes the configured content root")
        return candidate

    def delete_relative(self, relative_path: str | None) -> int:
        if not relative_path:
            return 0
        path = self.resolve(relative_path)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return 0
        if not path.is_file():
            raise ValueError("stored content path does not refer to a regular file")
        path.unlink()
        self._remove_empty_parents(path.parent)
        return size

    def _remove_empty_parents(self, path: Path) -> None:
        while path != self.content_root and path.is_relative_to(self.content_root):
            try:
                path.rmdir()
            except OSError:
                break
            path = path.parent


def _segment(value: str) -> str:
    if not value or not _SAFE_SEGMENT.fullmatch(value):
        raise ValueError("unsafe storage path segment")
    return value


def _write_durable(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
