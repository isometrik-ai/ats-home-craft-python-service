"""Unit tests for invites API route handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.invites import (
    accept_and_set_password_invitation,
    get_organization_invitations,
    patch_invitation,
    validate_invite_link,
)
from apps.user_service.app.schemas.invites import (
    InviteAcceptBySettingPasswordRequest,
    InviteValidateLinkRequest,
    PatchInviteRequest,
)
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ROLE_ID = "990e8400-e29b-41d4-a716-446655440004"
INVITE_ID = "aa0e8400-e29b-41d4-a716-446655440005"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/invites",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
        }
    )


@pytest.mark.asyncio
async def test_validate_invite_link_route() -> None:
    """validate_invite_link delegates to InviteService."""
    with patch(
        "apps.user_service.app.api.invites.InviteService",
    ) as svc_cls:
        svc_cls.return_value.validate_invite_link = AsyncMock(
            return_value={"is_existing_user": True, "has_password": False}
        )
        response = await validate_invite_link(
            request=_request(),
            db_connection=MagicMock(),
            body=InviteValidateLinkRequest(token="tok-1"),
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_accept_and_set_password_sets_audit_context() -> None:
    """Accept invitation stores audit context from service outcome."""
    request = _request()
    outcome = SimpleNamespace(
        audit_user_context={"user_id": "u1"},
        audit_record_id="inv-1",
        audit_old={"status": "pending"},
        audit_new={"status": "accepted"},
        message_key="invitations.success.accepted",
        response={"ok": True},
    )
    with patch(
        "apps.user_service.app.api.invites.InviteService",
    ) as svc_cls:
        svc_cls.return_value.accept_and_set_password = AsyncMock(return_value=outcome)
        response = await accept_and_set_password_invitation(
            request=request,
            db_connection=MagicMock(),
            sb_admin_client=MagicMock(),
            sb_anon_client=MagicMock(),
            body=InviteAcceptBySettingPasswordRequest(token="tok-1", password="Secret@123"),
        )
    assert response.status_code == 202
    assert request.state.audit_user_context == {"user_id": "u1"}
    assert request.state.raw_audit_old_data == {"status": "pending"}


@pytest.mark.asyncio
async def test_get_organization_invitations_empty_returns_204() -> None:
    """Empty invitation list returns 204."""
    user_ctx = UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID)
    with (
        patch(
            "apps.user_service.app.api.invites.check_permissions",
            AsyncMock(return_value=user_ctx),
        ),
        patch(
            "apps.user_service.app.api.invites.InviteService",
        ) as svc_cls,
    ):
        svc_cls.return_value.get_organization_invitations = AsyncMock(
            return_value={"items": [], "total_count": 0, "page": 1, "page_size": 20}
        )
        response = await get_organization_invitations(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
            organization_id=ORG_ID,
            page=1,
            page_size=20,
            status=None,
        )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_accept_without_audit_snapshots() -> None:
    """Accept invitation skips audit snapshots when outcome omits them."""
    request = _request()
    outcome = SimpleNamespace(
        audit_user_context={"user_id": "u1"},
        audit_record_id="inv-1",
        audit_old=None,
        audit_new=None,
        message_key="invitations.success.accepted",
        response={"ok": True},
    )
    with patch("apps.user_service.app.api.invites.InviteService") as svc_cls:
        svc_cls.return_value.accept_and_set_password = AsyncMock(return_value=outcome)
        response = await accept_and_set_password_invitation(
            request=request,
            db_connection=MagicMock(),
            sb_admin_client=MagicMock(),
            sb_anon_client=MagicMock(),
            body=InviteAcceptBySettingPasswordRequest(token="tok-1", password="Secret@123"),
        )
    assert response.status_code == 202
    assert not hasattr(request.state, "raw_audit_old_data")


@pytest.mark.asyncio
async def test_get_organization_invitations_returns_list() -> None:
    """Non-empty invitation list returns 200."""
    user_ctx = UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID)
    with (
        patch(
            "apps.user_service.app.api.invites.check_permissions",
            AsyncMock(return_value=user_ctx),
        ),
        patch("apps.user_service.app.api.invites.InviteService") as svc_cls,
    ):
        svc_cls.return_value.get_organization_invitations = AsyncMock(
            return_value={
                "items": [{"id": INVITE_ID, "email": "inv@example.com"}],
                "total_count": 1,
                "page": 1,
                "page_size": 20,
            }
        )
        response = await get_organization_invitations(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
            organization_id=ORG_ID,
            page=1,
            page_size=20,
            status=None,
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_patch_invitation_route() -> None:
    """patch_invitation stores audit snapshots from service."""
    from uuid import UUID

    request = _request()
    user_ctx = UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID)
    with (
        patch(
            "apps.user_service.app.api.invites.check_permissions",
            AsyncMock(return_value=user_ctx),
        ),
        patch("apps.user_service.app.api.invites.InviteService") as svc_cls,
    ):
        svc_cls.return_value.patch_invitation = AsyncMock(
            return_value=({"status": "pending"}, {"status": "pending", "role_id": ROLE_ID})
        )
        response = await patch_invitation(
            request=request,
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
            invite_id=INVITE_ID,
            body=PatchInviteRequest(role_id=UUID(ROLE_ID)),
        )
    assert response.status_code == 200
    assert request.state.raw_audit_old_data == {"status": "pending"}
