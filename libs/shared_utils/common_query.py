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
        "talent.candidates.view",
        "View Candidates",
        "View candidate profiles",
        "talent",
    ),
    (
        "talent.candidates.manage",
        "Manage Candidates",
        "Full candidate management",
        "talent",
    ),
    (
        "talent.rst_templates.view",
        "View RST Templates",
        "View templates only",
        "talent",
    ),
    (
        "talent.rst_templates.manage",
        "Manage RST Templates",
        "Create and edit templates",
        "talent",
    ),
    (
        "talent.search.advanced",
        "Advanced Search",
        "Access to advanced search features",
        "talent",
    ),
    (
        "automation.triggers.manage",
        "Manage Triggers",
        "Create and manage automation triggers",
        "automation",
    ),
    (
        "automation.analytics.view",
        "View Analytics",
        "Access to analytics dashboard",
        "automation",
    ),
    (
        "automation.analytics.export",
        "Export Analytics",
        "Export analytics data",
        "automation",
    ),
    (
        "automation.ai_assistant.access",
        "AI Assistant Access",
        "Access to AI assistant features",
        "automation",
    ),
    ("settings.users.manage", "Manage Users", "Full user management", "settings"),
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
    r.id,
    r.name,
    r.description,
    r.is_default,
    r.updated_at,
    r.created_at
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

USER_NOT_FOUND_MESSAGE = "User not found in organization"