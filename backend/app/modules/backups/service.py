from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import utc_datetime
from app.core.errors import AppError
from app.core.models import (
    BackupJob,
    BackupSubtask,
    IdempotencyRecord,
    JobTrigger,
    Repository,
    RepositoryCredential,
    YuqueCredential,
    utcnow,
)
from app.modules.backups.schemas import (
    BackupJobResponse,
    BackupSubtaskResponse,
    CredentialPick,
    JobAccepted,
    RepositoryPick,
)

ACTIVE_JOB_STATUSES = ("running", "waiting_quota")
TERMINAL_JOB_STATUSES = ("succeeded", "partial", "failed", "cancelled")


def public_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in scope.items() if not key.startswith("_")}


def serialize_job(job: BackupJob) -> BackupJobResponse:
    return BackupJobResponse(
        id=job.id,
        trigger=job.trigger,
        scope=public_scope(job.scope),
        status=job.status,
        progress=max(0.0, min(1.0, job.progress)),
        status_reason=job.status_reason,
        document_total=job.document_total,
        document_succeeded=job.document_succeeded,
        document_partial=job.document_partial,
        document_failed=job.document_failed,
        asset_total=job.asset_total,
        asset_succeeded=job.asset_succeeded,
        asset_failed=job.asset_failed,
        issue_count=job.issue_count,
        waiting_quota_credentials=job.waiting_quota_credentials,
        next_retry_at=utc_datetime(job.next_retry_at),
        merged_from=job.merged_from,
        cleanup_stats=job.cleanup_stats,
        created_at=utc_datetime(job.created_at),
        started_at=utc_datetime(job.started_at),
        finished_at=utc_datetime(job.finished_at),
        cancel_requested_at=utc_datetime(job.cancel_requested_at),
        can_cancel=job.status in {"queued", "running", "waiting_quota"} and job.cancel_requested_at is None,
        can_rerun=job.status in {"partial", "failed", "cancelled"},
    )


def serialize_subtask(db: Session, subtask: BackupSubtask) -> BackupSubtaskResponse:
    credential = db.get(YuqueCredential, subtask.credential_id)
    repository = db.get(Repository, subtask.repository_id)
    if credential is None or repository is None:
        raise AppError(503, "SERVICE_UNAVAILABLE", "任务索引不完整")
    return BackupSubtaskResponse(
        id=subtask.id,
        credential=CredentialPick(id=credential.id, name=credential.name, status=credential.status),
        repository=RepositoryPick(id=repository.id, name=repository.name),
        status=subtask.status,
        document_total=subtask.document_total,
        document_completed=subtask.document_completed,
        issue_count=subtask.issue_count,
        next_retry_at=utc_datetime(subtask.next_retry_at),
        last_issue=subtask.last_issue,
        created_at=utc_datetime(subtask.created_at),
    )


def get_job(db: Session, job_id: str) -> BackupJob:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise AppError(404, "JOB_NOT_FOUND", "备份任务不存在")
    return job


def _usable_targets_query() -> Any:
    return (
        select(Repository.id, YuqueCredential.id)
        .join(RepositoryCredential, RepositoryCredential.repository_id == Repository.id)
        .join(YuqueCredential, YuqueCredential.id == RepositoryCredential.credential_id)
        .where(
            RepositoryCredential.is_primary.is_(True),
            YuqueCredential.deleted_at.is_(None),
            YuqueCredential.enabled.is_(True),
            YuqueCredential.verification_valid.is_(True),
            YuqueCredential.status == "valid",
        )
    )


def resolve_targets(db: Session, scope: dict[str, Any]) -> list[str]:
    scope_type = scope["type"]
    statement = _usable_targets_query()
    if scope_type == "all":
        statement = statement.where(Repository.selected.is_(True))
    elif scope_type == "credential":
        credential_id = scope.get("credential_id")
        credential = db.scalar(
            select(YuqueCredential).where(
                YuqueCredential.id == credential_id,
                YuqueCredential.deleted_at.is_(None),
            )
        )
        if credential is None:
            raise AppError(404, "CREDENTIAL_NOT_FOUND", "语雀凭据不存在")
        statement = statement.where(
            YuqueCredential.id == credential_id,
            Repository.selected.is_(True),
        )
    elif scope_type == "repository":
        repository_id = scope.get("repository_id")
        repository = db.get(Repository, repository_id)
        if repository is None:
            raise AppError(404, "REPOSITORY_NOT_FOUND", "知识库不存在")
        relation = db.scalar(
            select(RepositoryCredential).where(
                RepositoryCredential.repository_id == repository.id,
                RepositoryCredential.is_primary.is_(True),
            )
        )
        if relation is None:
            raise AppError(409, "PRIMARY_CREDENTIAL_REQUIRED", "知识库需要指定主凭据")
        statement = statement.where(Repository.id == repository.id)
    else:
        raise AppError(422, "VALIDATION_ERROR", "任务范围不合法")
    targets = [row[0] for row in db.execute(statement).all()]
    if not targets:
        if scope_type == "all":
            selected_without_primary = db.scalar(
                select(Repository.id)
                .outerjoin(
                    RepositoryCredential,
                    (RepositoryCredential.repository_id == Repository.id)
                    & RepositoryCredential.is_primary.is_(True),
                )
                .where(Repository.selected.is_(True), RepositoryCredential.id.is_(None))
                .limit(1)
            )
            if selected_without_primary:
                raise AppError(409, "PRIMARY_CREDENTIAL_REQUIRED", "知识库需要指定主凭据")
        raise AppError(409, "NO_ENABLED_TARGETS", "没有可执行的已启用备份目标")
    return sorted(set(targets))


def merge_public_scope(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_public = public_scope(left)
    right_public = public_scope(right)
    if left_public == right_public:
        return left_public
    if left_public.get("type") == "all" or right_public.get("type") == "all":
        return {"type": "all"}
    return {"type": "all"}


def create_or_merge_job(
    db: Session,
    *,
    scope: dict[str, Any],
    trigger: str,
    trigger_key: str,
    target_override: list[str] | None = None,
) -> tuple[BackupJob, bool]:
    targets = target_override or resolve_targets(db, scope)
    if target_override:
        usable = set(row[0] for row in db.execute(_usable_targets_query()).all())
        targets = sorted(set(target_override) & usable)
        if not targets:
            raise AppError(409, "NO_ENABLED_TARGETS", "没有可执行的已启用备份目标")
    queued = db.scalar(select(BackupJob).where(BackupJob.status == "queued", BackupJob.pending_slot == 1))
    if queued is not None:
        existing_targets = set(queued.scope.get("_target_repository_ids", []))
        merged_targets = sorted(existing_targets | set(targets))
        merged_public = merge_public_scope(queued.scope, scope)
        queued.scope = {**merged_public, "_target_repository_ids": merged_targets}
        queued.merged_from = [*queued.merged_from, {"trigger": trigger, "scope": public_scope(scope)}]
        db.add(
            JobTrigger(
                trigger=trigger,
                idempotency_key=trigger_key,
                scope=public_scope(scope),
                status="merged",
                job_id=queued.id,
            )
        )
        return queued, True

    job = BackupJob(
        trigger=trigger,
        scope={**public_scope(scope), "_target_repository_ids": targets},
        status="queued",
        pending_slot=1,
        active_slot=None,
    )
    db.add(job)
    db.flush()
    db.add(
        JobTrigger(
            trigger=trigger,
            idempotency_key=trigger_key,
            scope=public_scope(scope),
            status="accepted",
            job_id=job.id,
        )
    )
    return job, False


def canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_idempotency_key(value: str | None) -> str:
    if value is None:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Idempotency-Key 必须是 UUID",
            field_errors=[{"field": "Idempotency-Key", "reason": "uuid"}],
        ) from exc


def replay_idempotency(
    db: Session,
    *,
    owner_key: str,
    method: str,
    path: str,
    key: str,
    request_hash: str,
) -> JobAccepted | None:
    now = utcnow()
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_key == owner_key,
            IdempotencyRecord.method == method,
            IdempotencyRecord.path == path,
            IdempotencyRecord.idempotency_key == key,
        )
    )
    if record is None:
        return None
    expires_at = (
        record.expires_at.replace(tzinfo=UTC)
        if record.expires_at.tzinfo is None
        else record.expires_at.astimezone(UTC)
    )
    if expires_at <= now:
        db.delete(record)
        db.flush()
        return None
    if record.request_hash != request_hash:
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键对应了不同请求")
    return JobAccepted.model_validate(record.response_json)


def save_idempotency(
    db: Session,
    *,
    owner_key: str,
    method: str,
    path: str,
    key: str,
    request_hash: str,
    response: JobAccepted,
) -> None:
    db.add(
        IdempotencyRecord(
            owner_key=owner_key,
            method=method,
            path=path,
            idempotency_key=key,
            request_hash=request_hash,
            response_status=202,
            response_json=response.model_dump(mode="json"),
            expires_at=utcnow() + timedelta(hours=24),
        )
    )


def commit_job_transaction(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "CONFLICT", "任务触发发生并发冲突，请重试") from exc  # noqa: RUF001
