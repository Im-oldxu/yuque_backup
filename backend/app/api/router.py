from fastapi import APIRouter

from app.api.health import router as health_router
from app.modules.auth.router import router as auth_router
from app.modules.backups.router import router as backups_router
from app.modules.credentials.router import router as credentials_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.documents.router import router as documents_router
from app.modules.repositories.router import router as repositories_router
from app.modules.settings.router import router as settings_router
from app.modules.tombstones.router import router as tombstones_router

router = APIRouter()
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(credentials_router)
router.include_router(repositories_router)
router.include_router(documents_router)
router.include_router(backups_router)
router.include_router(settings_router)
router.include_router(dashboard_router)
router.include_router(tombstones_router)
