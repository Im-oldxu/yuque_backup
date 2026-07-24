from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("APP_MASTER_KEY", "00" * 32)
os.environ.setdefault(
    "DATA_ROOT",
    str(Path(os.environ.get("TEMP", ".")) / "yuque-backup-scheduler-test-bootstrap"),
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import AppSetting, BackupJob, Base, JobTrigger
from app.worker.coordinator import JobCoordinator
from app.worker.main import SchedulerController
from app.worker.queue import PersistentQueue


@dataclass
class Clock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def make_controller(
    tmp_path: Path,
    clock: Clock,
    *,
    cron: str = "0 * * * *",
    timezone: str = "UTC",
    enabled: bool = True,
    updated_at: datetime,
) -> tuple[SchedulerController, AsyncIOScheduler, sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(
            AppSetting(
                id=1,
                cron=cron,
                timezone=timezone,
                schedule_enabled=enabled,
                version=1,
                updated_at=updated_at,
            )
        )

    queue = PersistentQueue(sessions, now=clock.now)
    coordinator = JobCoordinator(sessions, queue, now=clock.now)
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("UTC"))
    controller = SchedulerController(scheduler, coordinator, sessions, now=clock.now)
    return controller, scheduler, sessions


def test_startup_coalesces_all_missed_cron_runs_to_latest(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 23, 12, 30, tzinfo=UTC))
    controller, scheduler, sessions = make_controller(
        tmp_path,
        clock,
        updated_at=datetime(2026, 7, 23, 8, 15, tzinfo=UTC),
    )
    with sessions.begin() as session:
        session.add(
            JobTrigger(
                trigger="cron",
                idempotency_key="cron:2026-07-23T09:00:00+00:00",
                scope={"type": "all"},
                status="pending",
                created_at=datetime(2026, 7, 23, 9, 0, tzinfo=UTC),
            )
        )

    controller.refresh()
    job_id = controller.catch_up_due_cron()

    assert job_id is not None
    assert len(scheduler.get_jobs()) == 1
    with sessions() as session:
        keys = set(session.scalars(select(JobTrigger.idempotency_key)))
        assert keys == {
            "cron:2026-07-23T09:00:00+00:00",
            "cron:2026-07-23T12:00:00+00:00",
        }
        assert session.scalar(select(func.count()).select_from(BackupJob)) == 1


def test_repeated_startup_does_not_enqueue_same_cron_run(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 23, 12, 30, tzinfo=UTC))
    controller, _scheduler, sessions = make_controller(
        tmp_path,
        clock,
        updated_at=datetime(2026, 7, 23, 11, 30, tzinfo=UTC),
    )
    first_job_id = controller.catch_up_due_cron()
    assert first_job_id is not None

    queue = PersistentQueue(sessions, now=clock.now)
    coordinator = JobCoordinator(sessions, queue, now=clock.now)
    restarted = SchedulerController(
        AsyncIOScheduler(timezone=ZoneInfo("UTC")),
        coordinator,
        sessions,
        now=clock.now,
    )

    assert restarted.catch_up_due_cron() is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobTrigger)) == 1
        assert session.scalar(select(func.count()).select_from(BackupJob)) == 1


def test_startup_does_not_enqueue_cron_before_first_due_time(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 23, 12, 30, tzinfo=UTC))
    controller, scheduler, sessions = make_controller(
        tmp_path,
        clock,
        updated_at=datetime(2026, 7, 23, 12, 1, tzinfo=UTC),
    )

    controller.refresh()

    assert controller.catch_up_due_cron() is None
    assert len(scheduler.get_jobs()) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobTrigger)) == 0
        assert session.scalar(select(func.count()).select_from(BackupJob)) == 0


def test_disabled_schedule_neither_installs_nor_catches_up_cron(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 23, 12, 30, tzinfo=UTC))
    controller, scheduler, sessions = make_controller(
        tmp_path,
        clock,
        enabled=False,
        updated_at=datetime(2026, 7, 23, 8, 15, tzinfo=UTC),
    )

    controller.refresh()

    assert controller.catch_up_due_cron() is None
    assert scheduler.get_jobs() == []
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobTrigger)) == 0
        assert session.scalar(select(func.count()).select_from(BackupJob)) == 0


def test_startup_uses_latest_fall_back_fire_time_in_utc(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 1, 6, 45, tzinfo=UTC))
    controller, _scheduler, sessions = make_controller(
        tmp_path,
        clock,
        cron="30 1 * * *",
        timezone="America/New_York",
        updated_at=datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
    )

    assert controller.catch_up_due_cron() is not None
    with sessions() as session:
        assert session.scalar(select(JobTrigger.idempotency_key)) == ("cron:2026-11-01T06:30:00+00:00")
