"""Unit tests for project role slug helpers."""

from libs.shared_utils.project_role_defaults import (
    is_reserved_system_project_role_slug,
    is_valid_project_role_slug,
    slugify_project_role_name,
)


def test_slugify_project_role_name() -> None:
    assert slugify_project_role_name("Maintenance Lead") == "maintenance_lead"
    assert slugify_project_role_name("  ") == "custom_role"
    assert slugify_project_role_name("9th Floor") == "role_9th_floor"


def test_is_valid_project_role_slug() -> None:
    assert is_valid_project_role_slug("maintenance_lead")
    assert not is_valid_project_role_slug("Maintenance-Lead")
    assert not is_valid_project_role_slug("1bad")


def test_is_reserved_system_project_role_slug() -> None:
    assert is_reserved_system_project_role_slug("security")
    assert not is_reserved_system_project_role_slug("maintenance_lead")
