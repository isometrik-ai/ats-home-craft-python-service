"""Project permission aliases and scopable catalog for project-level RBAC."""

from __future__ import annotations

from libs.shared_utils.common_query import (
    COMMUNITY_EVENTS_MANAGEMENT_EDIT,
    COMMUNITY_EVENTS_MANAGEMENT_VIEW,
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
    PROJECT_MEMBERS_MANAGE,
    PROJECT_MEMBERS_MANAGE_ASSIGNED,
    PROJECT_SETUP_DELETE,
    PROJECT_SETUP_EDIT,
    PROJECTS_MANAGEMENT_DELETE,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    RESIDENT_MANAGEMENT_EDIT,
    RESIDENT_MANAGEMENT_VIEW,
    TENANT_REQUESTS_MANAGEMENT_EDIT,
    TENANT_REQUESTS_MANAGEMENT_VIEW,
    VISITOR_MANAGEMENT_VERIFY,
    VISITOR_MANAGEMENT_VIEW,
    WORK_ORDER_MANAGEMENT_APPROVE,
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_PAY,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.project_role_defaults import DEFAULT_PROJECT_ROLE_PERMISSIONS

# All permission codes that may appear on a project role template.
PROJECT_SCOPABLE_PERMISSION_CODES: frozenset[str] = frozenset(
    {code for codes in DEFAULT_PROJECT_ROLE_PERMISSIONS.values() for code in codes}
)

# Maps an API/org permission requirement to project-role codes that satisfy it.
PROJECT_PERMISSION_SATISFIERS: dict[str, frozenset[str]] = {
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED: frozenset({PROJECTS_MANAGEMENT_VIEW_ASSIGNED}),
    PROJECT_SETUP_EDIT: frozenset({PROJECT_SETUP_EDIT}),
    PROJECT_SETUP_DELETE: frozenset({PROJECT_SETUP_DELETE}),
    PROJECT_MEMBERS_MANAGE_ASSIGNED: frozenset({PROJECT_MEMBERS_MANAGE_ASSIGNED}),
    VISITOR_MANAGEMENT_VIEW: frozenset({VISITOR_MANAGEMENT_VIEW}),
    VISITOR_MANAGEMENT_VERIFY: frozenset({VISITOR_MANAGEMENT_VERIFY}),
    NOTICES_MANAGEMENT_VIEW: frozenset({NOTICES_MANAGEMENT_VIEW}),
    NOTICES_MANAGEMENT_EDIT: frozenset({NOTICES_MANAGEMENT_EDIT}),
    COMMUNITY_EVENTS_MANAGEMENT_VIEW: frozenset({COMMUNITY_EVENTS_MANAGEMENT_VIEW}),
    COMMUNITY_EVENTS_MANAGEMENT_EDIT: frozenset({COMMUNITY_EVENTS_MANAGEMENT_EDIT}),
    DAILY_HELP_MANAGEMENT_VIEW: frozenset({DAILY_HELP_MANAGEMENT_VIEW}),
    DAILY_HELP_MANAGEMENT_CREATE: frozenset({DAILY_HELP_MANAGEMENT_CREATE}),
    DAILY_HELP_MANAGEMENT_UPDATE: frozenset({DAILY_HELP_MANAGEMENT_UPDATE}),
    DAILY_HELP_MANAGEMENT_REVIEW: frozenset({DAILY_HELP_MANAGEMENT_REVIEW}),
    TENANT_REQUESTS_MANAGEMENT_VIEW: frozenset({TENANT_REQUESTS_MANAGEMENT_VIEW}),
    TENANT_REQUESTS_MANAGEMENT_EDIT: frozenset({TENANT_REQUESTS_MANAGEMENT_EDIT}),
    MOVE_EVENTS_MANAGEMENT_VIEW: frozenset({MOVE_EVENTS_MANAGEMENT_VIEW, RESIDENT_MANAGEMENT_VIEW}),
    MOVE_EVENTS_MANAGEMENT_EDIT: frozenset({MOVE_EVENTS_MANAGEMENT_EDIT, RESIDENT_MANAGEMENT_EDIT}),
    PARKING_MANAGEMENT_VIEW: frozenset({PARKING_MANAGEMENT_VIEW}),
    PARKING_MANAGEMENT_EDIT: frozenset({PARKING_MANAGEMENT_EDIT}),
    RESIDENT_MANAGEMENT_VIEW: frozenset({RESIDENT_MANAGEMENT_VIEW}),
    RESIDENT_MANAGEMENT_EDIT: frozenset({RESIDENT_MANAGEMENT_EDIT}),
    FINANCE_MANAGEMENT_VIEW: frozenset({FINANCE_MANAGEMENT_VIEW}),
    FINANCE_MANAGEMENT_EDIT: frozenset({FINANCE_MANAGEMENT_EDIT}),
    FINANCE_MANAGEMENT_ADMIN: frozenset({FINANCE_MANAGEMENT_ADMIN}),
    WORK_ORDER_MANAGEMENT_VIEW: frozenset({WORK_ORDER_MANAGEMENT_VIEW}),
    WORK_ORDER_MANAGEMENT_EDIT: frozenset({WORK_ORDER_MANAGEMENT_EDIT}),
    WORK_ORDER_MANAGEMENT_APPROVE: frozenset({WORK_ORDER_MANAGEMENT_APPROVE}),
    WORK_ORDER_MANAGEMENT_PAY: frozenset({WORK_ORDER_MANAGEMENT_PAY}),
}


def project_permission_satisfiers(permission_code: str) -> frozenset[str]:
    """Return project-role permission codes that satisfy an org/API permission check."""
    explicit = PROJECT_PERMISSION_SATISFIERS.get(permission_code)
    if explicit is not None:
        return explicit
    if permission_code == "projects_management.view":
        return frozenset({PROJECTS_MANAGEMENT_VIEW_ASSIGNED})
    return frozenset({permission_code})


def project_role_grants_any(
    *,
    role_permission_codes: set[str],
    required_permission_codes: list[str],
) -> bool:
    """Return True when the project role satisfies at least one required permission."""
    for required in required_permission_codes:
        satisfiers = project_permission_satisfiers(required)
        if role_permission_codes.intersection(satisfiers):
            return True
    return False


def org_ceiling_permission_codes(permission_code: str) -> frozenset[str]:
    """Org-role codes that satisfy the ceiling for a project-scoped API action."""
    codes = {permission_code}
    if permission_code not in PROJECT_SCOPABLE_PERMISSION_CODES:
        return frozenset(codes)

    codes.add(PROJECTS_MANAGEMENT_VIEW)

    if permission_code in {
        PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        VISITOR_MANAGEMENT_VIEW,
        NOTICES_MANAGEMENT_VIEW,
        COMMUNITY_EVENTS_MANAGEMENT_VIEW,
        DAILY_HELP_MANAGEMENT_VIEW,
        TENANT_REQUESTS_MANAGEMENT_VIEW,
        MOVE_EVENTS_MANAGEMENT_VIEW,
        PARKING_MANAGEMENT_VIEW,
        RESIDENT_MANAGEMENT_VIEW,
        FINANCE_MANAGEMENT_VIEW,
        WORK_ORDER_MANAGEMENT_VIEW,
    }:
        codes.add(PROJECTS_MANAGEMENT_VIEW_ASSIGNED)

    if permission_code in {
        PROJECT_SETUP_EDIT,
        PROJECT_SETUP_DELETE,
        PROJECT_MEMBERS_MANAGE_ASSIGNED,
        VISITOR_MANAGEMENT_VERIFY,
        NOTICES_MANAGEMENT_EDIT,
        COMMUNITY_EVENTS_MANAGEMENT_EDIT,
        DAILY_HELP_MANAGEMENT_CREATE,
        DAILY_HELP_MANAGEMENT_UPDATE,
        DAILY_HELP_MANAGEMENT_REVIEW,
        TENANT_REQUESTS_MANAGEMENT_EDIT,
        MOVE_EVENTS_MANAGEMENT_EDIT,
        PARKING_MANAGEMENT_EDIT,
        RESIDENT_MANAGEMENT_EDIT,
        FINANCE_MANAGEMENT_EDIT,
        FINANCE_MANAGEMENT_ADMIN,
        WORK_ORDER_MANAGEMENT_EDIT,
        WORK_ORDER_MANAGEMENT_APPROVE,
        WORK_ORDER_MANAGEMENT_PAY,
    }:
        codes.add(PROJECTS_MANAGEMENT_EDIT)

    if permission_code in {PROJECT_SETUP_DELETE}:
        codes.add(PROJECTS_MANAGEMENT_DELETE)

    if permission_code == PROJECT_MEMBERS_MANAGE_ASSIGNED:
        codes.add(PROJECT_MEMBERS_MANAGE)

    return frozenset(codes)


def expand_org_ceiling_permission_codes(permission_codes: list[str]) -> list[str]:
    """Expand project-scoped API codes to acceptable org-role ceiling codes."""
    expanded: list[str] = []
    seen: set[str] = set()
    for code in permission_codes:
        for candidate in org_ceiling_permission_codes(code):
            if candidate not in seen:
                seen.add(candidate)
                expanded.append(candidate)
    return expanded
