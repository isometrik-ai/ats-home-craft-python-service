"""API routes configuration module.

This module sets up the main API router and includes all sub-routers
for different API endpoints.
"""

# flake8: noqa
# type: ignore
# pants: no-infer-dep

import os
import sys

from fastapi import APIRouter

# Import admin management routers
from apps.user_service.app.api.auth import router as auth_router
from apps.user_service.app.api.admin_management.organisation import (
    router as organisation_router,
)
from apps.user_service.app.api.admin_management.users.users import (
    router as users_router,
)
from apps.user_service.app.api.admin_management.roles import router as roles_router
from apps.user_service.app.api.admin_management.sessions.sessions import (
    router as sessions_router,
)
from apps.user_service.app.api.admin_management.permissions import (
    router as permissions_router,
)

from apps.user_service.app.api.admin_management.router import router as admin_management_router
from apps.user_service.app.api.audit_logs.audit_logs import router as audit_logs_router


# Add apps/api_service to sys.path so 'app' and 'libs' can be imported
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, base_path)

# Also add the monorepo root for shared `libs`
monorepo_root = os.path.abspath(os.path.join(base_path, "../.."))
sys.path.insert(0, monorepo_root)


# Create main API router
router = APIRouter(prefix="/v1")

# Include all admin management routers
router.include_router(auth_router, prefix="/admin")
router.include_router(organisation_router, prefix="/admin")
router.include_router(users_router, prefix="/admin")
router.include_router(roles_router, prefix="/admin")
router.include_router(sessions_router, prefix="/admin")
router.include_router(permissions_router, prefix="/admin")
router.include_router(admin_management_router, prefix="/admin")
router.include_router(audit_logs_router, prefix="/admin")


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
            "/embedding",
            "/clients",
        ],
    }
