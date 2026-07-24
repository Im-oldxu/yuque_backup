from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import utc_datetime
from app.core.config import Settings
from app.core.errors import AppError
from app.core.models import (
    Operation,
    QueueItem,
    RepositoryCredential,
    YuqueCredential,
    utcnow,
)
from app.core.security import encrypt_token, mask_token
from app.modules.credentials.schemas import CredentialResponse, OperationResponse, RateLimitSnapshot
from app.worker.coordinator import aggregate_job_in_session

ACTIVE_OPERATION_STATUSES = ("queued", "running", "waiting_quota")


def normalize_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:
        raise AppError(422, "INVALID_BASE_URL", "API 基础域名不合法") from exc
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(422, "INVALID_BASE_URL", "API 基础域名必须是 HTTPS origin")
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise AppError(422, "INVALID_BASE_URL", "API 基础域名不合法") from exc
    if ":" in ascii_host and not ascii_host.startswith("["):
        ascii_host = f"[{ascii_host}]"
    netloc = ascii_host if port in {None, 443} else f"{ascii_host}:{port}"
    return urlunsplit(("https", netloc, "", "", ""))


def get_credential(db: Session, credential_id: str) -> YuqueCredential:
    credential = db.scalar(
        select(YuqueCredential).where(
            YuqueCredential.id == credential_id,
            YuqueCredential.deleted_at.is_(None),
        )
    )
    if credential is None:
        raise AppError(404, "CREDENTIAL_NOT_FOUND", "语雀凭据不存在")
    return credential


def serialize_operation(operation: Operation) -> OperationResponse:
    return OperationResponse(
        id=operation.id,
        type=operation.type,
        status=operation.status,
        credential_id=operation.credential_id,
        result=operation.result,
        error=operation.error,
        next_retry_at=utc_datetime(operation.next_retry_at),
        created_at=utc_datetime(operation.created_at),
        started_at=utc_datetime(operation.started_at),
        finished_at=utc_datetime(operation.finished_at),
    )


def serialize_credential(db: Session, credential: YuqueCredential) -> CredentialResponse:
    operation_id = db.scalar(
        select(Operation.id)
        .where(
            Operation.credential_id == credential.id,
            Operation.status.in_(ACTIVE_OPERATION_STATUSES),
        )
        .order_by(Operation.created_at.desc())
        .limit(1)
    )
    repository_count = (
        db.scalar(
            select(func.count(RepositoryCredential.id)).where(
                RepositoryCredential.credential_id == credential.id
            )
        )
        or 0
    )
    rate_limit = None
    if (
        credential.rate_limit_limit is not None
        and credential.rate_limit_remaining is not None
        and credential.rate_limit_observed_at is not None
    ):
        rate_limit = RateLimitSnapshot(
            limit=credential.rate_limit_limit,
            remaining=credential.rate_limit_remaining,
            observed_at=utc_datetime(credential.rate_limit_observed_at),
        )
    return CredentialResponse(
        id=credential.id,
        name=credential.name,
        base_url=credential.base_url,
        token_masked=f"{'*' * 12}{credential.token_suffix}",
        subject_type=credential.subject_type,
        subject_id=credential.subject_id,
        login=credential.login,
        status=credential.status,
        enabled=credential.enabled,
        last_verified_at=utc_datetime(credential.last_verified_at),
        rate_limit=rate_limit,
        next_retry_at=utc_datetime(credential.next_retry_at),
        active_operation_id=operation_id,
        repository_count=repository_count,
        created_at=utc_datetime(credential.created_at),
        updated_at=utc_datetime(credential.updated_at),
    )


def enqueue_operation(db: Session, credential: YuqueCredential, operation_type: str) -> Operation:
    existing = db.scalar(
        select(Operation).where(
            Operation.credential_id == credential.id,
            Operation.type == operation_type,
            Operation.status.in_(ACTIVE_OPERATION_STATUSES),
        )
    )
    if existing is not None:
        raise AppError(409, "OPERATION_ALREADY_RUNNING", "该操作已在执行")
    operation = Operation(type=operation_type, credential_id=credential.id, status="queued")
    db.add(operation)
    db.flush()
    db.add(
        QueueItem(
            category=operation_type,
            payload={"credential_id": credential.id},
            priority=10 if operation_type == "credential_verify" else 20,
            status="pending",
            available_at=utcnow(),
            idempotency_key=f"operation:{operation.id}",
            operation_id=operation.id,
            credential_id=credential.id,
        )
    )
    return operation


def create_credential(
    db: Session,
    *,
    name: str,
    base_url: str,
    token: str,
    settings: Settings,
) -> tuple[YuqueCredential, Operation]:
    normalized = normalize_base_url(base_url)
    if db.scalar(
        select(YuqueCredential.id).where(
            YuqueCredential.name == name,
            YuqueCredential.deleted_at.is_(None),
        )
    ):
        raise AppError(409, "CREDENTIAL_NAME_EXISTS", "凭据名称已存在")
    credential_id = str(uuid.uuid4())
    encrypted, nonce = encrypt_token(token, credential_id, settings)
    credential = YuqueCredential(
        id=credential_id,
        name=name,
        base_url=normalized,
        encrypted_token=encrypted,
        token_nonce=nonce,
        token_suffix=mask_token(token)[12:],
        key_version=1,
        subject_type="unknown",
        status="unverified",
        verification_valid=False,
        enabled=False,
    )
    db.add(credential)
    db.flush()
    return credential, enqueue_operation(db, credential, "credential_verify")


def cancel_credential_work(db: Session, credential_id: str) -> None:
    now = utcnow()
    cancelled_job_ids = set(
        db.scalars(
            update(QueueItem)
            .where(
                QueueItem.credential_id == credential_id,
                QueueItem.status.in_(("pending", "running", "retry_wait")),
            )
            .values(
                status="cancelled",
                finished_at=now,
                next_retry_at=None,
                lease_owner=None,
                lease_until=None,
            )
            .returning(QueueItem.job_id)
        )
    )
    db.execute(
        update(Operation)
        .where(
            Operation.credential_id == credential_id,
            Operation.status.in_(ACTIVE_OPERATION_STATUSES),
        )
        .values(status="cancelled", finished_at=now, next_retry_at=None)
    )
    for job_id in sorted(value for value in cancelled_job_ids if value is not None):
        aggregate_job_in_session(db, job_id, current=now)


def commit_or_conflict(db: Session, *, duplicate_message: str = "数据状态冲突") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        message = str(exc.orig).lower()
        if "yuque_credential" in message and "name" in message:
            raise AppError(409, "CREDENTIAL_NAME_EXISTS", "凭据名称已存在") from exc
        if "operation" in message:
            raise AppError(409, "OPERATION_ALREADY_RUNNING", "该操作已在执行") from exc
        raise AppError(409, "CONFLICT", duplicate_message) from exc
