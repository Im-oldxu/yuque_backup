from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool

from app.api.schemas import APIModel, CredentialStatus
from app.modules.credentials.schemas import HTTPS_ORIGIN_SCHEMA


class RepositoryCredentialSummary(APIModel):
    id: UUID
    name: str
    status: CredentialStatus
    enabled: bool


class RepositoryResponse(APIModel):
    id: UUID
    yuque_book_id: str
    base_url: str = Field(json_schema_extra=HTTPS_ORIGIN_SCHEMA)
    name: str
    slug: str | None
    namespace: str | None
    selected: bool
    connection_status: Literal["connected", "disabled", "action_required"]
    primary_credential_id: UUID | None
    credential_count: int
    document_count: int
    last_success_at: datetime | None
    content_updated_at: datetime | None


class RepositoryDetailResponse(RepositoryResponse):
    credentials: list[RepositoryCredentialSummary]


class RepositorySelection(APIModel):
    selected: StrictBool


class PrimaryCredentialRequest(APIModel):
    credential_id: UUID


class TocNode(APIModel):
    id: UUID
    type: str
    title: str
    document_id: UUID | None
    path: str
    children: list[TocNode]


class TocTree(APIModel):
    repository_id: UUID
    updated_at: datetime
    items: list[TocNode]
