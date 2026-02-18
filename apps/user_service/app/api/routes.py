"""API routes configuration module."""

from fastapi import APIRouter

from apps.user_service.app.api.audit_logs import router as audit_logs_router
from apps.user_service.app.api.auth import router as auth_router
from apps.user_service.app.api.clients import router as clients_router
from apps.user_service.app.api.custom_fields import router as custom_fields_router
from apps.user_service.app.api.invites import router as invites_router
from apps.user_service.app.api.organization import router as organization_router
from apps.user_service.app.api.permissions import router as permissions_router
from apps.user_service.app.api.presigned_url import router as presigned_url_router
from apps.user_service.app.api.projects import router as projects_router
from apps.user_service.app.api.roles import router as roles_router
from apps.user_service.app.api.sessions import router as sessions_router
from apps.user_service.app.api.teams import router as teams_router
from apps.user_service.app.api.users import router as users_router
from apps.user_service.app.api.verification_codes import (
    router as verification_codes_router,
)

router = APIRouter(prefix="/v1")

router.include_router(auth_router)
router.include_router(organization_router)
router.include_router(users_router)
router.include_router(roles_router)
router.include_router(sessions_router)
router.include_router(permissions_router)
router.include_router(audit_logs_router)
router.include_router(invites_router)
router.include_router(presigned_url_router)
router.include_router(verification_codes_router)
router.include_router(teams_router)
router.include_router(clients_router)
router.include_router(projects_router)
router.include_router(custom_fields_router)


@router.get("/status")
async def api_status():
    """API status endpoint to verify all routes are working"""
    return {
        "message": "API routes are active",
        "status": "success",
        "available_endpoints": [
            "/organization",
            "/users",
            "/roles",
            "/sessions",
            "/permissions",
            "/role-permissions",
            "/audit-logs",
            "/invite",
            "/embedding",
            "/clients",
        ],
    }
