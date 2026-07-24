from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentAdmin, DbSession
from app.api.openapi import documented_responses
from app.modules.tombstones.schemas import TombstonePageResponse, TombstoneResponse
from app.modules.tombstones.service import get_tombstone, list_tombstones

router = APIRouter(prefix="/api/v1/deletion-tombstones", tags=["deletion-tombstones"])


@router.get(
    "",
    response_model=TombstonePageResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 422),
)
def read_tombstones(
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query()] = None,
    repository_id: Annotated[UUID | None, Query()] = None,
    deleted_from: Annotated[datetime | None, Query()] = None,
    deleted_to: Annotated[datetime | None, Query()] = None,
) -> TombstonePageResponse:
    return list_tombstones(
        db,
        page=page,
        page_size=page_size,
        q=q,
        repository_id=str(repository_id) if repository_id is not None else None,
        deleted_from=deleted_from,
        deleted_to=deleted_to,
    )


@router.get(
    "/{tombstone_id}",
    response_model=TombstoneResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 404, 422),
)
def read_tombstone(
    tombstone_id: UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> TombstoneResponse:
    return get_tombstone(db, str(tombstone_id))
