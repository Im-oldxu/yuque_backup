from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Page[T](APIModel):
    items: list[T]
    page: int
    page_size: int
    total: int


class ErrorField(APIModel):
    field: str
    reason: str


class ErrorResponse(APIModel):
    code: str
    message: str
    request_id: str
    field_errors: list[ErrorField] | None = None
    retry_after_seconds: int | None = None


class LiveHealthResponse(APIModel):
    status: Literal["ok"]


class ReadyHealthResponse(APIModel):
    status: Literal["ready"]


JobStatus = Literal["queued", "running", "waiting_quota", "succeeded", "partial", "failed", "cancelled"]
CredentialStatus = Literal["unverified", "valid", "waiting_quota", "action_required", "disabled"]
Completeness = Literal["complete", "partial", "failed"]
OperationStatus = Literal["queued", "running", "waiting_quota", "succeeded", "failed", "cancelled"]
DocumentType = Literal["Doc", "Sheet", "Thread", "Board", "Table", "HtmlDoc", "unknown"]


class PageParams(APIModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_iso(value: datetime | None) -> str | None:
    normalized = utc_datetime(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")
