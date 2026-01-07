"""Unit tests for PermissionsService."""

import datetime

import pytest

from apps.user_service.app.services.permission_service import PermissionsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


class _FakePermissionsRepo:
    """Lightweight fake repository for permissions."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}

    async def get_all_permissions(self, organization_id):
        """Return fake permissions list."""
        self.calls["get_all_permissions"] = organization_id
        return [
            {
                "id": "p1",
                "name": "Perm 1",
                "code": "perm.1",
                "category": "cat",
                "description": "d",
                "created_at": datetime.datetime(2024, 1, 1),
            }
        ]

    async def get_permission_by_id(self, permission_id, organization_id):
        """Return permission dict or None."""
        self.calls["get_permission_by_id"] = (permission_id, organization_id)
        if permission_id == "missing":
            return None
        return {
            "id": permission_id,
            "name": "Name",
            "code": "code",
            "category": "cat",
            "description": "desc",
            "created_at": datetime.datetime(2024, 1, 2),
        }

    async def create_permission(self, permission_data, organization_id):
        """Create permission or return None to simulate failure."""
        self.calls["create_permission"] = (permission_data, organization_id)
        if permission_data.name == "fail":
            return None
        return {
            "id": "new-id",
            "name": permission_data.name,
            "code": permission_data.code,
            "category": permission_data.category,
            "description": permission_data.description,
            "created_at": datetime.datetime(2024, 1, 3),
        }

    async def delete_permission(self, permission_id, organization_id):
        """Delete permission and return row or None."""
        self.calls["delete_permission"] = (permission_id, organization_id)
        return None if permission_id == "missing" else {"id": permission_id}


def _ctx(org_id="org-1"):
    """Reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_get_all_permissions_formats(monkeypatch):
    """Should map repository data into PermissionItem list."""

    fake_repo = _FakePermissionsRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsRepository",
        lambda db_connection=None: fake_repo,
    )

    service = PermissionsService(user_context=_ctx(), db_connection=None)

    permissions = await service.get_all_permissions()

    assert len(permissions) == 1
    assert permissions[0].code == "perm.1"
    assert fake_repo.calls["get_all_permissions"] == "org-1"


@pytest.mark.asyncio
async def test_get_permission_by_id_happy_path(monkeypatch):
    """Returns PermissionItem when found."""

    fake_repo = _FakePermissionsRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsRepository",
        lambda db_connection=None: fake_repo,
    )

    service = PermissionsService(user_context=_ctx(), db_connection=None)
    permission = await service.get_permission_by_id("p1")

    assert permission.id == "p1"
    assert permission.name == "Name"
    assert fake_repo.calls["get_permission_by_id"] == ("p1", "org-1")


@pytest.mark.asyncio
async def test_get_permission_by_id_not_found(monkeypatch):
    """Raises NotFoundException when repo returns None."""

    fake_repo = _FakePermissionsRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsRepository",
        lambda db_connection=None: fake_repo,
    )

    service = PermissionsService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException):
        await service.get_permission_by_id("missing")


@pytest.mark.asyncio
async def test_create_permission_happy_path(monkeypatch):
    """Creates permission and returns PermissionItem."""

    fake_repo = _FakePermissionsRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsRepository",
        lambda db_connection=None: fake_repo,
    )

    service = PermissionsService(user_context=_ctx(), db_connection=None)

    class Obj:
        """Input payload stub."""

        def __init__(self):
            self.name = "Test"
            self.code = "test.code"
            self.category = "cat"
            self.description = "desc"

    permission = await service.create_permission(Obj())

    assert permission.id == "new-id"
    assert permission.code == "test.code"
    assert fake_repo.calls["create_permission"][1] == "org-1"


@pytest.mark.asyncio
async def test_create_permission_failure(monkeypatch):
    """Raises ValidationException when repo returns None."""

    fake_repo = _FakePermissionsRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsRepository",
        lambda db_connection=None: fake_repo,
    )

    service = PermissionsService(user_context=_ctx(), db_connection=None)

    class Obj:
        """Input payload stub that triggers failure."""

        def __init__(self):
            self.name = "fail"
            self.code = "code"
            self.category = "cat"
            self.description = "desc"

    with pytest.raises(ValidationException):
        await service.create_permission(Obj())


@pytest.mark.asyncio
async def test_delete_permission_not_found(monkeypatch):
    """Raises NotFoundException when delete returns None."""

    fake_repo = _FakePermissionsRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsRepository",
        lambda db_connection=None: fake_repo,
    )

    service = PermissionsService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException):
        await service.delete_permission("missing")
