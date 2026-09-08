"""Unit tests for project permission alias helpers."""

from libs.shared_utils.common_query import (
    NOTICES_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    VISITOR_MANAGEMENT_VIEW,
)
from libs.shared_utils.project_permission_aliases import (
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
