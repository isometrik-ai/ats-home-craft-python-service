"""
This module provides API endpoints for admin access management.

It combines routers from sub-modules for:
- Roles management
- Permissions management
- Role-Permission mappings
- User-Role mappings
"""

from fastapi import APIRouter

from .roles import router as roles_router
from .permissions import router as permissions_router
from .role_permissions import router as role_permissions_router
from .users.users import router as users_router

# Create main router for admin access management
router = APIRouter(prefix="/api/v1/users")

# Include all sub-routers
router.include_router(roles_router)
router.include_router(permissions_router)
router.include_router(role_permissions_router)
router.include_router(users_router)
