from fastapi import APIRouter, status

from app.api.dependencies import CurrentAdmin, DbSession
from app.api.openapi import documented_responses
from app.modules.dashboard.schemas import DashboardSummaryResponse
from app.modules.dashboard.service import get_dashboard_summary

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_responses(401, 503),
)
def read_dashboard_summary(db: DbSession, _admin: CurrentAdmin) -> DashboardSummaryResponse:
    return get_dashboard_summary(db)
