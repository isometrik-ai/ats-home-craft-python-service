"""Default project role templates seeded per project on creation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from libs.shared_utils.common_query import (
    BUSINESS_DASHBOARD_VIEW,
    COMMUNITY_EVENTS_MANAGEMENT_EDIT,
    COMMUNITY_EVENTS_MANAGEMENT_VIEW,
    CONTACTS_MANAGEMENT_CREATE,
    CONTACTS_MANAGEMENT_DELETE,
    CONTACTS_MANAGEMENT_VIEW,
    DAILY_HELP_MANAGEMENT_CREATE,
    DAILY_HELP_MANAGEMENT_REVIEW,
    DAILY_HELP_MANAGEMENT_UPDATE,
    DAILY_HELP_MANAGEMENT_VIEW,
    FINANCE_MANAGEMENT_ADMIN,
    FINANCE_MANAGEMENT_EDIT,
    FINANCE_MANAGEMENT_VIEW,
    MOVE_EVENTS_MANAGEMENT_EDIT,
    MOVE_EVENTS_MANAGEMENT_VIEW,
    NOTICES_MANAGEMENT_EDIT,
    NOTICES_MANAGEMENT_VIEW,
    PARKING_MANAGEMENT_EDIT,
    PARKING_MANAGEMENT_VIEW,
    PROJECT_MEMBERS_MANAGE_ASSIGNED,
    PROJECT_SETUP_DELETE,
    PROJECT_SETUP_EDIT,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    RESIDENT_MANAGEMENT_EDIT,
    RESIDENT_MANAGEMENT_VIEW,
    TENANT_REQUESTS_MANAGEMENT_EDIT,
    TENANT_REQUESTS_MANAGEMENT_VIEW,
    VEHICLE_MANAGEMENT_DELETE,
    VEHICLE_MANAGEMENT_EDIT,
    VEHICLE_MANAGEMENT_VIEW,
    VISITOR_MANAGEMENT_VERIFY,
    VISITOR_MANAGEMENT_VIEW,
    WORK_ORDER_MANAGEMENT_APPROVE,
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_PAY,
    WORK_ORDER_MANAGEMENT_VIEW,
)


@dataclass(frozen=True, slots=True)
class DefaultProjectRoleDefinition:
    """Metadata for a system project role slug."""

    slug: str
    name: str
    description: str


DEFAULT_PROJECT_ROLE_DEFINITIONS: tuple[DefaultProjectRoleDefinition, ...] = (
    DefaultProjectRoleDefinition(
        slug="community_admin",
        name="Community Admin",
        description="Project manager / RWA admin with full project operations.",
    ),
    DefaultProjectRoleDefinition(
        slug="security",
        name="Security",
        description="Gate and visitor operations for the project.",
    ),
    DefaultProjectRoleDefinition(
        slug="accountant",
        name="Accountant",
        description="Fees, billing, and finance for the project.",
    ),
    DefaultProjectRoleDefinition(
        slug="facility_manager",
        name="Facility Manager",
        description="Facilities, parking, maintenance, and work orders.",
    ),
    DefaultProjectRoleDefinition(
        slug="viewer",
        name="Viewer",
        description="Read-only access to assigned project modules.",
    ),
)

COMMUNITY_ADMIN_SLUG = "community_admin"

SYSTEM_PROJECT_ROLE_SLUGS: frozenset[str] = frozenset(
    role_def.slug for role_def in DEFAULT_PROJECT_ROLE_DEFINITIONS
)

_PROJECT_ROLE_SLUG_MAX_LEN = 64
_PROJECT_ROLE_SLUG_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"


def slugify_project_role_name(name: str) -> str:
    """Build a snake_case slug from a display name."""
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower().strip())
    compact = normalized.strip("_")
    if not compact:
        return "custom_role"
    if not compact[0].isalpha():
        compact = f"role_{compact}"
    return compact[:_PROJECT_ROLE_SLUG_MAX_LEN]


def is_valid_project_role_slug(slug: str) -> bool:
    """Return True when slug matches the allowed project role slug format."""
    return bool(re.fullmatch(_PROJECT_ROLE_SLUG_PATTERN, slug))


def is_reserved_system_project_role_slug(slug: str) -> bool:
    """Return True when slug is reserved for a seeded system role."""
    return slug in SYSTEM_PROJECT_ROLE_SLUGS


# Permission codes granted to each default project role template (within a project).
DEFAULT_PROJECT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "community_admin": frozenset(
        {
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
            PROJECT_SETUP_EDIT,
            PROJECT_SETUP_DELETE,
            PROJECT_MEMBERS_MANAGE_ASSIGNED,
            VISITOR_MANAGEMENT_VIEW,
            VISITOR_MANAGEMENT_VERIFY,
            NOTICES_MANAGEMENT_VIEW,
            NOTICES_MANAGEMENT_EDIT,
            COMMUNITY_EVENTS_MANAGEMENT_VIEW,
            COMMUNITY_EVENTS_MANAGEMENT_EDIT,
            DAILY_HELP_MANAGEMENT_VIEW,
            DAILY_HELP_MANAGEMENT_CREATE,
            DAILY_HELP_MANAGEMENT_UPDATE,
            DAILY_HELP_MANAGEMENT_REVIEW,
            TENANT_REQUESTS_MANAGEMENT_VIEW,
            TENANT_REQUESTS_MANAGEMENT_EDIT,
            MOVE_EVENTS_MANAGEMENT_VIEW,
            MOVE_EVENTS_MANAGEMENT_EDIT,
            PARKING_MANAGEMENT_VIEW,
            PARKING_MANAGEMENT_EDIT,
            VEHICLE_MANAGEMENT_VIEW,
            VEHICLE_MANAGEMENT_EDIT,
            VEHICLE_MANAGEMENT_DELETE,
            RESIDENT_MANAGEMENT_VIEW,
            RESIDENT_MANAGEMENT_EDIT,
            FINANCE_MANAGEMENT_VIEW,
            FINANCE_MANAGEMENT_EDIT,
            FINANCE_MANAGEMENT_ADMIN,
            WORK_ORDER_MANAGEMENT_VIEW,
            WORK_ORDER_MANAGEMENT_EDIT,
            WORK_ORDER_MANAGEMENT_APPROVE,
            WORK_ORDER_MANAGEMENT_PAY,
            BUSINESS_DASHBOARD_VIEW,
            CONTACTS_MANAGEMENT_VIEW,
            CONTACTS_MANAGEMENT_CREATE,
            CONTACTS_MANAGEMENT_DELETE,
        }
    ),
    "security": frozenset(
        {
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
            BUSINESS_DASHBOARD_VIEW,
            CONTACTS_MANAGEMENT_VIEW,
            VISITOR_MANAGEMENT_VIEW,
            VISITOR_MANAGEMENT_VERIFY,
            NOTICES_MANAGEMENT_VIEW,
            COMMUNITY_EVENTS_MANAGEMENT_VIEW,
            DAILY_HELP_MANAGEMENT_VIEW,
            PARKING_MANAGEMENT_VIEW,
            VEHICLE_MANAGEMENT_VIEW,
            RESIDENT_MANAGEMENT_VIEW,
        }
    ),
    "accountant": frozenset(
        {
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
            BUSINESS_DASHBOARD_VIEW,
            CONTACTS_MANAGEMENT_VIEW,
            TENANT_REQUESTS_MANAGEMENT_VIEW,
            MOVE_EVENTS_MANAGEMENT_VIEW,
            RESIDENT_MANAGEMENT_VIEW,
            FINANCE_MANAGEMENT_VIEW,
            FINANCE_MANAGEMENT_EDIT,
            FINANCE_MANAGEMENT_ADMIN,
            WORK_ORDER_MANAGEMENT_VIEW,
            WORK_ORDER_MANAGEMENT_APPROVE,
            WORK_ORDER_MANAGEMENT_PAY,
        }
    ),
    "facility_manager": frozenset(
        {
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
            BUSINESS_DASHBOARD_VIEW,
            CONTACTS_MANAGEMENT_VIEW,
            PROJECT_SETUP_EDIT,
            COMMUNITY_EVENTS_MANAGEMENT_VIEW,
            COMMUNITY_EVENTS_MANAGEMENT_EDIT,
            DAILY_HELP_MANAGEMENT_VIEW,
            DAILY_HELP_MANAGEMENT_CREATE,
            DAILY_HELP_MANAGEMENT_UPDATE,
            DAILY_HELP_MANAGEMENT_REVIEW,
            PARKING_MANAGEMENT_VIEW,
            PARKING_MANAGEMENT_EDIT,
            VEHICLE_MANAGEMENT_VIEW,
            VEHICLE_MANAGEMENT_EDIT,
            VEHICLE_MANAGEMENT_DELETE,
            RESIDENT_MANAGEMENT_VIEW,
            WORK_ORDER_MANAGEMENT_VIEW,
            WORK_ORDER_MANAGEMENT_EDIT,
        }
    ),
    "viewer": frozenset(
        {
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
            BUSINESS_DASHBOARD_VIEW,
            CONTACTS_MANAGEMENT_VIEW,
            VISITOR_MANAGEMENT_VIEW,
            NOTICES_MANAGEMENT_VIEW,
            COMMUNITY_EVENTS_MANAGEMENT_VIEW,
            DAILY_HELP_MANAGEMENT_VIEW,
            TENANT_REQUESTS_MANAGEMENT_VIEW,
            MOVE_EVENTS_MANAGEMENT_VIEW,
            PARKING_MANAGEMENT_VIEW,
            VEHICLE_MANAGEMENT_VIEW,
            RESIDENT_MANAGEMENT_VIEW,
            FINANCE_MANAGEMENT_VIEW,
            WORK_ORDER_MANAGEMENT_VIEW,
        }
    ),
}
