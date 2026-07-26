from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import (
    BackupJob,
    BackupSubtask,
    JobTrigger,
    QueueItem,
    Repository,
    RepositoryCredential,
    WorkerHeartbeat,
    YuqueCredential,
)
from app.modules.backups.service import estimate_scope_quota
from app.worker.queue import PersistentQueue, next_quota_retry_at

_TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
_NONTERMINAL = {"queued", "running", "waiting_quota"}
_ACTIVE_QUEUE_STATUSES = ("pending", "running", "retry_wait")


def aggregate_job_in_session(session: Session, job_id: str, *, current: datetime) -> str | None:
    job = session.get(BackupJob, job_id)
    if job is None:
        return None
    subtasks = list(session.scalars(select(BackupSubtask).where(BackupSubtask.job_id == job_id)))
    if not subtasks:
        return job.status

    nonterminal_ids = [item.id for item in subtasks if item.status in _NONTERMINAL]
    if nonterminal_ids:
        active_ids = set(
            session.scalars(
                select(QueueItem.subtask_id)
                .where(
                    QueueItem.subtask_id.in_(nonterminal_ids),
                    QueueItem.status.in_(_ACTIVE_QUEUE_STATUSES),
                )
                .distinct()
            )
        )
        cancelled_ids = set(
            session.scalars(
                select(QueueItem.subtask_id)
                .where(
                    QueueItem.subtask_id.in_(nonterminal_ids),
                    QueueItem.status == "cancelled",
                )
                .distinct()
            )
        )
        for subtask in subtasks:
            if (
                subtask.id in nonterminal_ids
                and subtask.id in cancelled_ids
                and subtask.id not in active_ids
            ):
                subtask.status = "failed"
                subtask.next_retry_at = None
                subtask.finished_at = current

    job.document_total = sum(item.document_total for item in subtasks)
    job.document_succeeded = sum(item.document_succeeded for item in subtasks)
    job.document_partial = sum(item.document_partial for item in subtasks)
    job.document_failed = sum(item.document_failed for item in subtasks)
    job.asset_total = sum(item.asset_total for item in subtasks)
    job.asset_succeeded = sum(item.asset_succeeded for item in subtasks)
    job.asset_failed = sum(item.asset_failed for item in subtasks)
    job.issue_count = sum(item.issue_count for item in subtasks)
    incomplete = [item for item in subtasks if item.status not in _TERMINAL]
    completed_documents = sum(item.document_completed for item in subtasks)
    job.progress = min(1.0, completed_documents / job.document_total) if job.document_total else 0.0
    if incomplete:
        waiting = [item for item in incomplete if item.status == "waiting_quota"]
        job.waiting_quota_credentials = len({item.credential_id for item in waiting})
        if waiting and len(waiting) == len(incomplete):
            job.status = "waiting_quota"
            retry_values = [item.next_retry_at for item in waiting if item.next_retry_at]
            job.next_retry_at = min(retry_values) if retry_values else None
        else:
            job.status = "running"
            job.next_retry_at = None
        return job.status

    job.waiting_quota_credentials = 0
    job.next_retry_at = None
    if job.cancel_requested_at is not None or all(item.status == "cancelled" for item in subtasks):
        final_status = "cancelled"
    elif all(item.status == "succeeded" for item in subtasks):
        final_status = "succeeded"
    elif any(
        item.status in {"succeeded", "partial"}
        or item.document_succeeded > 0
        or item.document_partial > 0
        for item in subtasks
    ):
        final_status = "partial"
    else:
        final_status = "failed"
    job.status = final_status
    job.progress = 1.0
    job.finished_at = current
    job.active_slot = None
    job.pending_slot = None
    return final_status


class JobCoordinator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: PersistentQueue,
        *,
        now: Callable[[], datetime] | None = None,
        quota_timezone: str = "Asia/Shanghai",
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._now = now or (lambda: datetime.now(UTC))
        self._quota_timezone = quota_timezone

    def enqueue_cron_job(self, *, idempotency_key: str) -> str:
        current = self._now()
        with self._session_factory.begin() as session:
            existing_trigger = session.scalar(
                select(JobTrigger).where(JobTrigger.idempotency_key == idempotency_key)
            )
            if existing_trigger and existing_trigger.job_id:
                return existing_trigger.job_id
            cron_scope: dict[str, Any] = {"type": "all"}
            target_ids = sorted(
                {repository_id for repository_id, _credential_id in self._targets(session, cron_scope)}
            )
            queued = session.scalar(select(BackupJob).where(BackupJob.status == "queued"))
            if queued is None:
                queued = BackupJob(
                    trigger="cron",
                    scope={**cron_scope, "_target_repository_ids": target_ids},
                    status="queued",
                    pending_slot=1,
                    active_slot=None,
                    created_at=current,
                )
                session.add(queued)
                session.flush()
            else:
                existing_targets = {
                    str(value) for value in queued.scope.get("_target_repository_ids", [])
                }
                queued.scope = {
                    **cron_scope,
                    "_target_repository_ids": sorted(existing_targets | set(target_ids)),
                }
                queued.merged_from = [*queued.merged_from, idempotency_key]
            trigger = existing_trigger or JobTrigger(
                trigger="cron",
                idempotency_key=idempotency_key,
                scope={"type": "all"},
            )
            trigger.status = "merged" if existing_trigger or queued.trigger != "cron" else "pending"
            trigger.job_id = queued.id
            if existing_trigger is None:
                session.add(trigger)
            return queued.id

    def promote_pending_job(self) -> str | None:
        current = self._now()
        with self._session_factory.begin() as session:
            active = session.scalar(
                select(BackupJob).where(BackupJob.status.in_(("running", "waiting_quota")))
            )
            if active is not None:
                return active.id
            job = session.scalar(
                select(BackupJob)
                .where(BackupJob.status == "queued", BackupJob.pending_slot == 1)
                .order_by(BackupJob.created_at.asc())
                .limit(1)
            )
            if job is None:
                return None
            targets = self._targets(session, job.scope)
            job.pending_slot = None
            if not targets:
                job.status = "failed"
                job.status_reason = "NO_ENABLED_TARGETS"
                job.finished_at = current
                job.active_slot = None
                return job.id
            job.status = "running"
            job.active_slot = 1
            job.started_at = current
            for repository_id, credential_id in targets:
                credential = session.get(YuqueCredential, credential_id)
                target_estimate = (
                    estimate_scope_quota(
                        session,
                        {
                            "type": "repositories",
                            "credential_id": credential_id,
                            "repository_ids": [repository_id],
                        },
                        now=current,
                    ).credentials[0]
                    if credential is not None and credential.status == "valid"
                    else None
                )
                subtask = session.scalar(
                    select(BackupSubtask).where(
                        BackupSubtask.job_id == job.id,
                        BackupSubtask.repository_id == repository_id,
                    )
                )
                if subtask is None:
                    subtask = BackupSubtask(
                        job_id=job.id,
                        repository_id=repository_id,
                        credential_id=credential_id,
                        status="queued",
                    )
                    session.add(subtask)
                    session.flush()
                available_at = current
                if credential is not None and credential.status == "waiting_quota":
                    available_at = next_quota_retry_at(
                        current,
                        timezone_name=self._quota_timezone,
                    )
                    if credential.next_retry_at is not None:
                        stored_retry = credential.next_retry_at
                        if stored_retry.tzinfo is None:
                            stored_retry = stored_retry.replace(tzinfo=UTC)
                        else:
                            stored_retry = stored_retry.astimezone(UTC)
                        available_at = max(available_at, stored_retry)
                    subtask.status = "waiting_quota"
                    subtask.next_retry_at = available_at
                    subtask.last_issue = credential.pause_reason or "语雀额度不足, 等待下次探测"
                elif target_estimate is not None and target_estimate.sufficient is False:
                    available_at = next_quota_retry_at(
                        current,
                        timezone_name=self._quota_timezone,
                    )
                    subtask.status = "waiting_quota"
                    subtask.next_retry_at = available_at
                    subtask.last_issue = (
                        f"剩余额度 {target_estimate.rate_limit_remaining}, "
                        f"低于预计 {target_estimate.estimated_api_calls} 次请求"
                    )
                idempotency_key = f"job:{job.id}:repository:{repository_id}"
                existing_item = session.scalar(
                    select(QueueItem.id).where(QueueItem.idempotency_key == idempotency_key)
                )
                if existing_item is None:
                    session.add(
                        QueueItem(
                            category="repository_sync",
                            idempotency_key=idempotency_key,
                            payload={"stage": "metadata", "candidate_watermark": _iso(current)},
                            priority=50,
                            available_at=available_at,
                            job_id=job.id,
                            subtask_id=subtask.id,
                            credential_id=credential_id,
                            repository_id=repository_id,
                        )
                    )
            return job.id

    def apply_cancellations(self) -> int:
        current = self._now()
        with self._session_factory() as session:
            job_ids = list(
                session.scalars(
                    select(BackupJob.id).where(
                        BackupJob.cancel_requested_at.is_not(None),
                        BackupJob.status.in_(tuple(_NONTERMINAL)),
                    )
                )
            )
        cancelled = 0
        for job_id in job_ids:
            self._queue.cancel_by_job(job_id)
            with self._session_factory.begin() as session:
                job = session.get(BackupJob, job_id)
                if job is None or job.status not in _NONTERMINAL:
                    continue
                subtasks = list(session.scalars(select(BackupSubtask).where(BackupSubtask.job_id == job_id)))
                for subtask in subtasks:
                    if subtask.status in _NONTERMINAL:
                        subtask.status = "cancelled"
                        subtask.finished_at = current
                job.status = "cancelled"
                job.status_reason = "CANCEL_REQUESTED"
                job.finished_at = current
                job.active_slot = None
                job.pending_slot = None
                cancelled += 1
        return cancelled

    def aggregate_job(self, job_id: str) -> str | None:
        current = self._now()
        with self._session_factory.begin() as session:
            return aggregate_job_in_session(session, job_id, current=current)

    def heartbeat(self, worker_id: str, *, started_at: datetime) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            heartbeat = session.get(WorkerHeartbeat, 1)
            if heartbeat is None:
                session.add(
                    WorkerHeartbeat(
                        id=1,
                        worker_id=worker_id,
                        started_at=started_at,
                        last_heartbeat_at=current,
                    )
                )
            else:
                heartbeat.worker_id = worker_id
                heartbeat.started_at = started_at
                heartbeat.last_heartbeat_at = current

    @staticmethod
    def _targets(session: Session, scope: dict[str, Any]) -> list[tuple[str, str]]:
        query = (
            select(Repository.id, RepositoryCredential.credential_id)
            .join(RepositoryCredential, RepositoryCredential.repository_id == Repository.id)
            .join(YuqueCredential, YuqueCredential.id == RepositoryCredential.credential_id)
            .where(
                RepositoryCredential.is_primary.is_(True),
                YuqueCredential.enabled.is_(True),
                YuqueCredential.deleted_at.is_(None),
                YuqueCredential.verification_valid.is_(True),
                YuqueCredential.status.in_(("valid", "waiting_quota")),
            )
        )
        scope_type = scope.get("type")
        if scope_type == "credential":
            query = query.where(
                Repository.selected.is_(True),
                RepositoryCredential.credential_id == scope.get("credential_id"),
            )
        elif scope_type == "repository":
            query = query.where(Repository.id == scope.get("repository_id"))
        elif scope_type == "repositories":
            query = query.where(
                RepositoryCredential.credential_id == scope.get("credential_id"),
                Repository.id.in_(str(value) for value in scope.get("repository_ids", [])),
            )
        elif scope_type == "all":
            query = query.where(Repository.selected.is_(True))
        else:
            return []
        target_ids = scope.get("_target_repository_ids")
        if isinstance(target_ids, list):
            query = query.where(Repository.id.in_(str(value) for value in target_ids))
        return [(repository_id, credential_id) for repository_id, credential_id in session.execute(query)]


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
