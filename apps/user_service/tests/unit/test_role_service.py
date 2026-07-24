"""Unit tests for RoleService helpers and critical branches."""

import datetime
from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.admin_access_management import (
    CreateRoleRequest,
    UpdateRoleRequest,
)
from apps.user_service.app.services.role_service import RoleService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)

PERM_LEADS = "11111111-1111-1111-1111-111111111111"
PERM_CF_CREATE = "22222222-2222-2222-2222-222222222222"
PERM_CF_VIEW = "33333333-3333-3333-3333-333333333333"
PERM_CF_EDIT = "44444444-4444-4444-4444-444444444444"
PERM_CF_DELETE = "55555555-5555-5555-5555-555555555555"
ROLE_ID = "66666666-6666-6666-6666-666666666666"


class _FakeRoleRepo:
    """Lightweight fake role repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.exists = True
        self.in_use_count = 0
        self.unique_name = True
        self.role_row = None
        self.permission_rows = []
        self.current_permission_ids = []
        self.permission_lookup_rows = []
        self.create_role = AsyncMock(side_effect=self._create_role_impl)
        self.assign_permissions_to_role = AsyncMock()
        self.get_role_by_id = AsyncMock(side_effect=self._get_role_by_id_impl)
        self.get_role_permissions = AsyncMock(side_effect=self._get_role_permissions_impl)
        self.update_role = AsyncMock()
        self.get_role_permission_ids = AsyncMock(side_effect=self._get_role_permission_ids_impl)
        self.remove_permissions_from_role = AsyncMock()
        self.get_permissions_by_ids_or_codes = AsyncMock(
            side_effect=self._get_permissions_by_ids_or_codes_impl
        )
        self.delete_role = AsyncMock()

    async def _create_role_impl(self, name, description, organization_id):
        self.calls["create_role"] = (name, description, organization_id)
        return {"id": ROLE_ID, "name": name, "description": description, "is_default": False}

    async def _get_role_by_id_impl(self, role_id, organization_id):
        self.calls["get_role_by_id"] = (role_id, organization_id)
        return self.role_row

    async def _get_role_permissions_impl(self, role_id, organization_id):
        self.calls["get_role_permissions"] = (role_id, organization_id)
        return self.permission_rows

    async def _get_role_permission_ids_impl(self, role_id, organization_id):
        self.calls["get_role_permission_ids"] = (role_id, organization_id)
        return self.current_permission_ids

    async def _get_permissions_by_ids_or_codes_impl(
        self, permission_ids, organization_id, codes=None
    ):
        self.calls["get_permissions_by_ids_or_codes"] = (
            permission_ids,
            organization_id,
            codes,
        )
        return self.permission_lookup_rows

    async def check_role_exists(self, role_id, organization_id):
        """Return existence flag for role."""
        self.calls["check_role_exists"] = (role_id, organization_id)
        return self.exists

    async def check_role_usage(self, role_id, organization_id):
        """Return usage count for role."""
        self.calls["check_role_usage"] = (role_id, organization_id)
        return self.in_use_count

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


@pytest.mark.asyncio
async def test_create_role_with_permissions(monkeypatch):
    """create_role assigns resolved permissions after insert."""
    fake_repo = _FakeRoleRepo()
    fake_repo.permission_lookup_rows = [
        {"id": PERM_LEADS, "code": "leads_management.view"},
        {"id": PERM_CF_CREATE, "code": "custom_fields_management.create"},
        {"id": PERM_CF_VIEW, "code": "custom_fields_management.view"},
        {"id": PERM_CF_EDIT, "code": "custom_fields_management.edit"},
        {"id": PERM_CF_DELETE, "code": "custom_fields_management.delete"},
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    role = await service.create_role(
        CreateRoleRequest(name="Sales", description="Sales role", permission_ids=[PERM_LEADS])
    )

    assert role["id"] == ROLE_ID
    fake_repo.assign_permissions_to_role.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_role_without_permissions(monkeypatch):
    """create_role skips permission assignment when list is empty."""
    fake_repo = _FakeRoleRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    await service.create_role(CreateRoleRequest(name="Viewer", description=None, permission_ids=[]))

    fake_repo.assign_permissions_to_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_role_details_not_found(monkeypatch):
    """get_role_details raises when role is missing."""
    fake_repo = _FakeRoleRepo()
    fake_repo.role_row = None
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    with pytest.raises(NotFoundException):
        await service.get_role_details(ROLE_ID)


@pytest.mark.asyncio
async def test_get_role_details_success(monkeypatch):
    """get_role_details maps permissions into RoleDetailItem."""
    fake_repo = _FakeRoleRepo()
    fake_repo.role_row = {
        "id": ROLE_ID,
        "name": "Admin",
        "description": "desc",
        "is_default": True,
        "created_at": datetime.datetime(2024, 1, 1),
        "updated_at": datetime.datetime(2024, 1, 2),
    }
    fake_repo.permission_rows = [
        {
            "id": PERM_LEADS,
            "code": "leads_management.view",
            "name": "View leads",
            "category": "leads",
            "description": "View leads permission",
            "created_at": datetime.datetime(2024, 1, 1),
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    detail = await service.get_role_details(ROLE_ID)

    assert detail.name == "Admin"
    assert detail.permissions


@pytest.mark.asyncio
async def test_update_role_adds_and_removes_permissions(monkeypatch):
    """update_role computes permission delta instead of full replace."""
    fake_repo = _FakeRoleRepo()
    fake_repo.current_permission_ids = [PERM_LEADS]
    fake_repo.permission_lookup_rows = [
        {"id": PERM_LEADS, "code": "leads_management.view"},
        {"id": PERM_CF_CREATE, "code": "custom_fields_management.create"},
        {"id": PERM_CF_VIEW, "code": "custom_fields_management.view"},
        {"id": PERM_CF_EDIT, "code": "custom_fields_management.edit"},
        {"id": PERM_CF_DELETE, "code": "custom_fields_management.delete"},
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    await service.update_role(
        ROLE_ID,
        UpdateRoleRequest(
            name="Updated",
            description="New desc",
            permission_ids=[PERM_CF_CREATE],
        ),
    )

    fake_repo.update_role.assert_awaited_once()
    fake_repo.remove_permissions_from_role.assert_awaited_once()
    fake_repo.assign_permissions_to_role.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_permission_ids_invalid(monkeypatch):
    """Unknown permission ids raise BadRequestException."""
    fake_repo = _FakeRoleRepo()
    fake_repo.permission_lookup_rows = []
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    with pytest.raises(BadRequestException):
        await service._resolve_permission_ids([PERM_LEADS])  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_get_all_role_permissions_empty(monkeypatch):
    """_get_all_role_permissions returns {} for empty input."""
    fake_repo = _FakeRoleRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    assert await service._get_all_role_permissions([]) == {}  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_delete_role_success(monkeypatch):
    """delete_role removes role when unused."""
    fake_repo = _FakeRoleRepo()
    fake_repo.in_use_count = 0
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleRepository",
        lambda db_connection=None: fake_repo,
    )

    service = RoleService(user_context=_ctx(), db_connection=None)
    await service.delete_role(ROLE_ID)

    fake_repo.delete_role.assert_awaited_once()
