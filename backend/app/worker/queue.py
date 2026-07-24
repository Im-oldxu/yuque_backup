from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import BackupJob, Operation, QueueItem, YuqueCredential

TRANSIENT_RETRY_DELAYS = (2, 10, 30)
QUOTA_MAX_DELAY_SECONDS = 3600
_ACTIVE_STATUSES = ("pending", "running", "retry_wait")
_RUNNABLE_JOB_STATUSES = {"queued", "running", "waiting_quota"}
_RUNNABLE_OPERATION_STATUSES = {"queued", "running", "waiting_quota"}
_ACTIVE_CREDENTIAL_CATEGORIES = {
    "repository_discovery",
    "repository_sync",
    "document_sync",
}


class QueueLeaseLost(RuntimeError):
    """The queue item reached a terminal state outside the current worker."""


@dataclass(frozen=True, slots=True)
class QueueItemSnapshot:
    id: str
    category: str
    payload: dict[str, Any]
    priority: int
    status: str
    attempt_count: int
    operation_id: str | None
    job_id: str | None
    subtask_id: str | None
    credential_id: str | None
    repository_id: str | None
    document_id: str | None
    lease_owner: str | None
    lease_until: datetime | None

    @classmethod
    def from_model(cls, item: QueueItem) -> QueueItemSnapshot:
        return cls(
            id=item.id,
            category=item.category,
            payload=dict(item.payload),
            priority=item.priority,
            status=item.status,
            attempt_count=item.attempt_count,
            operation_id=item.operation_id,
            job_id=item.job_id,
            subtask_id=item.subtask_id,
            credential_id=item.credential_id,
            repository_id=item.repository_id,
            document_id=item.document_id,
            lease_owner=item.lease_owner,
            lease_until=item.lease_until,
        )


class PersistentQueue:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(UTC))

    def enqueue(
        self,
        category: str,
        *,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        priority: int = 100,
        available_at: datetime | None = None,
        operation_id: str | None = None,
        job_id: str | None = None,
        subtask_id: str | None = None,
        credential_id: str | None = None,
        repository_id: str | None = None,
        document_id: str | None = None,
    ) -> QueueItemSnapshot:
        with self._session_factory.begin() as session:
            existing = session.scalar(select(QueueItem).where(QueueItem.idempotency_key == idempotency_key))
            if existing is not None:
                return QueueItemSnapshot.from_model(existing)
            item = QueueItem(
                category=category,
                idempotency_key=idempotency_key,
                payload=payload or {},
                priority=priority,
                available_at=available_at or self._now(),
                operation_id=operation_id,
                job_id=job_id,
                subtask_id=subtask_id,
                credential_id=credential_id,
                repository_id=repository_id,
                document_id=document_id,
            )
            session.add(item)
            session.flush()
            return QueueItemSnapshot.from_model(item)

    def recover_expired(self, *, now: datetime | None = None) -> int:
        current = now or self._now()
        with self._session_factory.begin() as session:
            items = list(
                session.scalars(
                    select(QueueItem).where(
                    QueueItem.status == "running",
                    QueueItem.lease_until.is_not(None),
                    QueueItem.lease_until <= current,
                )
                )
            )
            for item in items:
                if self._is_runnable(session, item):
                    item.status = "pending"
                    item.available_at = current
                    item.lease_owner = None
                    item.lease_until = None
                    item.last_error_code = "LEASE_EXPIRED"
                    item.last_error_message = "Worker lease expired before completion"
                else:
                    self._cancel_item(item, current)
            return len(items)

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
        categories: set[str] | None = None,
    ) -> QueueItemSnapshot | None:
        current = now or self._now()
        with self._session_factory.begin() as session:
            eligible = or_(
                and_(QueueItem.status == "pending", QueueItem.available_at <= current),
                and_(
                    QueueItem.status == "retry_wait",
                    QueueItem.next_retry_at.is_not(None),
                    QueueItem.next_retry_at <= current,
                ),
                and_(
                    QueueItem.status == "running",
                    QueueItem.lease_until.is_not(None),
                    QueueItem.lease_until <= current,
                ),
            )
            query = select(QueueItem).where(eligible)
            if categories:
                query = query.where(QueueItem.category.in_(categories))
            query = query.order_by(
                QueueItem.priority.asc(), QueueItem.available_at.asc(), QueueItem.created_at.asc()
            ).limit(1)
            while True:
                item = session.scalar(query)
                if item is None:
                    return None
                if not self._is_runnable(session, item):
                    self._cancel_item(item, current)
                    session.flush()
                    continue
                item.status = "running"
                item.lease_owner = worker_id
                item.lease_until = current + timedelta(seconds=lease_seconds)
                item.next_retry_at = None
                item.updated_at = current
                session.flush()
                return QueueItemSnapshot.from_model(item)

    def record_attempt(self, item_id: str, worker_id: str) -> int:
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            item.attempt_count += 1
            session.flush()
            return item.attempt_count

    def complete(self, item_id: str, worker_id: str) -> None:
        self._finish(item_id, worker_id, "succeeded")

    def cancel_owned(self, item_id: str, worker_id: str, *, code: str, message: str) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            self._cancel_item(item, current, code=code, message=message)

    def fail(self, item_id: str, worker_id: str, *, code: str, message: str) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            item.status = "failed"
            item.last_error_code = code[:64]
            item.last_error_message = message[:1024]
            item.finished_at = current
            item.lease_owner = None
            item.lease_until = None
            item.next_retry_at = None

    def retry_transient(
        self,
        item_id: str,
        worker_id: str,
        *,
        code: str,
        message: str,
    ) -> datetime | None:
        current = self._now()
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            delay = transient_retry_delay(item.attempt_count)
            if delay is None:
                item.status = "failed"
                item.finished_at = current
                item.next_retry_at = None
            else:
                item.status = "retry_wait"
                item.next_retry_at = current + timedelta(seconds=delay)
                item.available_at = item.next_retry_at
            item.last_error_code = code[:64]
            item.last_error_message = message[:1024]
            item.lease_owner = None
            item.lease_until = None
            return item.next_retry_at

    def retry_quota(
        self,
        item_id: str,
        worker_id: str,
        *,
        retry_after_seconds: int | None,
        code: str = "YUQUE_RATE_LIMITED",
    ) -> datetime:
        current = self._now()
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            payload = dict(item.payload)
            quota_attempt = int(payload.get("_quota_attempt", 0)) + 1
            payload["_quota_attempt"] = quota_attempt
            fallback = min(60 * (2 ** (quota_attempt - 1)), QUOTA_MAX_DELAY_SECONDS)
            delay = retry_after_seconds if retry_after_seconds is not None else fallback
            next_retry_at = current + timedelta(seconds=max(0, delay))
            item.payload = payload
            item.status = "retry_wait"
            item.next_retry_at = next_retry_at
            item.available_at = next_retry_at
            item.last_error_code = code
            item.last_error_message = "Waiting for Yuque API quota"
            item.lease_owner = None
            item.lease_until = None
            return next_retry_at

    def continue_with_payload(
        self,
        item_id: str,
        worker_id: str,
        payload: dict[str, Any],
        *,
        available_at: datetime | None = None,
        priority: int | None = None,
    ) -> None:
        current = self._now()
        payload = {key: value for key, value in payload.items() if key != "_quota_attempt"}
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            item.payload = payload
            if priority is not None:
                item.priority = priority
            item.status = "pending"
            item.attempt_count = 0
            item.available_at = available_at or current
            item.next_retry_at = None
            item.lease_owner = None
            item.lease_until = None
            item.last_error_code = None
            item.last_error_message = None

    def cancel_by_job(self, job_id: str) -> int:
        current = self._now()
        with self._session_factory.begin() as session:
            result = session.connection().execute(
                update(QueueItem)
                .where(
                    QueueItem.job_id == job_id,
                    QueueItem.status.in_(_ACTIVE_STATUSES),
                )
                .values(
                    status="cancelled",
                    finished_at=current,
                    next_retry_at=None,
                    lease_owner=None,
                    lease_until=None,
                )
            )
            return result.rowcount or 0

    def extend_lease(self, item_id: str, worker_id: str, *, lease_seconds: int) -> None:
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            item.lease_until = self._now() + timedelta(seconds=lease_seconds)

    def _finish(self, item_id: str, worker_id: str, status: str) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            item = self._owned(session, item_id, worker_id)
            item.status = status
            item.finished_at = current
            item.next_retry_at = None
            item.lease_owner = None
            item.lease_until = None
            item.last_error_code = None
            item.last_error_message = None

    @staticmethod
    def _owned(session: Session, item_id: str, worker_id: str) -> QueueItem:
        item = session.get(QueueItem, item_id)
        if item is None:
            raise LookupError("queue item does not exist")
        if item.status != "running" or item.lease_owner != worker_id:
            raise QueueLeaseLost("queue item is no longer leased by this worker")
        return item

    @staticmethod
    def _cancel_item(
        item: QueueItem,
        current: datetime,
        *,
        code: str = "WORK_CANCELLED",
        message: str = "Queue work was cancelled before completion",
    ) -> None:
        item.status = "cancelled"
        item.finished_at = current
        item.next_retry_at = None
        item.lease_owner = None
        item.lease_until = None
        item.last_error_code = code[:64]
        item.last_error_message = message[:1024]

    @staticmethod
    def _is_runnable(session: Session, item: QueueItem) -> bool:
        if item.job_id is not None:
            job = session.get(BackupJob, item.job_id)
            if (
                job is None
                or job.cancel_requested_at is not None
                or job.status not in _RUNNABLE_JOB_STATUSES
            ):
                return False
        if item.operation_id is not None:
            operation = session.get(Operation, item.operation_id)
            if operation is None or operation.status not in _RUNNABLE_OPERATION_STATUSES:
                return False
        if item.credential_id is not None:
            credential = session.get(YuqueCredential, item.credential_id)
            if credential is None or credential.deleted_at is not None:
                return False
            if item.category in _ACTIVE_CREDENTIAL_CATEGORIES and (
                not credential.enabled
                or not credential.verification_valid
                or credential.status not in {"valid", "waiting_quota"}
            ):
                return False
        return True


def transient_retry_delay(attempt_count: int) -> int | None:
    if attempt_count <= 0:
        raise ValueError("attempt_count must include the failed attempt")
    index = attempt_count - 1
    return TRANSIENT_RETRY_DELAYS[index] if index < len(TRANSIENT_RETRY_DELAYS) else None
