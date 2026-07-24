from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScheduleUpdateRequest(StrictRequest):
    cron: str = Field(min_length=1, max_length=128)
    timezone: str = Field(min_length=1, max_length=64)


class ScheduleSettingResponse(BaseModel):
    cron: str
    timezone: str
    next_runs: list[datetime]
    updated_at: datetime


class RetentionUpdateRequest(StrictRequest):
    retention_days: int = Field(gt=0)


class RetentionSettingResponse(BaseModel):
    retention_days: int
    updated_at: datetime


class StorageLimitUpdateRequest(StrictRequest):
    max_asset_size_bytes: int | None = Field(gt=0)


class StorageUsageResponse(BaseModel):
    database_bytes: int
    version_bytes: int
    asset_bytes: int
    total_bytes: int


class StorageSettingResponse(BaseModel):
    database_path: str
    content_path: str
    max_asset_size_bytes: int | None
    max_asset_size_unlimited: bool
    usage: StorageUsageResponse
    updated_at: datetime
