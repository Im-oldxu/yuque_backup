from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.modules.backups.schemas import JobScope


class DashboardScheduleResponse(BaseModel):
    enabled: bool
    cron: str
    timezone: str
    next_run_at: datetime | None


class DashboardJobResponse(BaseModel):
    id: UUID
    trigger: Literal["manual", "cron"]
    scope: JobScope
    status: Literal["queued", "running", "waiting_quota", "succeeded", "partial", "failed", "cancelled"]
    progress: float
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
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    can_cancel: bool
    can_rerun: bool


class DashboardJobCountsResponse(BaseModel):
    succeeded: int
    partial: int
    failed: int


class DashboardStorageResponse(BaseModel):
    database_bytes: int
    content_bytes: int
    asset_bytes: int
    total_bytes: int


class DashboardWorkerResponse(BaseModel):
    status: Literal["online", "offline"]
    last_heartbeat_at: datetime | None


class DashboardSummaryResponse(BaseModel):
    schedule: DashboardScheduleResponse
    current_job: DashboardJobResponse | None
    last_success_at: datetime | None
    waiting_quota_credentials: int
    job_counts: DashboardJobCountsResponse
    repositories: int
    documents: int
    versions: int
    storage: DashboardStorageResponse
    worker: DashboardWorkerResponse
