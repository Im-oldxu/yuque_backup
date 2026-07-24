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


JobScope = Annotated[AllScope | CredentialScope | RepositoryScope, Field(discriminator="type")]


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


class CredentialPick(APIModel):
    id: uuid.UUID
    name: str
    status: CredentialStatus


class RepositoryPick(APIModel):
    id: uuid.UUID
    name: str


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
    created_at: datetime
