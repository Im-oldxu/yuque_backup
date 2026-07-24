from fastapi import APIRouter, status

from app.api.dependencies import CsrfAdmin, CurrentAdmin, DbSession
from app.api.openapi import CSRF_OPENAPI_EXTRA, documented_responses
from app.modules.settings.schemas import (
    RetentionSettingResponse,
    RetentionUpdateRequest,
    ScheduleSettingResponse,
    ScheduleUpdateRequest,
    StorageLimitUpdateRequest,
    StorageSettingResponse,
)
from app.modules.settings.service import (
    get_retention,
    get_schedule,
    get_storage,
    update_retention,
    update_schedule,
    update_storage_limit,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get(
    "/schedule",
    response_model=ScheduleSettingResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 503),
)
def read_schedule(db: DbSession, _admin: CurrentAdmin) -> ScheduleSettingResponse:
    return get_schedule(db)


@router.put(
    "/schedule",
    response_model=ScheduleSettingResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 403, 422, 503),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def write_schedule(
    payload: ScheduleUpdateRequest,
    db: DbSession,
    _admin: CsrfAdmin,
) -> ScheduleSettingResponse:
    return update_schedule(db, payload.cron, payload.timezone)


@router.get(
    "/retention",
    response_model=RetentionSettingResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 503),
)
def read_retention(db: DbSession, _admin: CurrentAdmin) -> RetentionSettingResponse:
    return get_retention(db)


@router.put(
    "/retention",
    response_model=RetentionSettingResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 403, 422, 503),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def write_retention(
    payload: RetentionUpdateRequest,
    db: DbSession,
    _admin: CsrfAdmin,
) -> RetentionSettingResponse:
    return update_retention(db, payload.retention_days)


@router.get(
    "/storage",
    response_model=StorageSettingResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 503),
)
def read_storage(db: DbSession, _admin: CurrentAdmin) -> StorageSettingResponse:
    return get_storage(db)


@router.put(
    "/storage-limit",
    response_model=StorageSettingResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 403, 422, 503),
    openapi_extra=CSRF_OPENAPI_EXTRA,
)
def write_storage_limit(
    payload: StorageLimitUpdateRequest,
    db: DbSession,
    _admin: CsrfAdmin,
) -> StorageSettingResponse:
    return update_storage_limit(db, payload.max_asset_size_bytes)
