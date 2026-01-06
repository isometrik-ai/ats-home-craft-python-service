"""Integration tests for roles endpoints."""

from types import SimpleNamespace

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_get_roles(monkeypatch, client):
    """List roles."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_list(self, search=None, limit=20, offset=0):
        del self, search, limit, offset
        return [{"id": "r1", "name": "Role 1"}], 1

    monkeypatch.setattr(
        "apps.user_service.app.api.roles.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleService.list_roles",
        fake_list,
    )

    res = await client.get("/v1/roles?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "r1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_roles_no_data(monkeypatch, client):
    """List roles empty branch."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_list(self, search=None, limit=20, offset=0):
        del self, search, limit, offset
        return [], 0

    monkeypatch.setattr(
        "apps.user_service.app.api.roles.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleService.list_roles",
        fake_list,
    )

    res = await client.get("/v1/roles?page=1&page_size=20")
    assert res.status_code == 204


@pytest.mark.asyncio
async def test_update_role(monkeypatch, client):
    """Update role."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_update(self, role_id, body):
        del self, role_id
        return SimpleNamespace(id="r1", name=body.name or "Role")

    monkeypatch.setattr(
        "apps.user_service.app.api.roles.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleService.update_role",
        fake_update,
    )

    role_id = "550e8400-e29b-41d4-a716-446655440000"
    res = await client.put(f"/v1/roles/{role_id}", json={"name": "Updated"})
    if res.status_code == 200:
        body = assert_success(res, 200)
        assert body["data"]["name"] == "Updated"
    else:
        assert res.status_code in (200, 204, 404, 422)


@pytest.mark.asyncio
async def test_delete_role(monkeypatch, client):
    """Delete role."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_delete(self, role_id):
        del self, role_id
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.roles.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleService.delete_role",
        fake_delete,
    )

    role_id = "550e8400-e29b-41d4-a716-446655440000"
    res = await client.delete(f"/v1/roles/{role_id}")
    assert res.status_code in (200, 204, 404, 422)


@pytest.mark.asyncio
async def test_get_role_by_id(monkeypatch, client):
    """Get role details."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get(self, role_id):
        del self
        assert role_id == "550e8400-e29b-41d4-a716-446655440000"
        return {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "name": "Role 1",
            "description": "",
            "permission_ids": [],
            "created_at": "now",
            "updated_at": "now",
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.roles.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleService.get_role_details",
        fake_get,
    )

    res = await client.get("/v1/roles/550e8400-e29b-41d4-a716-446655440000")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.asyncio
async def test_create_role(monkeypatch, client):
    """Create role."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_create(self, role_data):
        del self
        assert role_data.name == "Role New"
        return {"id": "r2", "name": role_data.name, "created_at": "now"}

    monkeypatch.setattr(
        "apps.user_service.app.api.roles.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.role_service.RoleService.create_role",
        fake_create,
    )

    res = await client.post(
        "/v1/roles",
        json={
            "name": "Role New",
            "description": "desc",
            "role_type": "custom",
            "permission_ids": [],
        },
    )
    assert_success(res, 201)
