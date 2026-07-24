from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.credentials.schemas import HTTPS_ORIGIN_SCHEMA


class TombstoneRepositoryResponse(BaseModel):
    id: UUID
    name: str


class TombstoneResponse(BaseModel):
    id: UUID
    base_url: str = Field(json_schema_extra=HTTPS_ORIGIN_SCHEMA)
    yuque_book_id: str
    yuque_doc_id: str
    title: str
    original_path: str
    repository: TombstoneRepositoryResponse
    deleted_at: datetime
    purged_at: datetime
    source_job_id: UUID
    cleanup_job_id: UUID


class TombstonePageResponse(BaseModel):
    items: list[TombstoneResponse]
    page: int
    page_size: int
    total: int
