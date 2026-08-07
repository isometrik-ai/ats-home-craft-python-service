"""Shared helpers for API integration tests."""

from __future__ import annotations

from apps.user_service.app.utils.common_utils import UserContext


def admin_context(*, org_id: str = "org-123") -> UserContext:
    """Build a reusable admin user context for permission checks."""
    return UserContext(
        user_id="test-user-id",
        email="test@example.com",
        organization_id=org_id,
        user_type="admin",
    )


def patch_check_permissions(monkeypatch, module_path: str, org_id: str = "org-123") -> None:
    """Patch check_permissions on an API module to bypass RBAC in tests."""

    async def fake_check_permissions(
        current_user,
        db_connection,
        permission_codes,
        organization_id=None,
        request=None,
    ):
        del current_user, db_connection, permission_codes, organization_id, request
        return admin_context(org_id=org_id)

    monkeypatch.setattr(f"{module_path}.check_permissions", fake_check_permissions)


def patch_check_any_permissions(monkeypatch, module_path: str, org_id: str = "org-123") -> None:
    """Patch check_any_permissions on an API module to bypass RBAC in tests."""

    async def fake_check_any_permissions(
        current_user,
        db_connection,
        permission_codes,
        organization_id=None,
        request=None,
    ):
        del current_user, db_connection, permission_codes, organization_id, request
        return admin_context(org_id=org_id)

    monkeypatch.setattr(
        f"{module_path}.check_any_permissions",
        fake_check_any_permissions,
        raising=False,
    )


def patch_ensure_staff_project_access(
    monkeypatch, module_path: str, org_id: str = "org-123"
) -> None:
    """Patch ensure_staff_project_access on an API module to bypass RBAC in tests."""

    async def fake_ensure_staff_project_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(
        f"{module_path}.ensure_staff_project_access",
        fake_ensure_staff_project_access,
    )


def patch_ensure_staff_project_access_optional(
    monkeypatch, module_path: str, org_id: str = "org-123"
) -> None:
    """Patch ensure_staff_project_access_optional on an API module."""

    async def fake_ensure_staff_project_access_optional(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(
        f"{module_path}.ensure_staff_project_access_optional",
        fake_ensure_staff_project_access_optional,
    )


def patch_staff_project_access_wrapper(
    monkeypatch, module_path: str, org_id: str = "org-123"
) -> None:
    """Patch _staff_project_access wrapper used by the projects API."""

    async def fake_staff_project_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(f"{module_path}._staff_project_access", fake_staff_project_access)


def patch_staff_move_event_access(monkeypatch, module_path: str, org_id: str = "org-123") -> None:
    """Patch _staff_move_event_access wrapper used by the move events API."""

    async def fake_staff_move_event_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(f"{module_path}._staff_move_event_access", fake_staff_move_event_access)
