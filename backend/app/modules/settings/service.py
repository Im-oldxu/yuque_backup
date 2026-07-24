from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.models import AppSetting, Asset, DocumentVersion, RetentionPolicy
from app.modules.settings.schemas import (
    RetentionSettingResponse,
    ScheduleSettingResponse,
    StorageSettingResponse,
    StorageUsageResponse,
)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _app_setting(db: Session) -> AppSetting:
    setting = db.get(AppSetting, 1)
    if setting is None:
        raise AppError(503, "SERVICE_UNAVAILABLE", "应用设置尚未初始化")
    return setting


def _retention_policy(db: Session) -> RetentionPolicy:
    policy = db.get(RetentionPolicy, 1)
    if policy is None:
        raise AppError(503, "SERVICE_UNAVAILABLE", "保留设置尚未初始化")
    return policy


def build_cron_trigger(cron: str, timezone_name: str) -> CronTrigger:
    try:
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AppError(422, "INVALID_TIMEZONE", "时区名称不合法") from exc

    try:
        return CronTrigger.from_crontab(cron, timezone=timezone)
    except ValueError as exc:
        raise AppError(422, "INVALID_CRON", "Cron 表达式不合法") from exc


def calculate_next_runs(
    cron: str,
    timezone_name: str,
    *,
    count: int = 3,
    now: datetime | None = None,
) -> list[datetime]:
    trigger = build_cron_trigger(cron, timezone_name)
    reference = as_utc(now or datetime.now(UTC))
    previous: datetime | None = None
    next_runs: list[datetime] = []

    for _ in range(count):
        next_fire_time = trigger.get_next_fire_time(previous, reference)
        if next_fire_time is None:
            raise AppError(422, "INVALID_CRON", "Cron 表达式无法生成后续运行时间")
        next_runs.append(next_fire_time.astimezone(UTC))
        previous = next_fire_time
        reference = next_fire_time

    return next_runs


def get_schedule(db: Session) -> ScheduleSettingResponse:
    setting = _app_setting(db)
    return ScheduleSettingResponse(
        cron=setting.cron,
        timezone=setting.timezone,
        next_runs=calculate_next_runs(setting.cron, setting.timezone),
        updated_at=as_utc(setting.updated_at),
    )


def update_schedule(db: Session, cron: str, timezone_name: str) -> ScheduleSettingResponse:
    next_runs = calculate_next_runs(cron, timezone_name)
    setting = _app_setting(db)
    setting.cron = cron
    setting.timezone = timezone_name
    setting.version += 1
    db.commit()
    db.refresh(setting)
    return ScheduleSettingResponse(
        cron=setting.cron,
        timezone=setting.timezone,
        next_runs=next_runs,
        updated_at=as_utc(setting.updated_at),
    )


def get_retention(db: Session) -> RetentionSettingResponse:
    policy = _retention_policy(db)
    return RetentionSettingResponse(
        retention_days=policy.retention_days,
        updated_at=as_utc(policy.updated_at),
    )


def update_retention(db: Session, retention_days: int) -> RetentionSettingResponse:
    policy = _retention_policy(db)
    policy.retention_days = retention_days
    db.commit()
    db.refresh(policy)
    return RetentionSettingResponse(
        retention_days=policy.retention_days,
        updated_at=as_utc(policy.updated_at),
    )


def _database_size(database_path: Path | None) -> int:
    if database_path is None:
        return 0
    try:
        return database_path.stat().st_size
    except FileNotFoundError:
        return 0


def get_storage_usage(db: Session, settings: Settings | None = None) -> StorageUsageResponse:
    settings = settings or get_settings()
    version_bytes = int(
        db.scalar(
            select(func.coalesce(func.sum(DocumentVersion.content_size_bytes), 0)).where(
                DocumentVersion.purged_at.is_(None)
            )
        )
        or 0
    )
    asset_bytes = int(
        db.scalar(select(func.coalesce(func.sum(Asset.size), 0)).where(Asset.purged_at.is_(None))) or 0
    )
    database_bytes = _database_size(settings.db_path)
    return StorageUsageResponse(
        database_bytes=database_bytes,
        version_bytes=version_bytes,
        asset_bytes=asset_bytes,
        total_bytes=database_bytes + version_bytes + asset_bytes,
    )


def get_storage(db: Session, settings: Settings | None = None) -> StorageSettingResponse:
    settings = settings or get_settings()
    app_setting = _app_setting(db)
    database_path = settings.db_path.parent if settings.db_path is not None else settings.data_root / "db"
    return StorageSettingResponse(
        database_path=str(database_path),
        content_path=str(settings.content_root),
        max_asset_size_bytes=app_setting.max_asset_size_bytes,
        max_asset_size_unlimited=app_setting.max_asset_size_bytes is None,
        usage=get_storage_usage(db, settings),
        updated_at=as_utc(app_setting.updated_at),
    )


def update_storage_limit(
    db: Session,
    max_asset_size_bytes: int | None,
    settings: Settings | None = None,
) -> StorageSettingResponse:
    app_setting = _app_setting(db)
    app_setting.max_asset_size_bytes = max_asset_size_bytes
    app_setting.version += 1
    db.commit()
    db.refresh(app_setting)
    return get_storage(db, settings)
