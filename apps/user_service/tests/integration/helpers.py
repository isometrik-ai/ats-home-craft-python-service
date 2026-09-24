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


def patch_ensure_companies_or_resident_project_access(
    monkeypatch,
    module_path: str,
    org_id: str = "org-123",
) -> None:
    """Patch companies project access helper and project scope checks in tests."""

    async def fake_ensure_company_in_project(self, **kwargs):
        del self, kwargs
        return None

    async def fake_validate_project_scoped_create(self, **kwargs):
        del self, kwargs
        return None

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.ensure_company_in_project",
        fake_ensure_company_in_project,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.validate_project_scoped_create",
        fake_validate_project_scoped_create,
    )

    async def fake_ensure_companies_or_resident_project_access(
        *,
        current_user,
        db_connection,
        project_id=None,
        edit=False,
        create=False,
        delete=False,
        request=None,
    ):
        del (
            current_user,
            db_connection,
            project_id,
            edit,
            create,
            delete,
            request,
        )
        return admin_context(org_id=org_id)

    monkeypatch.setattr(
        f"{module_path}.ensure_companies_or_resident_project_access",
        fake_ensure_companies_or_resident_project_access,
    )


def patch_companies_project_access_denied(monkeypatch, module_path: str) -> None:
    """Patch companies access helper to simulate forbidden project role."""

    from libs.shared_utils.http_exceptions import ForbiddenException
    from libs.shared_utils.status_codes import CustomStatusCode

    async def fake_denied(**kwargs):
        del kwargs
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

    monkeypatch.setattr(
        f"{module_path}.ensure_companies_or_resident_project_access",
        fake_denied,
    )


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


def patch_crm_or_resident_access(monkeypatch, module_path: str, org_id: str = "org-123") -> None:
    """Patch resident/CRM access helpers imported by an API module."""

    async def fake_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    for helper_name in (
        "ensure_crm_or_resident_project_access",
        "ensure_resident_access_for_unit",
        "ensure_resident_or_crm_contact_access",
    ):
        monkeypatch.setattr(
            f"{module_path}.{helper_name}",
            fake_access,
            raising=False,
        )


def patch_staff_project_access_wrapper(
    monkeypatch, module_path: str, org_id: str = "org-123"
) -> None:
    """Patch _staff_project_access wrapper used by the projects API."""

    async def fake_staff_project_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(f"{module_path}._staff_project_access", fake_staff_project_access)


def patch_project_staff_management_access_wrapper(
    monkeypatch, module_path: str, org_id: str = "org-123"
) -> None:
    """Patch _project_staff_management_access used by project member/role routes."""

    async def fake_project_staff_management_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(
        f"{module_path}._project_staff_management_access",
        fake_project_staff_management_access,
    )


def patch_staff_move_event_access(monkeypatch, module_path: str, org_id: str = "org-123") -> None:
    """Patch _staff_move_event_access wrapper used by the move events API."""

    async def fake_staff_move_event_access(**kwargs):
        del kwargs
        return admin_context(org_id=org_id)

    monkeypatch.setattr(f"{module_path}._staff_move_event_access", fake_staff_move_event_access)
