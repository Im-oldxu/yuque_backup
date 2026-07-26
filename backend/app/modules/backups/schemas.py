from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from app.api.schemas import APIModel, CredentialStatus, JobStatus


class AllScope(APIModel):
    type: Literal["all"]


class CredentialScope(APIModel):
    type: Literal["credential"]
    credential_id: uuid.UUID


class RepositoryScope(APIModel):
    type: Literal["repository"]
    repository_id: uuid.UUID


class RepositoriesScope(APIModel):
    type: Literal["repositories"]
    credential_id: uuid.UUID
    repository_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


JobScope = Annotated[
    AllScope | CredentialScope | RepositoryScope | RepositoriesScope,
    Field(discriminator="type"),
]


class CreateJobRequest(APIModel):
    scope: JobScope


class BackupJobResponse(APIModel):
    id: uuid.UUID
    trigger: Literal["manual", "cron"]
    scope: JobScope
    status: JobStatus
    progress: float
    status_reason: str | None = None
    document_total: int
    document_succeeded: int
    document_partial: int
    document_failed: int
    asset_total: int
    asset_succeeded: int
    asset_failed: int
    issue_count: int
    waiting_quota_credentials: int
    next_retry_at: datetime | None
    merged_from: list[Any] = Field(default_factory=list)
    cleanup_stats: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    can_cancel: bool
    can_rerun: bool


class JobAccepted(APIModel):
    job: BackupJobResponse
    merged: bool


class QuotaEstimateCredential(APIModel):
    credential_id: uuid.UUID
    credential_name: str
    repository_count: int
    document_count: int
    estimated_api_calls: int
    rate_limit_limit: int | None
    rate_limit_remaining: int | None
    rate_limit_observed_at: datetime | None
    snapshot_fresh: bool
    sufficient: bool | None


class QuotaEstimateResponse(APIModel):
    repository_count: int
    document_count: int
    estimated_api_calls: int
    is_precise: Literal[False] = False
    credentials: list[QuotaEstimateCredential]
    calculation_basis: list[str]


class CredentialPick(APIModel):
    id: uuid.UUID
    name: str
    status: CredentialStatus


class RepositoryPick(APIModel):
    id: uuid.UUID
    name: str


class BackupActivityResponse(APIModel):
    stage: Literal[
        "queued",
        "waiting_retry",
        "repository_metadata",
        "repository_toc",
        "repository_documents",
        "repository_deletions",
        "document_fetch",
        "resource_download",
        "resource_retry",
        "document_commit",
    ]
    document_title: str | None = None
    resource_name: str | None = None
    resource_completed: int = 0
    resource_total: int = 0
    attempt: int | None = None
    max_attempts: int | None = None
    retry_in_seconds: int | None = None
    last_error_code: str | None = None
    updated_at: datetime | None = None


class BackupSubtaskResponse(APIModel):
    id: uuid.UUID
    credential: CredentialPick
    repository: RepositoryPick
    status: JobStatus
    document_total: int
    document_completed: int
    issue_count: int
    next_retry_at: datetime | None
    last_issue: str | None
    activity: BackupActivityResponse | None
    created_at: datetime
