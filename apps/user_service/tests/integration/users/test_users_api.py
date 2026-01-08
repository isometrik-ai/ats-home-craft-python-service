"""Integration tests for users endpoints."""

import pytest

from apps.user_service.app.schemas.enums import OrganizationMemberStatus
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


def _ctx():
    """Return a reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_get_users_list(monkeypatch, client):
    """List users."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_list(self, organization_id, search=None, limit=20, offset=0):
        del self, organization_id, search, limit, offset
        return {"users": [{"user_id": "u2", "email": "user@example.com"}], "total_count": 1}

    monkeypatch.setattr(
        "apps.user_service.app.api.users.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.UserService.get_users_list",
        fake_list,
    )

    res = await client.get("/v1/users/list?page=1&page_size=10")
    body = assert_success(res, 200)
    assert body["data"][0]["user_id"] == "u2"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_user_profile(monkeypatch, client):
    """Get current user profile."""

    async def fake_extract(current_user, db_connection):
        del current_user, db_connection
        return _ctx()

    async def fake_get_profile(self, user_id, organization_id):
        del self, user_id, organization_id
        return {
            "profile_data": {"user_id": "u1", "email": "u1@example.com"},
            "audit_data": {"user_id": "u1"},
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.users.extract_user_context",
        fake_extract,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.UserService.get_user_profile_with_metadata",
        fake_get_profile,
    )

    res = await client.get("/v1/users/profile")
    body = assert_success(res, 200)
    assert body["data"]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_update_user_email(monkeypatch, client):
    """Update user email."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_update_email(self, user_id, organization_id, new_email):
        del self
        assert user_id == "u2"
        assert organization_id == "org-1"
        assert new_email == "new@example.com"
        return {
            "current_user_data": {
                "user_id": "u2",
                "email": "old@example.com",
                "full_name": "Old User",
                "first_name": "Old",
                "last_name": "User",
                "phone": None,
                "timezone": "UTC",
                "avatar_url": None,
                "status": OrganizationMemberStatus.ACTIVE.value,
                "role_id": "",
                "organization_id": "org-1",
            }
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.users.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.UserService.update_user_email",
        fake_update_email,
    )

    res = await client.put("/v1/users/u2/email", json={"email": "new@example.com"})
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_update_user_profile(monkeypatch, client):
    """Update own profile."""

    async def fake_extract(current_user, db_connection):
        del current_user, db_connection
        return _ctx()

    async def fake_update_profile(self, user_id, organization_id, body):
        del self
        assert user_id == "u1"
        assert organization_id == "org-1"
        assert body.first_name == "New"
        return {
            "updated_profile": {
                "user_id": "u1",
                "first_name": "New",
                "last_name": "User",
                "full_name": "New User",
                "email": "u1@example.com",
                "timezone": "UTC",
                "status": OrganizationMemberStatus.ACTIVE.value,
                "organization_id": "org-1",
                "identities": [],
                "permissions": [],
                "role_description": "",
            },
            "audit_data": {},
            "current_user_data": {
                "user_id": "u1",
                "email": "u1@example.com",
                "full_name": "Old User",
                "first_name": "Old",
                "last_name": "User",
                "phone": None,
                "timezone": "UTC",
                "avatar_url": None,
                "status": OrganizationMemberStatus.ACTIVE.value,
                "role_id": "",
                "organization_id": "org-1",
            },
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.users.extract_user_context",
        fake_extract,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.UserService.update_user_profile",
        fake_update_profile,
    )

    res = await client.put("/v1/users/update", json={"first_name": "New"})
    body = assert_success(res, 200)
    assert body["message"]


@pytest.mark.asyncio
async def test_ban_unban_user(monkeypatch, client):
    """Ban and unban user happy paths."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_ban(self, user_id, organization_id):
        del self
        assert user_id == "u2"
        assert organization_id == "org-1"
        return {
            "audit_data": {},
            "current_user_data": {
                "user_id": "u2",
                "email": "ban@example.com",
                "full_name": "Ban User",
                "first_name": "Ban",
                "last_name": "User",
                "phone": None,
                "timezone": "UTC",
                "avatar_url": None,
                "status": OrganizationMemberStatus.ACTIVE.value,
                "role_id": "",
                "organization_id": "org-1",
            },
        }

    async def fake_unban(self, user_id, organization_id):
        del self
        assert user_id == "u2"
        assert organization_id == "org-1"
        return {
            "audit_data": {},
            "current_user_data": {
                "user_id": "u2",
                "email": "ban@example.com",
                "full_name": "Ban User",
                "first_name": "Ban",
                "last_name": "User",
                "phone": None,
                "timezone": "UTC",
                "avatar_url": None,
                "status": OrganizationMemberStatus.ACTIVE.value,
                "role_id": "",
                "organization_id": "org-1",
            },
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.users.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.UserService.ban_user",
        fake_ban,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.UserService.unban_user",
        fake_unban,
    )

    res_ban = await client.post("/v1/users/ban/u2")
    assert_success(res_ban, 200)

    res_unban = await client.post("/v1/users/unban/u2")
    assert_success(res_unban, 200)
