from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import BackupJob
from app.modules.retention import RetentionService
from app.storage import AssetDownloader, ContentStore
from app.worker.coordinator import JobCoordinator
from app.worker.queue import PersistentQueue
from app.worker.sync import SyncExecutor, TokenResolver

logger = logging.getLogger(__name__)


def _is_database_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


class WorkerService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        worker_id: str | None = None,
        token_resolver: TokenResolver | None = None,
        yuque_http_client: httpx.AsyncClient | None = None,
        resource_http_client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self.started_at = (now or (lambda: datetime.now(UTC)))()
        self._now = now or (lambda: datetime.now(UTC))
        self._next_heartbeat_at: datetime | None = None
        timeout = httpx.Timeout(
            settings.http_read_timeout_seconds,
            connect=settings.http_connect_timeout_seconds,
            write=settings.http_write_timeout_seconds,
        )
        self._yuque_http_client = yuque_http_client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        self._resource_http_client = resource_http_client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=settings.resource_download_concurrency,
                max_keepalive_connections=settings.resource_download_concurrency,
            ),
        )
        self._owns_yuque_client = yuque_http_client is None
        self._owns_resource_client = resource_http_client is None
        self.store = ContentStore(settings.data_root)
        self.queue = PersistentQueue(
            session_factory,
            now=self._now,
            quota_timezone=settings.tz,
        )
        self.coordinator = JobCoordinator(
            session_factory,
            self.queue,
            now=self._now,
            quota_timezone=settings.tz,
        )
        self.asset_downloader = AssetDownloader(
            self.store,
            client=self._resource_http_client,
            redirect_limit=settings.resource_redirect_limit,
            timeout=timeout,
        )
        self.executor = SyncExecutor(
            session_factory,
            self.queue,
            self.store,
            settings,
            yuque_http_client=self._yuque_http_client,
            asset_downloader=self.asset_downloader,
            token_resolver=token_resolver,
            now=self._now,
        )
        self.retention = RetentionService(session_factory, self.store, now=self._now)
        self._session_factory = session_factory

    async def run_once(self) -> bool:
        try:
            return await self._run_once()
        except OperationalError as exc:
            if not _is_database_locked(exc):
                raise
            logger.warning("SQLite write lock delayed the worker loop; retrying", exc_info=exc)
            return False

    async def _run_once(self) -> bool:
        current = self._now()
        if self._next_heartbeat_at is None or current >= self._next_heartbeat_at:
            self.coordinator.heartbeat(self.worker_id, started_at=self.started_at)
            self._next_heartbeat_at = current + timedelta(
                seconds=self.settings.worker_heartbeat_seconds
            )
        self.queue.recover_expired()
        self.coordinator.apply_cancellations()
        self.coordinator.promote_pending_job()
        item = self.queue.claim(
            self.worker_id,
            lease_seconds=self.settings.queue_lease_seconds,
        )
        if item is None:
            self._aggregate_active_jobs()
            return False
        await self.executor.handle(item, self.worker_id)
        if item.job_id:
            status = self.coordinator.aggregate_job(item.job_id)
            if status in {"succeeded", "partial", "failed"}:
                self.retention.run(cleanup_job_id=item.job_id)
        return True

    async def run_until_idle(self, *, max_items: int = 10_000) -> int:
        handled = 0
        while handled < max_items and await self.run_once():
            handled += 1
        return handled

    async def aclose(self) -> None:
        if self._owns_yuque_client:
            await self._yuque_http_client.aclose()
        if self._owns_resource_client:
            await self._resource_http_client.aclose()

    def _aggregate_active_jobs(self) -> None:
        with self._session_factory() as session:
            job_ids = list(
                session.scalars(
                    select(BackupJob.id).where(BackupJob.status.in_(("running", "waiting_quota")))
                )
            )
        for job_id in job_ids:
            self.coordinator.aggregate_job(job_id)
