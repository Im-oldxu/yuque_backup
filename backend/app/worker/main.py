from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, engine, ping_database
from app.core.logging import configure_logging
from app.core.migrations import database_is_at_head
from app.core.models import AppSetting, JobTrigger
from app.worker.coordinator import JobCoordinator
from app.worker.service import WorkerService

logger = logging.getLogger(__name__)

_CRON_KEY_PREFIX = "cron:"
_SEARCH_RESOLUTION = timedelta(microseconds=1)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _cron_idempotency_key(scheduled_for: datetime) -> str:
    scheduled_utc = _as_utc(scheduled_for).replace(second=0, microsecond=0)
    return f"{_CRON_KEY_PREFIX}{scheduled_utc.isoformat(timespec='seconds')}"


def _parse_cron_idempotency_key(value: str) -> datetime | None:
    if not value.startswith(_CRON_KEY_PREFIX):
        return None
    try:
        scheduled_for = datetime.fromisoformat(value.removeprefix(_CRON_KEY_PREFIX))
    except ValueError:
        return None
    if scheduled_for.tzinfo is None:
        return None
    return scheduled_for.astimezone(UTC)


def _next_fire_time(trigger: CronTrigger, reference: datetime) -> datetime | None:
    next_fire_time = trigger.get_next_fire_time(None, _as_utc(reference))
    return _as_utc(next_fire_time) if next_fire_time is not None else None


def _latest_due_fire_time(
    trigger: CronTrigger,
    *,
    after: datetime,
    now: datetime,
) -> datetime | None:
    lower = _as_utc(after) + _SEARCH_RESOLUTION
    upper_bound = _as_utc(now)
    if lower > upper_bound:
        return None

    latest = _next_fire_time(trigger, lower)
    if latest is None or latest > upper_bound:
        return None

    # The predicate "there is a fire time between this instant and now" is monotonic.
    # Locate its upper edge instead of scanning every missed minute after a long outage.
    upper = upper_bound + _SEARCH_RESOLUTION
    while upper - lower > _SEARCH_RESOLUTION:
        reference = lower + (upper - lower) / 2
        candidate = _next_fire_time(trigger, reference)
        if candidate is not None and candidate <= upper_bound:
            lower = reference
            latest = candidate
        else:
            upper = reference
    return latest


class SchedulerController:
    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        coordinator: JobCoordinator,
        session_factory: sessionmaker[Session],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._coordinator = coordinator
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._version: int | None = None

    def refresh(self) -> None:
        with self._session_factory() as session:
            setting = session.get(AppSetting, 1)
            if setting is None:
                cron, timezone, enabled, version = "0 2 * * *", "Asia/Shanghai", True, 0
            else:
                cron, timezone = setting.cron, setting.timezone
                enabled, version = setting.schedule_enabled, setting.version
        if version == self._version:
            return
        self._scheduler.remove_all_jobs()
        if enabled:
            trigger = CronTrigger.from_crontab(cron, timezone=ZoneInfo(timezone))
            self._scheduler.add_job(
                self._enqueue_cron,
                trigger=trigger,
                id="yuque-backup-cron",
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
        self._version = version

    def catch_up_due_cron(self) -> str | None:
        current = _as_utc(self._now())
        with self._session_factory() as session:
            setting = session.get(AppSetting, 1)
            if setting is None or not setting.schedule_enabled:
                return None
            cron = setting.cron
            timezone = setting.timezone
            baseline = _as_utc(setting.updated_at)
            idempotency_keys = session.scalars(
                select(JobTrigger.idempotency_key).where(JobTrigger.trigger == "cron")
            )
            for idempotency_key in idempotency_keys:
                scheduled_for = _parse_cron_idempotency_key(idempotency_key)
                if scheduled_for is not None and baseline < scheduled_for <= current:
                    baseline = scheduled_for

        trigger = CronTrigger.from_crontab(cron, timezone=ZoneInfo(timezone))
        scheduled_for = _latest_due_fire_time(trigger, after=baseline, now=current)
        if scheduled_for is None:
            return None
        return self._coordinator.enqueue_cron_job(idempotency_key=_cron_idempotency_key(scheduled_for))

    def _enqueue_cron(self) -> None:
        self._coordinator.enqueue_cron_job(idempotency_key=_cron_idempotency_key(self._now()))


async def serve(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    _master_key = settings.master_key
    settings.ensure_directories()
    ping_database(require_write=True)
    if not database_is_at_head(engine):
        raise RuntimeError("database schema is not at the Alembic head; run migrations before startup")
    service = WorkerService(SessionLocal, settings)
    service.queue.recover_expired()
    service.coordinator.apply_cancellations()
    service.coordinator.promote_pending_job()
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.tz))
    controller = SchedulerController(scheduler, service.coordinator, SessionLocal)
    controller.refresh()
    scheduler.start(paused=True)
    try:
        controller.catch_up_due_cron()
        scheduler.resume()
        service.retention.run()
        while True:
            handled = await service.run_once()
            controller.refresh()
            if not handled:
                await asyncio.sleep(settings.queue_poll_seconds)
    finally:
        scheduler.shutdown(wait=False)
        await service.aclose()


def run() -> None:
    configure_logging()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    run()
