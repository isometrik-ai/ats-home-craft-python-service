"""Integration tests for permissions endpoints."""

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_get_permissions(monkeypatch, client):
    """List permissions."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get_all(self):
        del self
        return [{"id": "p1", "name": "perm"}]

    monkeypatch.setattr(
        "apps.user_service.app.api.permissions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsService.get_all_permissions",
        fake_get_all,
    )

    res = await client.get("/v1/permissions")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "p1"


@pytest.mark.asyncio
async def test_get_permissions_no_data(monkeypatch, client):
    """List permissions empty branch."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get_all(self):
        del self
        return []

    monkeypatch.setattr(
        "apps.user_service.app.api.permissions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsService.get_all_permissions",
        fake_get_all,
    )

    res = await client.get("/v1/permissions")
    assert res.status_code == 204


@pytest.mark.asyncio
async def test_delete_permission(monkeypatch, client):
    """Delete permission."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_delete(self, permission_id):
        del self
        assert permission_id == "p1"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.permissions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsService.delete_permission",
        fake_delete,
    )

    res = await client.delete("/v1/permissions/p1")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_get_permission_by_id(monkeypatch, client):
    """Get permission by id."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get(self, perm_id):
        del self
        assert perm_id == "p1"
        return {"id": "p1", "name": "perm"}

    monkeypatch.setattr(
        "apps.user_service.app.api.permissions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsService.get_permission_by_id",
        fake_get,
    )

    res = await client.get("/v1/permissions/p1")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "p1"


@pytest.mark.asyncio
async def test_create_permission(monkeypatch, client):
    """Create permission."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_create(self, perm_data):
        del self
        assert perm_data.name == "Test Perm"

        class FakePerm(dict):
            """Simple permission payload for tests."""

            def __init__(self):
                super().__init__(
                    id="p2",
                    name="Test Perm",
                    code=perm_data.code,
                    description=perm_data.description,
                    category=perm_data.category,
                    created_at="now",
                )

            def __getattr__(self, item):
                return self[item]

            def model_dump(self, **_kwargs):
                """Dump as dict."""
                return dict(self)

        return FakePerm()

    monkeypatch.setattr(
        "apps.user_service.app.api.permissions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.permission_service.PermissionsService.create_permission",
        fake_create,
    )

    res = await client.post(
        "/v1/permissions",
        json={
            "name": "Test Perm",
            "code": "test.perm",
            "category": "test",
            "description": "desc",
        },
    )
    assert_success(res, 201)
