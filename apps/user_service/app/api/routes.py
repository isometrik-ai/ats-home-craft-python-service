"""API routes configuration module."""

from fastapi import APIRouter

from apps.user_service.app.api.admin_management.organisation import (
    router as organisation_router,
)
from apps.user_service.app.api.admin_management.permissions import (
    router as permissions_router,
)
from apps.user_service.app.api.admin_management.roles import router as roles_router
from apps.user_service.app.api.admin_management.sessions.sessions import (
    router as sessions_router,
)
from apps.user_service.app.api.admin_management.users.users import (
    router as users_router,
)
from apps.user_service.app.api.audit_logs.audit_logs import router as audit_logs_router
from apps.user_service.app.api.auth import router as auth_router
from apps.user_service.app.api.invites import router as invites_router
from apps.user_service.app.api.presigned_url import router as presigned_url_router
from apps.user_service.app.api.verification_codes import (
    router as verification_codes_router,
)

router = APIRouter(prefix="/v1/admin")

router.include_router(auth_router)
router.include_router(organisation_router)
router.include_router(users_router)
router.include_router(roles_router)
router.include_router(sessions_router)
router.include_router(permissions_router)
router.include_router(audit_logs_router)
router.include_router(invites_router)
router.include_router(presigned_url_router)
router.include_router(verification_codes_router)


@router.get("/status")
async def api_status():
    """API status endpoint to verify all routes are working"""
    return {
        "message": "API routes are active",
        "status": "success",
        "available_endpoints": [
            "/admin/organisation",
            "/admin/users",
            "/admin/roles",
            "/admin/sessions",
            "/admin/permissions",
            "/admin/role-permissions",
            "/admin/audit-logs",
            "/admin/invite",
            "/embedding",
            "/clients",
        ],
    }
