from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query
from pydantic import AwareDatetime
from sqlalchemy import select

from app.api.dependencies import CsrfAdmin, CurrentAdmin, DbSession
from app.api.openapi import (
    CSRF_IDEMPOTENCY_OPENAPI_EXTRA,
    CSRF_OPENAPI_EXTRA,
    documented_responses,
)
from app.api.schemas import JobStatus, Page
from app.core.errors import AppError
from app.core.models import BackupIssue, BackupJob, BackupSubtask, RepositoryCredential, utcnow
from app.modules.backups.schemas import (
    BackupJobResponse,
    BackupSubtaskResponse,
    CreateJobRequest,
    JobAccepted,
)
from app.modules.backups.service import (
    canonical_hash,
    commit_job_transaction,
    create_or_merge_job,
    get_job,
    replay_idempotency,
    save_idempotency,
    serialize_job,
    serialize_subtask,
    validate_idempotency_key,
)
from app.modules.documents.schemas import BackupIssueResponse
from app.modules.documents.service import serialize_issue

router = APIRouter(prefix="/api/v1/backup-jobs", tags=["backup-jobs"])


@router.post(
    "",
    response_model=JobAccepted,
    status_code=202,
    responses=documented_responses(400, 401, 403, 404, 409, 422, success_status=202),
    openapi_extra=CSRF_IDEMPOTENCY_OPENAPI_EXTRA,
)
def create_backup_job(
    payload: CreateJobRequest,
    db: DbSession,
    admin: CsrfAdmin,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", include_in_schema=False),
    ] = None,
) -> JobAccepted:
    key = validate_idempotency_key(idempotency_key)
    scope = payload.scope.model_dump(mode="json")
    request_hash = canonical_hash(payload.model_dump(mode="json"))
    path = "/api/v1/backup-jobs"
    replay = replay_idempotency(
        db,
        owner_key=admin.id,
        method="POST",
        path=path,
        key=key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay
    job, merged = create_or_merge_job(
        db,
        scope=scope,
        trigger="manual",
        trigger_key=f"{admin.id}:{key}:create",
    )
    response = JobAccepted(job=serialize_job(job), merged=merged)
    save_idempotency(
        db,
        owner_key=admin.id,
        method="POST",
        path=path,
        key=key,
        request_hash=request_hash,
        response=response,
    )
    commit_job_transaction(db)
    return response


@router.get(
    "",
    response_model=Page[BackupJobResponse],
    status_code=200,
    responses=documented_responses(401, 422),
)
def list_backup_jobs(
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    trigger: Literal["manual", "cron"] | None = None,
    credential_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
    created_from: AwareDatetime | None = None,
    created_to: AwareDatetime | None = None,
) -> Page[BackupJobResponse]:
    if created_from is not None and created_to is not None and created_from > created_to:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求参数不合法",
            field_errors=[{"field": "created_from", "reason": "range"}],
        )
    statement = select(BackupJob)
    if status:
        statement = statement.where(BackupJob.status.in_(status))
    if trigger:
        statement = statement.where(BackupJob.trigger == trigger)
    if created_from:
        statement = statement.where(BackupJob.created_at >= created_from)
    if created_to:
        statement = statement.where(BackupJob.created_at <= created_to)
    jobs = db.scalars(statement.order_by(BackupJob.created_at.desc(), BackupJob.id.desc())).all()
    if credential_id is not None:
        credential_value = str(credential_id)
        ids = set(
            db.scalars(
                select(BackupSubtask.job_id).where(BackupSubtask.credential_id == credential_value)
            ).all()
        )
        repository_ids = set(
            db.scalars(
                select(RepositoryCredential.repository_id).where(
                    RepositoryCredential.credential_id == credential_value,
                    RepositoryCredential.is_primary.is_(True),
                )
            ).all()
        )
        jobs = [
            job
            for job in jobs
            if job.id in ids
            or bool(repository_ids & set(job.scope.get("_target_repository_ids", [])))
        ]
    if repository_id is not None:
        repository_job_ids = set(
            db.scalars(
                select(BackupSubtask.job_id).where(
                    BackupSubtask.repository_id == str(repository_id)
                )
            ).all()
        )
        jobs = [
            job
            for job in jobs
            if job.id in repository_job_ids
            or str(repository_id) in job.scope.get("_target_repository_ids", [])
        ]
    total = len(jobs)
    offset = (page - 1) * page_size
    return Page(
        items=[serialize_job(job) for job in jobs[offset : offset + page_size]],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{job_id}",
    response_model=BackupJobResponse,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def backup_job_detail(job_id: uuid.UUID, db: DbSession, _admin: CurrentAdmin) -> BackupJobResponse:
    return serialize_job(get_job(db, str(job_id)))


@router.get(
    "/{job_id}/subtasks",
    response_model=Page[BackupSubtaskResponse],
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def list_subtasks(
    job_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: JobStatus | None = None,
    credential_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
) -> Page[BackupSubtaskResponse]:
    job = get_job(db, str(job_id))
    filters = [BackupSubtask.job_id == job.id]
    if status:
        filters.append(BackupSubtask.status == status)
    if credential_id:
        filters.append(BackupSubtask.credential_id == str(credential_id))
    if repository_id:
        filters.append(BackupSubtask.repository_id == str(repository_id))
    subtasks = db.scalars(
        select(BackupSubtask).where(*filters).order_by(BackupSubtask.created_at.asc(), BackupSubtask.id.asc())
    ).all()
    total = len(subtasks)
    offset = (page - 1) * page_size
    return Page(
        items=[serialize_subtask(db, item) for item in subtasks[offset : offset + page_size]],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{job_id}/issues",
    response_model=Page[BackupIssueResponse],
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def list_job_issues(
    job_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    level: Literal["warning", "error"] | None = None,
    credential_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    asset_id: uuid.UUID | None = None,
    code: str | None = None,
) -> Page[BackupIssueResponse]:
    job = get_job(db, str(job_id))
    filters = [BackupIssue.job_id == job.id]
    for column, value in (
        (BackupIssue.level, level),
        (BackupIssue.credential_id, credential_id),
        (BackupIssue.repository_id, repository_id),
        (BackupIssue.document_id, document_id),
        (BackupIssue.asset_id, asset_id),
        (BackupIssue.code, code),
    ):
        if value is not None:
            filters.append(column == str(value))
    issues = db.scalars(
        select(BackupIssue)
        .where(*filters)
        .order_by(BackupIssue.last_occurred_at.desc(), BackupIssue.id.desc())
    ).all()
    total = len(issues)
    offset = (page - 1) * page_size
    return Page(
        items=[serialize_issue(item) for item in issues[offset : offset + page_size]],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/{job_id}/cancel",
    response_model=BackupJobResponse,
    status_code=202,
    responses=documented_responses(401, 403, 404, 409, 422, success_status=202),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def cancel_backup_job(job_id: uuid.UUID, db: DbSession, _admin: CsrfAdmin) -> BackupJobResponse:
    job = get_job(db, str(job_id))
    if job.status not in {"queued", "running", "waiting_quota"} or job.cancel_requested_at is not None:
        raise AppError(409, "JOB_NOT_CANCELLABLE", "该任务当前不能取消")
    job.cancel_requested_at = utcnow()
    db.commit()
    return serialize_job(job)


@router.post(
    "/{job_id}/rerun",
    response_model=JobAccepted,
    status_code=202,
    responses=documented_responses(400, 401, 403, 404, 409, 422, success_status=202),
    openapi_extra=CSRF_IDEMPOTENCY_OPENAPI_EXTRA,
)
def rerun_backup_job(
    job_id: uuid.UUID,
    db: DbSession,
    admin: CsrfAdmin,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", include_in_schema=False),
    ] = None,
) -> JobAccepted:
    source = get_job(db, str(job_id))
    if source.status not in {"partial", "failed", "cancelled"}:
        raise AppError(409, "JOB_NOT_RERUNNABLE", "该任务当前不能重新执行")
    key = validate_idempotency_key(idempotency_key)
    path = f"/api/v1/backup-jobs/{source.id}/rerun"
    request_hash = canonical_hash({})
    replay = replay_idempotency(
        db,
        owner_key=admin.id,
        method="POST",
        path=path,
        key=key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay
    scope = {key: value for key, value in source.scope.items() if not key.startswith("_")}
    job, merged = create_or_merge_job(
        db,
        scope=scope,
        trigger="manual",
        trigger_key=f"{admin.id}:{key}:rerun",
        target_override=list(source.scope.get("_target_repository_ids", [])),
    )
    response = JobAccepted(job=serialize_job(job), merged=merged)
    save_idempotency(
        db,
        owner_key=admin.id,
        method="POST",
        path=path,
        key=key,
        request_hash=request_hash,
        response=response,
    )
    commit_job_transaction(db)
    return response
