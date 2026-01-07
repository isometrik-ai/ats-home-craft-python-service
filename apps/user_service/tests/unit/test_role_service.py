"""Unit tests for RoleService helpers and critical branches."""

import datetime

import pytest

from apps.user_service.app.services.role_service import RoleService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
)


class _FakeRoleRepo:
    """Lightweight fake role repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.exists = True
        self.in_use_count = 0
        self.unique_name = True

    async def check_role_exists(self, role_id, organization_id):
        """Return existence flag for role."""
        self.calls["check_role_exists"] = (role_id, organization_id)
        return self.exists

    async def check_role_usage(self, role_id, organization_id):
        """Return usage count for role."""
        self.calls["check_role_usage"] = (role_id, organization_id)
        return self.in_use_count

    async def delete_role(self, role_id, organization_id):
        """Track deletion call."""
        self.calls["delete_role"] = (role_id, organization_id)
        return None

    async def check_role_name_unique(self, name, organization_id, exclude_role_id=None):
        """Return uniqueness flag for name."""
        self.calls["check_role_name_unique"] = (name, organization_id, exclude_role_id)
        return self.unique_name

    async def get_roles_list_enriched(self, organization_id, search, limit, offset):
        """Return fake roles list."""
        self.calls["get_roles_list_enriched"] = (organization_id, search, limit, offset)
        return [
            {
                "id": "r1",
                "name": "Role 1",
                "description": "desc",
                "is_default": False,
                "created_at": datetime.datetime(2024, 1, 1),
                "user_count": 2,
                "permission_count": 1,
                "permission_categories": "{}",
            }
        ]

    async def get_roles_count(self, organization_id, search):
        """Return fake count."""
        self.calls["get_roles_count"] = (organization_id, search)
        return 1

    async def get_permissions_for_roles(self, role_ids, organization_id):
        """Return permissions for provided role ids."""
        self.calls["get_permissions_for_roles"] = (role_ids, organization_id)
        return [{"role_id": "r1", "permission_id": "p1"}]


def _ctx(org_id="org-1"):
    """Build a reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_delete_role_not_found(monkeypatch):
    """Raises NotFoundException when role absent."""

    fake_repo = _FakeRoleRepo()
    fake_repo.exists = False
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException):
        await service.delete_role("missing")


@pytest.mark.asyncio
async def test_delete_role_in_use(monkeypatch):
    """Raises ForbiddenException when role in use."""

    fake_repo = _FakeRoleRepo()
    fake_repo.in_use_count = 5
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)

    with pytest.raises(ForbiddenException):
        await service.delete_role("r1")


@pytest.mark.asyncio
async def test_validate_role_name_conflict(monkeypatch):
    """Role name uniqueness check raises ConflictException."""

    fake_repo = _FakeRoleRepo()
    fake_repo.unique_name = False
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)

    with pytest.raises(ConflictException):
        await service._validate_role_name_unique("Dup", "org-1")  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_list_roles_formats(monkeypatch):
    """List roles maps repo data and permissions."""

    fake_repo = _FakeRoleRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    roles, total = await service.list_roles(search="r", limit=5, offset=0)

    assert total == 1
    assert roles[0].id == "r1"
    assert roles[0].permission_ids == ["p1"]
    assert fake_repo.calls["get_roles_list_enriched"][1] == "r"


def test_compute_permission_changes():
    """Static helper computes additions/removals."""

    current_ids = {"p1", "p2"}
    new_ids = {"p2", "p3"}

    to_add, to_remove = RoleService._compute_permission_changes(current_ids, new_ids)  # pylint: disable=protected-access

    assert to_add == {"p3"}
    assert to_remove == {"p1"}
