from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import utc_datetime
from app.core.errors import AppError
from app.core.models import (
    BackupJob,
    BackupSubtask,
    Document,
    IdempotencyRecord,
    JobTrigger,
    QueueItem,
    Repository,
    RepositoryCredential,
    YuqueCredential,
    utcnow,
)
from app.modules.backups.schemas import (
    BackupActivityResponse,
    BackupJobResponse,
    BackupSubtaskResponse,
    CredentialPick,
    JobAccepted,
    QuotaEstimateCredential,
    QuotaEstimateResponse,
    RepositoryPick,
)

ACTIVE_JOB_STATUSES = ("running", "waiting_quota")
TERMINAL_JOB_STATUSES = ("succeeded", "partial", "failed", "cancelled")


def public_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in scope.items() if not key.startswith("_")}


def public_progress(progress: float) -> float:
    return max(0.0, min(100.0, progress * 100))


def serialize_job(job: BackupJob) -> BackupJobResponse:
    return BackupJobResponse(
        id=job.id,
        trigger=job.trigger,
        scope=public_scope(job.scope),
        status=job.status,
        progress=public_progress(job.progress),
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
        activity=_serialize_subtask_activity(db, subtask),
        created_at=utc_datetime(subtask.created_at),
    )


def _serialize_subtask_activity(
    db: Session,
    subtask: BackupSubtask,
) -> BackupActivityResponse | None:
    active_items = list(
        db.scalars(
            select(QueueItem)
            .where(
                QueueItem.subtask_id == subtask.id,
                QueueItem.status.in_(("pending", "running", "retry_wait")),
            )
            .order_by(QueueItem.updated_at.desc(), QueueItem.created_at.desc())
        )
    )
    if not active_items:
        return None
    item = min(
        active_items,
        key=lambda candidate: {
            "running": 0,
            "retry_wait": 1,
            "pending": 2,
        }.get(candidate.status, 3),
    )
    payload_activity = item.payload.get("_activity")
    if isinstance(payload_activity, dict):
        return BackupActivityResponse.model_validate(payload_activity)
    if item.status == "retry_wait":
        stage = "waiting_retry"
    elif item.category == "document_sync":
        stage = "document_fetch" if item.status == "running" else "queued"
    elif item.status == "pending":
        stage = "queued"
    else:
        stage = {
            "metadata": "repository_metadata",
            "toc": "repository_toc",
            "documents": "repository_documents",
            "deleted_documents": "repository_deletions",
            "barrier": "document_commit",
        }.get(str(item.payload.get("stage", "metadata")), "repository_metadata")
    document = db.get(Document, item.document_id) if item.document_id else None
    return BackupActivityResponse(
        stage=stage,
        document_title=document.title if document else None,
        attempt=item.attempt_count or None,
        updated_at=utc_datetime(item.updated_at),
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
    elif scope_type == "repositories":
        credential_id = str(scope.get("credential_id"))
        repository_ids = sorted({str(value) for value in scope.get("repository_ids", [])})
        credential = db.scalar(
            select(YuqueCredential).where(
                YuqueCredential.id == credential_id,
                YuqueCredential.deleted_at.is_(None),
            )
        )
        if credential is None:
            raise AppError(404, "CREDENTIAL_NOT_FOUND", "语雀凭据不存在")
        existing_ids = set(
            db.scalars(select(Repository.id).where(Repository.id.in_(repository_ids))).all()
        )
        if existing_ids != set(repository_ids):
            raise AppError(404, "REPOSITORY_NOT_FOUND", "知识库不存在")
        statement = statement.where(
            YuqueCredential.id == credential_id,
            Repository.id.in_(repository_ids),
        )
    else:
        raise AppError(422, "VALIDATION_ERROR", "任务范围不合法")
    targets = [row[0] for row in db.execute(statement).all()]
    if scope_type == "repositories":
        requested = {str(value) for value in scope.get("repository_ids", [])}
        if set(targets) != requested:
            raise AppError(
                409,
                "CREDENTIAL_CANNOT_ACCESS_REPOSITORY",
                "所选凭据不是全部所选知识库的可用主凭据",
            )
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


def _target_rows(db: Session, repository_ids: list[str]) -> list[tuple[Repository, YuqueCredential]]:
    statement = (
        select(Repository, YuqueCredential)
        .join(RepositoryCredential, RepositoryCredential.repository_id == Repository.id)
        .join(YuqueCredential, YuqueCredential.id == RepositoryCredential.credential_id)
        .where(
            Repository.id.in_(repository_ids),
            RepositoryCredential.is_primary.is_(True),
            YuqueCredential.deleted_at.is_(None),
        )
        .order_by(YuqueCredential.name.asc(), Repository.name.asc(), Repository.id.asc())
    )
    return [(repository, credential) for repository, credential in db.execute(statement)]


def _repository_api_call_estimate(document_count: int, *, initial: bool) -> int:
    # Mirrors SyncExecutor: repository metadata, TOC, paged current list,
    # optional deleted list, then one detail request per locally known document.
    current_list_pages = max(1, (document_count + 99) // 100)
    return 2 + current_list_pages + (0 if initial else 1) + document_count


def estimate_scope_quota(
    db: Session,
    scope: dict[str, Any],
    *,
    now: datetime | None = None,
) -> QuotaEstimateResponse:
    target_ids = resolve_targets(db, scope)
    rows = _target_rows(db, target_ids)
    counts = {
        repository_id: int(count)
        for repository_id, count in db.execute(
            select(Document.repository_id, func.count(Document.id))
            .where(Document.repository_id.in_(target_ids), Document.deleted_at.is_(None))
            .group_by(Document.repository_id)
        )
    }
    current = now or utcnow()
    grouped: dict[str, dict[str, Any]] = {}
    for repository, credential in rows:
        document_count = counts.get(repository.id, 0)
        entry = grouped.setdefault(
            credential.id,
            {
                "credential": credential,
                "repository_count": 0,
                "document_count": 0,
                "estimated_api_calls": 0,
            },
        )
        entry["repository_count"] += 1
        entry["document_count"] += document_count
        entry["estimated_api_calls"] += _repository_api_call_estimate(
            document_count,
            initial=repository.safe_watermark is None,
        )

    credential_estimates: list[QuotaEstimateCredential] = []
    for entry in grouped.values():
        credential = entry["credential"]
        observed_at = utc_datetime(credential.rate_limit_observed_at)
        snapshot_fresh = bool(observed_at and observed_at >= current - timedelta(hours=1))
        remaining = credential.rate_limit_remaining
        sufficient = (
            remaining >= entry["estimated_api_calls"]
            if snapshot_fresh and remaining is not None
            else None
        )
        credential_estimates.append(
            QuotaEstimateCredential(
                credential_id=credential.id,
                credential_name=credential.name,
                repository_count=entry["repository_count"],
                document_count=entry["document_count"],
                estimated_api_calls=entry["estimated_api_calls"],
                rate_limit_limit=credential.rate_limit_limit,
                rate_limit_remaining=remaining,
                rate_limit_observed_at=observed_at,
                snapshot_fresh=snapshot_fresh,
                sufficient=sufficient,
            )
        )
    return QuotaEstimateResponse(
        repository_count=len(target_ids),
        document_count=sum(item.document_count for item in credential_estimates),
        estimated_api_calls=sum(item.estimated_api_calls for item in credential_estimates),
        credentials=credential_estimates,
        calculation_basis=[
            "每个知识库包含详情与目录请求",
            "文档列表按语雀官方每页最多 100 条估算",
            "增量任务包含已删除文档列表请求",
            "按本地已知文档数估算详情请求; 远端变化数和 Table 额外分页无法预先精确获知",
        ],
    )


def ensure_quota_sufficient(estimate: QuotaEstimateResponse) -> None:
    insufficient = [item for item in estimate.credentials if item.sufficient is False]
    if not insufficient:
        return
    item = insufficient[0]
    raise AppError(
        409,
        "RATE_LIMIT_INSUFFICIENT",
        (
            f"凭据“{item.credential_name}”最近一次语雀响应显示剩余额度 "
            f"{item.rate_limit_remaining}, 低于本次预计 {item.estimated_api_calls} 次请求"
        ),
    )


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
