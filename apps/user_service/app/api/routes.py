"""API routes configuration module.

This module sets up the main API router and includes all sub-routers
for different API endpoints.
"""

from fastapi import APIRouter

# Import admin management routers
from apps.user_service.app.api.auth import router as auth_router
from apps.user_service.app.api.admin_management.organisation import (
    router as organisation_router,
)
from apps.user_service.app.api.admin_management.users.users import (
    router as users_router,
)
from apps.user_service.app.api.admin_management.users.user_profile import (
    router as user_profile_router,
)
from apps.user_service.app.api.admin_management.users.update_user import (
    router as update_user_router,
)

from apps.user_service.app.api.admin_management.roles import router as roles_router
from apps.user_service.app.api.admin_management.sessions.sessions import (
    router as sessions_router,
)
from apps.user_service.app.api.admin_management.permissions import (
    router as permissions_router,
)
from apps.user_service.app.api.audit_logs.audit_logs import router as audit_logs_router
from apps.user_service.app.api.invites import router as invites_router
from apps.user_service.app.api.presigned_url import router as presigned_url_router


# Create main API router
router = APIRouter(prefix="/v1/admin")

# Include all admin management routers
router.include_router(auth_router)
router.include_router(organisation_router)
router.include_router(users_router)
router.include_router(update_user_router)
router.include_router(user_profile_router)
router.include_router(roles_router)
router.include_router(sessions_router)
router.include_router(permissions_router)
router.include_router(audit_logs_router)
router.include_router(invites_router)
router.include_router(presigned_url_router)

# Health check endpoint for the API router
@router.get("/status")
async def api_status():
    """
    API status endpoint to verify all routes are working

    Returns:
        dict: Status message indicating API routes are active
    """
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
