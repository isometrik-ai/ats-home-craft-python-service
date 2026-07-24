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
