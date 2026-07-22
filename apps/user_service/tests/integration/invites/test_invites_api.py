"""Integration tests for invite endpoints."""

from types import SimpleNamespace

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_accept_and_set_password_invitation(monkeypatch, client):
    """Accept invitation by setting password."""

    async def fake_accept(self, body):
        del self
        assert body.token == "tok123"
        assert body.password == "NewPass123!"
        return SimpleNamespace(
            response={"access_token": "atk", "refresh_token": "rtk"},
            message_key="invitations.success.invitation_accepted_new_account",
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.InviteService.accept_and_set_password",
        fake_accept,
    )

    res = await client.post(
        "/v1/invite/set-password",
        json={"token": "tok123", "password": "NewPass123!"},
    )
    body = assert_success(res, 202)
    assert body["data"]["access_token"] == "atk"
    assert body["message"] == (
        "Your account has been created, and you have been added to the organization."
    )


@pytest.mark.asyncio
async def test_accept_invitation_password_optional(monkeypatch, client):
    """Accept invitation allows password to be omitted (service decides branch)."""

    async def fake_accept(self, body):
        del self
        assert body.token == "tok123"
        assert body.password is None
        return SimpleNamespace(
            response={"access_token": "atk", "refresh_token": "rtk"},
            message_key="invitations.success.invitation_accepted_signed_in",
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.InviteService.accept_and_set_password",
        fake_accept,
    )

    res = await client.post(
        "/v1/invite/set-password",
        json={"token": "tok123"},
    )
    body = assert_success(res, 202)
    assert body["data"]["access_token"] == "atk"
    assert body["message"] == "You have been signed in and added to the organization."


@pytest.mark.asyncio
async def test_create_invitation(monkeypatch, client):
    """Create a new invitation."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        del current_user, db_connection, permission_codes, organization_id
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_create(self, organization_id, body):
        del self, organization_id, body
        return {
            "invite_id": "inv-1",
            "invite_url": "http://example.com/invite",
            "email": "invitee@example.com",
            "expires_at": "2024-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.invites.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.InviteService.create_invitation",
        fake_create,
    )

    res = await client.post(
        "/v1/invite/org-123",
        json={
            "email": "invitee@example.com",
            "role_id": "550e8400-e29b-41d4-a716-446655440000",
            "first_name": "Test",
            "last_name": "User",
            "team_id": "660e8400-e29b-41d4-a716-446655440001",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["invite_id"] == "inv-1"


@pytest.mark.asyncio
async def test_get_organization_invitations(monkeypatch, client):
    """List invitations for an organization."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        del current_user, db_connection, permission_codes, organization_id
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get(self, organization_id, page, page_size, status=None):
        del self, organization_id, page, page_size, status
        return {
            "items": [{"id": "inv-1", "email": "invitee@example.com"}],
            "total_count": 1,
            "page": 1,
            "page_size": 20,
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.invites.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.InviteService.get_organization_invitations",
        fake_get,
    )

    res = await client.get("/v1/invite/org-123?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "inv-1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_resend_invitation(monkeypatch, client):
    """Resend an invitation."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        del current_user, db_connection, permission_codes, organization_id
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_resend(self, invite_id):
        del self
        assert invite_id == "inv-1"
        return {
            "invite_id": "inv-1",
            "invite_url": "http://example.com/invite",
            "email": "invitee@example.com",
            "expires_at": "2024-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.invites.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.InviteService.resend_invitation",
        fake_resend,
    )

    res = await client.put("/v1/invite/resend/inv-1")
    body = assert_success(res, 202)
    assert body["data"]["invite_id"] == "inv-1"


@pytest.mark.asyncio
async def test_delete_invitation(monkeypatch, client):
    """Delete an invitation."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        del current_user, db_connection, permission_codes, organization_id
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_delete(self, invite_id):
        del self
        assert invite_id == "inv-1"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.invites.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.InviteService.delete_invitation",
        fake_delete,
    )

    res = await client.delete("/v1/invite/inv-1")
    body = assert_success(res, 200)
    assert body["code"]
