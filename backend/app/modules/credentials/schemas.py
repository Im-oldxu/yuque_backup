from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictStr, model_validator
from pydantic.json_schema import SkipJsonSchema

from app.api.schemas import APIModel, CredentialStatus, OperationStatus

HTTPS_ORIGIN_SCHEMA: dict[str, Any] = {
    "format": "uri",
    "pattern": r"^https://(?![^/?#]*@)[^/?#]+/?$",
    "description": "HTTPS origin without user info, path, query, or fragment.",
}


def _credential_patch_openapi(schema: dict[str, Any]) -> None:
    schema["minProperties"] = 1
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return
    for property_schema in properties.values():
        if isinstance(property_schema, dict):
            property_schema.pop("default", None)


class RateLimitSnapshot(APIModel):
    limit: int
    remaining: int
    observed_at: datetime


class CredentialResponse(APIModel):
    id: UUID
    name: str
    base_url: str = Field(json_schema_extra=HTTPS_ORIGIN_SCHEMA)
    token_masked: str
    subject_type: Literal["user", "group", "unknown"]
    subject_id: str | None
    login: str | None
    status: CredentialStatus
    enabled: bool
    last_verified_at: datetime | None
    rate_limit: RateLimitSnapshot | None
    next_retry_at: datetime | None
    active_operation_id: UUID | None
    repository_count: int
    created_at: datetime
    updated_at: datetime


class CredentialCreate(APIModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: StrictStr = Field(min_length=1, max_length=100)
    base_url: StrictStr = Field(
        min_length=1,
        max_length=2048,
        json_schema_extra=HTTPS_ORIGIN_SCHEMA,
    )
    token: StrictStr = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_name(self) -> CredentialCreate:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name cannot be blank")
        return self


class CredentialPatch(APIModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        json_schema_extra=_credential_patch_openapi,
    )

    name: StrictStr | SkipJsonSchema[None] = Field(default=None, min_length=1, max_length=100)
    base_url: StrictStr | SkipJsonSchema[None] = Field(
        default=None,
        min_length=1,
        max_length=2048,
        json_schema_extra=HTTPS_ORIGIN_SCHEMA,
    )
    token: StrictStr | SkipJsonSchema[None] = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def require_non_null_field(self) -> CredentialPatch:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        if "name" in self.model_fields_set:
            self.name = self.name.strip() if self.name is not None else None
            if not self.name:
                raise ValueError("name cannot be blank")
        return self


class OperationResponse(APIModel):
    id: UUID
    type: Literal["credential_verify", "repository_discovery"]
    status: OperationStatus
    credential_id: UUID
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    next_retry_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CredentialCreated(APIModel):
    credential: CredentialResponse
    operation: OperationResponse
