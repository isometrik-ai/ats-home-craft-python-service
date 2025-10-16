"""Constants for query used across the application."""

import os
import sys
from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("common_utils")

ROLE_TYPES = ["system", "custom"]
# libs/shared_utils/common_query.py

def log_exception():
    """Log exception details"""
    exc_type, _, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    logger.error("Error: %s, File: %s, Line: %s", exc_type, fname, exc_tb.tb_lineno)


# def _get_default_permissions() -> List[tuple]:

# """
# Get the default permissions for a new organisation.

# Returns:
#     List[tuple]: List of permission tuples (code, name, description, category)
# """
DEFAULT_PERMISSIONS = [
    (
        "business.dashboard.view",
        "View Dashboard",
        "Access to main dashboard",
        "business",
    ),
    (
        "business.customers.view",
        "View Customers",
        "View customer information",
        "business",
    ),
    (
        "business.customers.manage",
        "Manage Customers",
        "Full customer management",
        "business",
    ),
    (
        "business.projects.view",
        "View Projects",
        "View project information",
        "business",
    ),
    (
        "business.projects.manage",
        "Manage Projects",
        "Full project management",
        "business",
    ),
    (
        "settings.users.manage",
        "Manage Users",
        "Full user management",
        "settings",
    ),
    (
        "settings.roles.manage",
        "Manage Roles",
        "Create and manage roles",
        "settings",
    ),
    (
        "settings.integrations.manage",
        "Manage Integrations",
        "Configure integrations",
        "settings",
    ),
    (
        "settings.system.manage",
        "System Settings",
        "Access to system settings",
        "settings",
    ),
]



# Common SELECT fields for roles
ROLE_SELECT_FIELDS = """
    id,
    name,
    description,
    is_default,
    updated_at,
    created_at
"""

# Common SELECT fields for permissions
PERMISSION_SELECT_FIELDS = """
    p.id,
    p.name,
    p.code,
    p.category,
    p.description,
    p.created_at
"""

SETTINGS_SYSTEM_MANAGE = "settings.system.manage"
SETTINGS_ROLES_MANAGE = "settings.roles.manage"
SETTINGS_USERS_MANAGE = "settings.users.manage"
SETTINGS_USERS_VIEW = "settings.users.view"
SETTINGS_PERMISSIONS_MANAGE = "settings.permissions.manage"

USER_NOT_FOUND_MESSAGE = "User not found in organization"
