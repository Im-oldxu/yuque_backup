from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid4_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Admin(Base, TimestampMixin):
    __tablename__ = "admin"
    __table_args__ = (CheckConstraint("singleton_key = 1", name="ck_admin_singleton"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    singleton_key: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, default=1)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    __tablename__ = "admin_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    admin_id: Mapped[str] = mapped_column(
        ForeignKey("admin.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    password_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LoginAttempt(Base):
    __tablename__ = "login_attempt"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("owner_key", "method", "path", "idempotency_key", name="uq_idempotency_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    owner_key: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class YuqueCredential(Base, TimestampMixin):
    __tablename__ = "yuque_credential"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    token_suffix: Mapped[str] = mapped_column(String(4), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    subject_id: Mapped[str | None] = mapped_column(String(128))
    login: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unverified", index=True)
    verification_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limit_limit: Mapped[int | None] = mapped_column(Integer)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    rate_limit_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pause_reason: Mapped[str | None] = mapped_column(String(255))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


Index(
    "uq_yuque_credential_active_name",
    YuqueCredential.name,
    unique=True,
    sqlite_where=YuqueCredential.deleted_at.is_(None),
)


class RateLimitBucket(Base, TimestampMixin):
    __tablename__ = "rate_limit_bucket"
    __table_args__ = (
        UniqueConstraint("base_url", "subject_type", "subject_id", name="uq_rate_bucket_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rate_limit_limit: Mapped[int | None] = mapped_column(Integer)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Operation(Base):
    __tablename__ = "operation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    credential_id: Mapped[str] = mapped_column(
        ForeignKey("yuque_credential.id", ondelete="CASCADE"), nullable=False, index=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_operation_active_credential_type",
    Operation.credential_id,
    Operation.type,
    unique=True,
    sqlite_where=Operation.status.in_(("queued", "running", "waiting_quota")),
)


class Repository(Base, TimestampMixin):
    __tablename__ = "repository"
    __table_args__ = (UniqueConstraint("normalized_base_url", "yuque_book_id", name="uq_repository_remote"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    normalized_base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    yuque_book_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(255))
    namespace: Mapped[str | None] = mapped_column(String(512))
    repo_type: Mapped[str | None] = mapped_column(String(64))
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    content_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    toc_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryCredential(Base):
    __tablename__ = "repository_credential"
    __table_args__ = (UniqueConstraint("repository_id", "credential_id", name="uq_repository_credential"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[str] = mapped_column(
        ForeignKey("yuque_credential.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    access_status: Mapped[str] = mapped_column(String(32), nullable=False, default="valid")
    last_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


Index(
    "uq_repository_primary_credential",
    RepositoryCredential.repository_id,
    unique=True,
    sqlite_where=RepositoryCredential.is_primary.is_(True),
)


class TocItem(Base):
    __tablename__ = "toc_item"
    __table_args__ = (UniqueConstraint("repository_id", "remote_id", name="uq_toc_remote"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False, index=True
    )
    remote_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_remote_id: Mapped[str | None] = mapped_column(String(255), index=True)
    yuque_doc_id: Mapped[str | None] = mapped_column(String(128), index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    path: Mapped[str] = mapped_column(String(2048), nullable=False, default="/")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Document(Base, TimestampMixin):
    __tablename__ = "document"
    __table_args__ = (UniqueConstraint("repository_id", "yuque_doc_id", name="uq_document_remote"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False, index=True
    )
    yuque_doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    slug: Mapped[str | None] = mapped_column(String(512), index=True)
    path: Mapped[str] = mapped_column(String(2048), nullable=False, default="/", index=True)
    original_path: Mapped[str] = mapped_column(String(2048), nullable=False, default="/")
    toc_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("toc_item.id", ondelete="SET NULL"), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_slug: Mapped[str | None] = mapped_column(String(512))
    latest_successful_version_id: Mapped[str | None] = mapped_column(String(36), index=True)
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupJob(Base):
    __tablename__ = "backup_job"
    __table_args__ = (
        CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_backup_job_active_slot"),
        CheckConstraint("pending_slot IS NULL OR pending_slot = 1", name="ck_backup_job_pending_slot"),
        UniqueConstraint("active_slot", name="uq_backup_job_active_slot"),
        UniqueConstraint("pending_slot", name="uq_backup_job_pending_slot"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    active_slot: Mapped[int | None] = mapped_column(Integer)
    pending_slot: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status_reason: Mapped[str | None] = mapped_column(String(255))
    document_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    waiting_quota_credentials: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_from: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    cleanup_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base):
    __tablename__ = "document_version"
    __table_args__ = (UniqueConstraint("document_id", "content_hash", name="uq_document_version_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
    )
    remote_version_id: Mapped[str | None] = mapped_column(String(128))
    format: Mapped[str | None] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    raw_response_path: Mapped[str | None] = mapped_column(String(2048))
    raw_body_path: Mapped[str | None] = mapped_column(String(2048))
    markdown_path: Mapped[str | None] = mapped_column(String(2048))
    preview_path: Mapped[str | None] = mapped_column(String(2048))
    manifest_path: Mapped[str | None] = mapped_column(String(2048))
    content_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    normalized_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resource_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_downloaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_job_id: Mapped[str] = mapped_column(
        ForeignKey("backup_job.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    storage_path: Mapped[str | None] = mapped_column(String(2048))
    checksum_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class VersionAsset(Base):
    __tablename__ = "version_asset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_version.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("asset.id", ondelete="SET NULL"), index=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    safe_url: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    declared_size: Mapped[int | None] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_location: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    issue_code: Mapped[str | None] = mapped_column(String(64))


class BackupSubtask(Base):
    __tablename__ = "backup_subtask"
    __table_args__ = (UniqueConstraint("job_id", "repository_id", name="uq_subtask_job_repository"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("backup_job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[str] = mapped_column(
        ForeignKey("yuque_credential.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repository.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    document_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_issue: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupIssue(Base):
    __tablename__ = "backup_issue"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("backup_job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subtask_id: Mapped[str | None] = mapped_column(
        ForeignKey("backup_subtask.id", ondelete="SET NULL"), index=True
    )
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("yuque_credential.id", ondelete="SET NULL"), index=True
    )
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repository.id", ondelete="SET NULL"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_version.id", ondelete="SET NULL"), index=True
    )
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("asset.id", ondelete="SET NULL"), index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_title: Mapped[str | None] = mapped_column(String(512))
    asset_type: Mapped[str | None] = mapped_column(String(32))
    safe_url: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class JobTrigger(Base):
    __tablename__ = "job_trigger"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_job_trigger_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("backup_job.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class QueueItem(Base):
    __tablename__ = "queue_item"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_queue_attempt_nonnegative"),
        UniqueConstraint("idempotency_key", name="uq_queue_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(1024))
    operation_id: Mapped[str | None] = mapped_column(
        ForeignKey("operation.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("backup_job.id", ondelete="CASCADE"), index=True)
    subtask_id: Mapped[str | None] = mapped_column(
        ForeignKey("backup_subtask.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("yuque_credential.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_queue_claim", QueueItem.status, QueueItem.available_at, QueueItem.priority)


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoint"
    __table_args__ = (UniqueConstraint("checkpoint_key", name="uq_checkpoint_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    checkpoint_key: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safe_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AppSetting(Base):
    __tablename__ = "app_setting"
    __table_args__ = (CheckConstraint("id = 1", name="ck_app_setting_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cron: Mapped[str] = mapped_column(String(128), nullable=False, default="0 2 * * *")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_asset_size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=524_288_000)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RetentionPolicy(Base):
    __tablename__ = "retention_policy"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_retention_policy_singleton"),
        CheckConstraint("retention_days > 0", name="ck_retention_days_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DeletionTombstone(Base):
    __tablename__ = "deletion_tombstone"
    __table_args__ = (
        UniqueConstraint(
            "base_url", "yuque_book_id", "yuque_doc_id", "deleted_at", name="uq_tombstone_remote"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repository.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    yuque_book_id: Mapped[str] = mapped_column(String(128), nullable=False)
    yuque_doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    original_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    deleted_slug: Mapped[str | None] = mapped_column(String(512))
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    purged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_job_id: Mapped[str] = mapped_column(
        ForeignKey("backup_job.id", ondelete="RESTRICT"), nullable=False
    )
    cleanup_job_id: Mapped[str] = mapped_column(String(36), nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"
    __table_args__ = (CheckConstraint("id = 1", name="ck_worker_heartbeat_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
