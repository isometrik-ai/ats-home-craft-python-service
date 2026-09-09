"""Unit tests for project permission alias helpers."""

from libs.shared_utils.common_query import (
    MOVE_EVENTS_MANAGEMENT_VIEW,
    NOTICES_MANAGEMENT_EDIT,
    PROJECT_SETUP_EDIT,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    RESIDENT_MANAGEMENT_VIEW,
    VISITOR_MANAGEMENT_VIEW,
)
from libs.shared_utils.project_permission_aliases import (
    expand_org_ceiling_permission_codes,
    org_ceiling_permission_codes,
    project_permission_satisfiers,
    project_role_grants_any,
)


def test_project_permission_satisfiers_for_legacy_view():
    assert PROJECTS_MANAGEMENT_VIEW_ASSIGNED in project_permission_satisfiers(
        "projects_management.view"
    )


def test_project_role_grants_any_matches_granular_edit():
    role_codes = {"notices_management.edit", PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    assert project_role_grants_any(
        role_permission_codes=role_codes,
        required_permission_codes=[NOTICES_MANAGEMENT_EDIT],
    )


def test_project_role_grants_any_rejects_missing_permission():
    role_codes = {PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    assert not project_role_grants_any(
        role_permission_codes=role_codes,
        required_permission_codes=[VISITOR_MANAGEMENT_VIEW],
    )


def test_org_ceiling_includes_legacy_projects_management_edit():
    ceiling = org_ceiling_permission_codes(PROJECT_SETUP_EDIT)
    assert PROJECT_SETUP_EDIT in ceiling
    assert PROJECTS_MANAGEMENT_EDIT in ceiling
    assert PROJECTS_MANAGEMENT_VIEW in ceiling


def test_move_events_not_satisfied_by_resident_management_only():
    role_codes = {RESIDENT_MANAGEMENT_VIEW, PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    assert not project_role_grants_any(
        role_permission_codes=role_codes,
        required_permission_codes=[MOVE_EVENTS_MANAGEMENT_VIEW],
    )


def test_expand_org_ceiling_permission_codes_deduplicates():
    expanded = expand_org_ceiling_permission_codes(
        [PROJECT_SETUP_EDIT, PROJECTS_MANAGEMENT_VIEW_ASSIGNED]
    )
    assert PROJECT_SETUP_EDIT in expanded
    assert PROJECTS_MANAGEMENT_EDIT in expanded
    assert expanded.count(PROJECTS_MANAGEMENT_VIEW) == 1
