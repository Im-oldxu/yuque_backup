from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.api.schemas import APIModel, Completeness, DocumentType


class RepositorySummary(APIModel):
    id: UUID
    name: str
    namespace: str | None


class DocumentSummary(APIModel):
    id: UUID
    repository_id: UUID
    yuque_doc_id: str
    type: DocumentType
    title: str
    slug: str | None
    path: str
    deleted_at: datetime | None
    purge_at: datetime | None
    latest_version_id: UUID | None
    latest_version_completeness: Completeness | None
    updated_at: datetime


class VersionSummary(APIModel):
    id: UUID
    remote_version_id: str | None
    format: str | None
    content_hash: str
    completeness: Completeness
    is_latest: bool
    preview_available: bool
    resource_total: int
    resource_downloaded: int
    issue_count: int
    source_job_id: UUID
    remote_updated_at: datetime | None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    repository: RepositorySummary
    original_path: str
    remaining_retention_seconds: int | None
    latest_successful_version: VersionSummary | None
    version_count: int


class DownloadAvailability(APIModel):
    raw_response: bool
    raw_body: bool
    offline_html: bool


class AssetSummary(APIModel):
    total: int
    downloaded: int
    failed: int
    skipped: int


class VersionDetail(VersionSummary):
    document_id: UUID
    downloads: DownloadAvailability
    asset_summary: AssetSummary
    metadata: dict[str, Any]


class AssetReferenceResponse(APIModel):
    asset_id: UUID | None
    name: str
    type: str
    mime_type: str | None
    size: int | None
    status: Literal["pending", "downloaded", "skipped", "failed"]
    inline_available: bool
    download_available: bool
    issue_code: str | None


class BackupIssueResponse(APIModel):
    id: UUID
    level: Literal["warning", "error"]
    code: str
    message: str
    credential_id: UUID | None
    repository_id: UUID | None
    document_id: UUID | None
    document_title: str | None
    asset_id: UUID | None
    asset_type: str | None
    safe_url: str | None
    http_status: int | None
    attempt_count: int
    first_occurred_at: datetime
    last_occurred_at: datetime


class SearchRepositoryResponse(APIModel):
    id: UUID
    name: str
    namespace: str | None
    selected: bool


class SearchResponse(APIModel):
    repositories: list[SearchRepositoryResponse]
    documents: list[DocumentSummary]
