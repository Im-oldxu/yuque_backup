from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InitializationStatus(StrictModel):
    initialized: bool


class InitializeRequest(StrictModel):
    username: str = Field(json_schema_extra={"minLength": 3, "maxLength": 64})
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not 3 <= len(value) <= 64:
            raise ValueError("username must contain between 3 and 64 characters")
        return value


class LoginRequest(StrictModel):
    username: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 64:
            raise ValueError("username must contain between 1 and 64 characters")
        return value


class PasswordChangeRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AdminResponse(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    username: str
    created_at: datetime
    password_changed_at: datetime | None

    @field_validator("id", mode="before")
    @classmethod
    def parse_id(cls, value: UUID | str) -> UUID:
        return value if isinstance(value, UUID) else UUID(value)

    @field_validator("created_at", "password_changed_at", mode="before")
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
