from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.models import (
    AppSetting,
    BackupJob,
    Document,
    DocumentVersion,
    Repository,
    WorkerHeartbeat,
    YuqueCredential,
)
from app.modules.backups.service import public_progress, public_scope
from app.modules.dashboard.schemas import (
    DashboardJobCountsResponse,
    DashboardJobResponse,
    DashboardScheduleResponse,
    DashboardStorageResponse,
    DashboardSummaryResponse,
    DashboardWorkerResponse,
)
from app.modules.settings.service import as_utc, calculate_next_runs, get_storage_usage

ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_quota")
CANCELLABLE_JOB_STATUSES = frozenset(ACTIVE_JOB_STATUSES)
RERUNNABLE_JOB_STATUSES = frozenset(("partial", "failed", "cancelled"))


def _datetime_or_none(value: datetime | None) -> datetime | None:
    return as_utc(value) if value is not None else None


def _job_response(job: BackupJob) -> DashboardJobResponse:
    return DashboardJobResponse(
        id=job.id,
        trigger=job.trigger,
        scope=public_scope(job.scope),
        status=job.status,
        progress=public_progress(job.progress),
        document_total=job.document_total,
        document_succeeded=job.document_succeeded,
        document_partial=job.document_partial,
        document_failed=job.document_failed,
        asset_total=job.asset_total,
        asset_succeeded=job.asset_succeeded,
        asset_failed=job.asset_failed,
        issue_count=job.issue_count,
        waiting_quota_credentials=job.waiting_quota_credentials,
        next_retry_at=_datetime_or_none(job.next_retry_at),
        created_at=as_utc(job.created_at),
        started_at=_datetime_or_none(job.started_at),
        finished_at=_datetime_or_none(job.finished_at),
        cancel_requested_at=_datetime_or_none(job.cancel_requested_at),
        can_cancel=job.status in CANCELLABLE_JOB_STATUSES and job.cancel_requested_at is None,
        can_rerun=job.status in RERUNNABLE_JOB_STATUSES,
    )


def _current_job(db: Session) -> DashboardJobResponse | None:
    priority = case(
        (BackupJob.status == "running", 0),
        (BackupJob.status == "waiting_quota", 1),
        (BackupJob.status == "queued", 2),
        else_=3,
    )
    job = db.scalar(
        select(BackupJob)
        .where(BackupJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(priority, BackupJob.created_at.asc(), BackupJob.id.asc())
        .limit(1)
    )
    return _job_response(job) if job is not None else None


def _job_counts(db: Session) -> DashboardJobCountsResponse:
    counts = {"succeeded": 0, "partial": 0, "failed": 0}
    rows = db.execute(
        select(BackupJob.status, func.count(BackupJob.id))
        .where(BackupJob.status.in_(tuple(counts)))
        .group_by(BackupJob.status)
    )
    for job_status, count in rows:
        counts[job_status] = int(count)
    return DashboardJobCountsResponse(**counts)


def _worker_status(
    heartbeat: WorkerHeartbeat | None,
    settings: Settings,
    *,
    now: datetime,
) -> DashboardWorkerResponse:
    if heartbeat is None:
        return DashboardWorkerResponse(status="offline", last_heartbeat_at=None)

    last_heartbeat_at = as_utc(heartbeat.last_heartbeat_at)
    online_window = timedelta(seconds=max(settings.worker_heartbeat_seconds * 3, 30))
    status = "online" if last_heartbeat_at >= now - online_window else "offline"
    return DashboardWorkerResponse(status=status, last_heartbeat_at=last_heartbeat_at)


def get_dashboard_summary(
    db: Session,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> DashboardSummaryResponse:
    settings = settings or get_settings()
    now = as_utc(now or datetime.now(UTC))
    app_setting = db.get(AppSetting, 1)
    if app_setting is None:
        from app.core.errors import AppError

        raise AppError(503, "SERVICE_UNAVAILABLE", "应用设置尚未初始化")

    next_run_at = None
    if app_setting.schedule_enabled:
        next_run_at = calculate_next_runs(app_setting.cron, app_setting.timezone, count=1, now=now)[0]

    last_success_at = db.scalar(
        select(func.max(BackupJob.finished_at)).where(BackupJob.status == "succeeded")
    )
    waiting_quota_credentials = int(
        db.scalar(
            select(func.count(YuqueCredential.id)).where(
                YuqueCredential.status == "waiting_quota",
                YuqueCredential.deleted_at.is_(None),
            )
        )
        or 0
    )
    repository_count = int(
        db.scalar(select(func.count(Repository.id)).where(Repository.selected.is_(True))) or 0
    )
    document_count = int(db.scalar(select(func.count(Document.id))) or 0)
    version_count = int(
        db.scalar(select(func.count(DocumentVersion.id)).where(DocumentVersion.purged_at.is_(None))) or 0
    )
    usage = get_storage_usage(db, settings)
    heartbeat = db.get(WorkerHeartbeat, 1)

    return DashboardSummaryResponse(
        schedule=DashboardScheduleResponse(
            enabled=app_setting.schedule_enabled,
            cron=app_setting.cron,
            timezone=app_setting.timezone,
            next_run_at=next_run_at,
        ),
        current_job=_current_job(db),
        last_success_at=_datetime_or_none(last_success_at),
        waiting_quota_credentials=waiting_quota_credentials,
        job_counts=_job_counts(db),
        repositories=repository_count,
        documents=document_count,
        versions=version_count,
        storage=DashboardStorageResponse(
            database_bytes=usage.database_bytes,
            content_bytes=usage.version_bytes + usage.asset_bytes,
            asset_bytes=usage.asset_bytes,
            total_bytes=usage.total_bytes,
        ),
        worker=_worker_status(heartbeat, settings, now=now),
    )
