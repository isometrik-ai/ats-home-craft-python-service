"""Unit tests for project permission alias helpers."""

from libs.shared_utils.common_query import (
    CONTACTS_MANAGEMENT_EDIT,
    DAILY_HELP_MANAGEMENT_DELETE,
    DEFAULT_PROJECT_PERMISSIONS,
    MOVE_EVENTS_MANAGEMENT_VIEW,
    NOTICES_MANAGEMENT_EDIT,
    PROJECT_SETUP_EDIT,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    RESIDENT_MANAGEMENT_VIEW,
    VEHICLE_MANAGEMENT_DELETE,
    VISITOR_MANAGEMENT_VIEW,
    WORK_ORDER_MANAGEMENT_APPROVE,
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_MANAGE,
    WORK_ORDER_MANAGEMENT_PAY,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.project_permission_aliases import (
    expand_org_ceiling_permission_codes,
    org_ceiling_permission_codes,
    project_code_allowed_by_org_ceiling,
    project_permission_satisfiers,
    project_role_grants_any,
)


def test_project_permission_satisfiers_view_is_org_only():
    assert project_permission_satisfiers(PROJECTS_MANAGEMENT_VIEW) == frozenset()
    assert project_permission_satisfiers(PROJECTS_MANAGEMENT_VIEW_ASSIGNED) == frozenset()


def test_project_role_grants_any_access_only_needs_membership_not_project_code():
    assert project_role_grants_any(
        role_permission_codes=set(),
        required_permission_codes=[PROJECTS_MANAGEMENT_VIEW_ASSIGNED],
    )


def test_project_role_grants_any_matches_granular_edit():
    role_codes = {"notices_management.edit"}
    assert project_role_grants_any(
        role_permission_codes=role_codes,
        required_permission_codes=[NOTICES_MANAGEMENT_EDIT],
    )


def test_project_role_grants_any_rejects_missing_permission():
    role_codes = {"notices_management.view"}
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
    role_codes = {RESIDENT_MANAGEMENT_VIEW}
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


def test_daily_help_delete_ceiling_requires_projects_management_edit():
    ceiling = org_ceiling_permission_codes(DAILY_HELP_MANAGEMENT_DELETE)
    assert DAILY_HELP_MANAGEMENT_DELETE in ceiling
    assert PROJECTS_MANAGEMENT_EDIT in ceiling


def test_daily_help_delete_satisfied_only_by_delete_permission():
    assert project_role_grants_any(
        role_permission_codes={DAILY_HELP_MANAGEMENT_DELETE},
        required_permission_codes=[DAILY_HELP_MANAGEMENT_DELETE],
    )
    assert not project_role_grants_any(
        role_permission_codes={"daily_help_management.update"},
        required_permission_codes=[DAILY_HELP_MANAGEMENT_DELETE],
    )


def test_vehicle_and_daily_help_delete_share_edit_ceiling_pattern():
    vehicle_ceiling = org_ceiling_permission_codes(VEHICLE_MANAGEMENT_DELETE)
    daily_help_ceiling = org_ceiling_permission_codes(DAILY_HELP_MANAGEMENT_DELETE)
    assert PROJECTS_MANAGEMENT_EDIT in vehicle_ceiling
    assert PROJECTS_MANAGEMENT_EDIT in daily_help_ceiling


def test_project_code_allowed_by_org_ceiling_expands_view_assigned():
    """Staff with only view_assigned must retain project permissions in my-permissions."""
    org_codes = {PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    assert project_code_allowed_by_org_ceiling(org_codes, VISITOR_MANAGEMENT_VIEW)
    assert project_code_allowed_by_org_ceiling(org_codes, MOVE_EVENTS_MANAGEMENT_VIEW)


def test_work_order_granular_org_codes_satisfied_by_project_manage():
    assert project_permission_satisfiers(WORK_ORDER_MANAGEMENT_VIEW) == frozenset(
        {WORK_ORDER_MANAGEMENT_VIEW, WORK_ORDER_MANAGEMENT_MANAGE}
    )
    assert project_permission_satisfiers(WORK_ORDER_MANAGEMENT_EDIT) == frozenset(
        {WORK_ORDER_MANAGEMENT_EDIT, WORK_ORDER_MANAGEMENT_MANAGE}
    )


def test_work_order_legacy_project_role_codes_remain_valid():
    assert project_role_grants_any(
        role_permission_codes={WORK_ORDER_MANAGEMENT_VIEW},
        required_permission_codes=[WORK_ORDER_MANAGEMENT_VIEW],
    )
    assert project_role_grants_any(
        role_permission_codes={WORK_ORDER_MANAGEMENT_EDIT},
        required_permission_codes=[WORK_ORDER_MANAGEMENT_EDIT],
    )


def test_work_order_manage_grants_any_granular_org_requirement():
    role_codes = {WORK_ORDER_MANAGEMENT_MANAGE}
    for required in (
        WORK_ORDER_MANAGEMENT_VIEW,
        WORK_ORDER_MANAGEMENT_EDIT,
        WORK_ORDER_MANAGEMENT_APPROVE,
        WORK_ORDER_MANAGEMENT_PAY,
    ):
        assert project_role_grants_any(
            role_permission_codes=role_codes,
            required_permission_codes=[required],
        )


def test_work_order_manage_project_ceiling_uses_org_granular_codes():
    ceiling = org_ceiling_permission_codes(WORK_ORDER_MANAGEMENT_MANAGE)
    assert WORK_ORDER_MANAGEMENT_VIEW in ceiling
    assert WORK_ORDER_MANAGEMENT_EDIT in ceiling
    assert WORK_ORDER_MANAGEMENT_APPROVE in ceiling
    assert WORK_ORDER_MANAGEMENT_PAY in ceiling
    assert PROJECTS_MANAGEMENT_VIEW in ceiling


def test_default_project_permissions_work_order_is_single_manage():
    work_order_entries = [
        entry for entry in DEFAULT_PROJECT_PERMISSIONS if entry[3] == "work_order_management"
    ]
    assert len(work_order_entries) == 1
    assert work_order_entries[0][0] == WORK_ORDER_MANAGEMENT_MANAGE


def test_default_project_permissions_daily_help_includes_delete():
    """Role editor daily_help group must expose all five granular permissions."""
    daily_help_entries = [
        entry for entry in DEFAULT_PROJECT_PERMISSIONS if entry[3] == "daily_help"
    ]
    codes = {entry[0] for entry in daily_help_entries}
    assert len(daily_help_entries) == 5
    assert DAILY_HELP_MANAGEMENT_DELETE in codes
    delete_entry = next(
        entry for entry in daily_help_entries if entry[0] == DAILY_HELP_MANAGEMENT_DELETE
    )
    assert delete_entry[1] == "Delete Daily Help"
    assert delete_entry[2] == "Soft-delete daily help profiles within assigned projects"


def test_default_project_permissions_contacts_includes_edit():
    """Role editor contacts group must expose view, create, edit, and delete."""
    contacts_entries = [entry for entry in DEFAULT_PROJECT_PERMISSIONS if entry[3] == "contacts"]
    codes = {entry[0] for entry in contacts_entries}
    assert len(contacts_entries) == 4
    assert CONTACTS_MANAGEMENT_EDIT in codes
    edit_entry = next(entry for entry in contacts_entries if entry[0] == CONTACTS_MANAGEMENT_EDIT)
    assert edit_entry[1] == "Edit Project Contacts"
    assert edit_entry[2] == "Modify contacts within assigned projects"


def test_contacts_edit_ceiling_requires_projects_management_edit():
    ceiling = org_ceiling_permission_codes(CONTACTS_MANAGEMENT_EDIT)
    assert CONTACTS_MANAGEMENT_EDIT in ceiling
    assert PROJECTS_MANAGEMENT_EDIT in ceiling
