"""Unit tests for InviteService create/accept and helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from supabase import AuthApiError

from apps.user_service.app.db.repositories.invite_repository import (
    PatchPendingInviteResult,
)
from apps.user_service.app.schemas.enums import (
    INVITE_ACCEPT_MSG_KEY_NEW_ACCOUNT,
    InviteAcceptAuthKind,
    InviteStatus,
)
from apps.user_service.app.schemas.invites import (
    InviteAcceptBySettingPasswordRequest,
    InviteCreateRequest,
    PatchInviteRequest,
)
from apps.user_service.app.services.invite_service import InviteService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GoneException,
    NotFoundException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ROLE_ID = "660e8400-e29b-41d4-a716-446655440001"
TEAM_ID = "770e8400-e29b-41d4-a716-446655440002"
USER_ID = "880e8400-e29b-41d4-a716-446655440003"
INVITER_ID = "990e8400-e29b-41d4-a716-446655440004"


def _ctx() -> UserContext:
    """Build a reusable UserContext for invite tests."""
    return UserContext(
        user_id=INVITER_ID,
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


def _create_body(*, team_id: UUID | None = None) -> InviteCreateRequest:
    """Build InviteCreateRequest with optional team_id."""
    return InviteCreateRequest(
        email="invitee@example.com",
        first_name="Jane",
        last_name="Doe",
        role_id=UUID(ROLE_ID),
        team_id=team_id,
    )


def _invite_service(*, ctx: UserContext | None = None) -> InviteService:
    """Build InviteService with mocked repos (no DB)."""
    service = InviteService(user_context=ctx, db_connection=MagicMock())
    service.invite_repository = MagicMock()
    service.organization_repository = MagicMock()
    service.role_repository = MagicMock()
    service.organization_member_repository = MagicMock()
    service.user_repository = MagicMock()
    service.team_repository = MagicMock()
    return service


def _pending_invite(**overrides) -> dict:
    """Build a valid pending invitation row."""
    row = {
        "id": "inv-1",
        "organization_id": ORG_ID,
        "email": "invitee@example.com",
        "role_id": ROLE_ID,
        "status": InviteStatus.PENDING.value,
        "invited_by": INVITER_ID,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "metadata": {"first_name": "Jane", "last_name": "Doe"},
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_metadata_includes_team_id():
    """Metadata stores team_id when provided on create request."""
    service = InviteService(user_context=None, db_connection=None)
    metadata = service._build_invite_metadata(  # pylint: disable=protected-access
        _create_body(team_id=UUID(TEAM_ID))
    )

    assert metadata["team_id"] == TEAM_ID
    assert metadata["first_name"] == "Jane"


@pytest.mark.asyncio
async def test_metadata_omits_team_id():
    """Metadata omits team_id when not provided on create request."""
    service = InviteService(user_context=None, db_connection=None)
    metadata = service._build_invite_metadata(_create_body())  # pylint: disable=protected-access

    assert "team_id" not in metadata


@pytest.mark.asyncio
async def test_validate_team_missing():
    """Create validation fails when team does not exist in the organization."""
    service = InviteService(user_context=None, db_connection=None)
    service.team_repository = MagicMock()
    service.team_repository.get_team_detail = AsyncMock(return_value=(None, []))

    with pytest.raises(NotFoundException):
        await service._validate_team_in_org(TEAM_ID, ORG_ID)  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_add_invitee_to_existing_team():
    """Accept path adds the user to the team when team_id is present."""
    service = InviteService(user_context=None, db_connection=None)
    service.team_repository = MagicMock()
    service.team_repository.get_team_detail = AsyncMock(
        return_value=({"id": TEAM_ID, "name": "Sales"}, [])
    )
    service.team_repository._insert_team_members = AsyncMock()  # pylint: disable=protected-access

    await service._add_invitee_to_team(  # pylint: disable=protected-access
        team_id=TEAM_ID,
        organization_id=ORG_ID,
        user_id=USER_ID,
        added_by=INVITER_ID,
    )

    service.team_repository._insert_team_members.assert_awaited_once()  # pylint: disable=protected-access
    call_kwargs = (
        service.team_repository._insert_team_members.await_args.kwargs  # pylint: disable=protected-access
    )
    assert call_kwargs["team_id"] == TEAM_ID
    assert call_kwargs["added_by"] == INVITER_ID
    assert call_kwargs["member_data"][0].member_id == USER_ID


@pytest.mark.asyncio
async def test_add_invitee_skips_no_team_id():
    """Accept path skips team insert when no team_id on invitation."""
    service = InviteService(user_context=None, db_connection=None)
    service.team_repository = MagicMock()
    service.team_repository.get_team_detail = AsyncMock()
    service.team_repository._insert_team_members = AsyncMock()  # pylint: disable=protected-access

    await service._add_invitee_to_team(  # pylint: disable=protected-access
        team_id=None,
        organization_id=ORG_ID,
        user_id=USER_ID,
        added_by=INVITER_ID,
    )

    service.team_repository.get_team_detail.assert_not_awaited()
    service.team_repository._insert_team_members.assert_not_awaited()  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_add_invitee_skips_deleted_team():
    """Accept path still succeeds when team was deleted after invite was sent."""
    service = InviteService(user_context=None, db_connection=None)
    service.team_repository = MagicMock()
    service.team_repository.get_team_detail = AsyncMock(return_value=(None, []))
    service.team_repository._insert_team_members = AsyncMock()  # pylint: disable=protected-access

    await service._add_invitee_to_team(  # pylint: disable=protected-access
        team_id=TEAM_ID,
        organization_id=ORG_ID,
        user_id=USER_ID,
        added_by=INVITER_ID,
    )

    service.team_repository._insert_team_members.assert_not_awaited()  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_create_invite_stores_team_id(monkeypatch):
    """Create invitation validates team and persists team_id in invite metadata."""
    service = InviteService(user_context=_ctx(), db_connection=MagicMock())
    service.organization_repository = MagicMock()
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Acme"}
    )
    service.invite_repository = MagicMock()
    service.invite_repository.check_user_membership = AsyncMock(return_value=False)
    service.invite_repository.check_existing_invite = AsyncMock(return_value=None)
    service.invite_repository.create_invite = AsyncMock(
        return_value={
            "id": "inv-1",
            "expires_at": "2024-12-26T10:00:00Z",
        }
    )
    service.role_repository = MagicMock()
    service.role_repository.get_role_by_id = AsyncMock(
        return_value={"id": ROLE_ID, "name": "Member"}
    )
    service.team_repository = MagicMock()
    service.team_repository.get_team_detail = AsyncMock(
        return_value=({"id": TEAM_ID, "name": "Sales"}, [])
    )

    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.get_user_by_id",
        AsyncMock(return_value={"user_metadata": {"first_name": "Admin"}}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.send_organization_invitation_email",
        lambda **kwargs: None,
    )

    await service.create_invitation(ORG_ID, _create_body(team_id=UUID(TEAM_ID)))

    create_call = service.invite_repository.create_invite.await_args.args[0]
    assert create_call["metadata"]["team_id"] == TEAM_ID
    service.team_repository.get_team_detail.assert_awaited_once_with(TEAM_ID, ORG_ID)


def test_list_item_includes_team_id():
    """List response exposes team_id from invitation metadata."""
    service = InviteService(user_context=None, db_connection=None)
    item = service.build_invite_list_item(
        {
            "id": "inv-1",
            "email": "invitee@example.com",
            "role_id": ROLE_ID,
            "status": "pending",
            "invited_by": INVITER_ID,
            "expires_at": "2024-12-26T10:00:00Z",
            "created_at": "2024-12-19T10:00:00Z",
            "updated_at": "2024-12-19T10:00:00Z",
            "metadata": {"first_name": "Jane", "team_id": TEAM_ID},
        }
    )

    assert item["team_id"] == TEAM_ID


def test_parse_json_field_handles_dict_and_string():
    """JSON helper accepts dicts and JSON strings."""
    service = InviteService(user_context=None, db_connection=None)
    assert service._parse_json_field({"a": 1}) == {"a": 1}  # pylint: disable=protected-access
    assert service._parse_json_field('{"b": 2}') == {"b": 2}  # pylint: disable=protected-access
    assert service._parse_json_field(None) == {}  # pylint: disable=protected-access


def test_validate_invitation_expired_raises_gone():
    """Expired invitations raise gone."""
    service = InviteService(user_context=None, db_connection=None)
    expired = _pending_invite(
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with pytest.raises(GoneException):
        service._validate_invitation_for_acceptance(expired)  # pylint: disable=protected-access


def test_validate_invitation_accepted_raises_conflict():
    """Already accepted invitations raise conflict."""
    service = InviteService(user_context=None, db_connection=None)
    accepted = _pending_invite(status=InviteStatus.ACCEPTED.value)
    with pytest.raises(ConflictException):
        service._validate_invitation_for_acceptance(accepted)  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_create_invitation_forbidden_org():
    """Create rejects when context org differs from path org."""
    service = _invite_service(ctx=_ctx())
    with pytest.raises(ForbiddenException):
        await service.create_invitation("00000000-0000-4000-8000-000000000001", _create_body())


@pytest.mark.asyncio
async def test_create_invitation_member_exists():
    """Create rejects when invitee is already a member."""
    service = _invite_service(ctx=_ctx())
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Acme"}
    )
    service.invite_repository.check_user_membership = AsyncMock(return_value=True)

    with pytest.raises(ConflictException):
        await service.create_invitation(ORG_ID, _create_body())


@pytest.mark.asyncio
async def test_create_invitation_pending_conflict(monkeypatch):
    """Create rejects when a non-expired pending invite exists."""
    service = _invite_service(ctx=_ctx())
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Acme"}
    )
    service.invite_repository.check_user_membership = AsyncMock(return_value=False)
    service.invite_repository.check_existing_invite = AsyncMock(
        return_value={
            "id": "old-inv",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        }
    )
    service.role_repository.get_role_by_id = AsyncMock(
        return_value={"id": ROLE_ID, "name": "Member"}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.get_user_by_id",
        AsyncMock(return_value={"user_metadata": {}}),
    )

    with pytest.raises(ConflictException):
        await service.create_invitation(ORG_ID, _create_body())


@pytest.mark.asyncio
async def test_create_invitation_renews_expired(monkeypatch):
    """Create renews an expired pending invite instead of inserting."""
    service = _invite_service(ctx=_ctx())
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Acme"}
    )
    service.invite_repository.check_user_membership = AsyncMock(return_value=False)
    service.invite_repository.check_existing_invite = AsyncMock(
        return_value={
            "id": "old-inv",
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
        }
    )
    service.invite_repository.renew_expired_invite = AsyncMock(
        return_value={"id": "old-inv", "expires_at": datetime.now(timezone.utc)}
    )
    service.invite_repository.create_invite = AsyncMock()
    service.role_repository.get_role_by_id = AsyncMock(
        return_value={"id": ROLE_ID, "name": "Member"}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.get_user_by_id",
        AsyncMock(return_value={"user_metadata": {"first_name": "Admin"}}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.send_organization_invitation_email",
        lambda **kwargs: None,
    )

    result = await service.create_invitation(ORG_ID, _create_body())

    service.invite_repository.renew_expired_invite.assert_awaited_once()
    service.invite_repository.create_invite.assert_not_awaited()
    assert result["invite_id"] == "old-inv"


@pytest.mark.asyncio
async def test_validate_invite_link_new_user():
    """Validate link reports non-existing user without password."""
    service = _invite_service()
    service.invite_repository.get_invite_by_token = AsyncMock(return_value=_pending_invite())
    service.user_repository.get_auth_user_by_email = AsyncMock(return_value=None)

    result = await service.validate_invite_link("raw-token")

    assert result == {"is_existing_user": False, "has_password": False}


@pytest.mark.asyncio
async def test_validate_invite_link_existing_with_password():
    """Validate link reports existing user with password."""
    service = _invite_service()
    service.invite_repository.get_invite_by_token = AsyncMock(return_value=_pending_invite())
    service.user_repository.get_auth_user_by_email = AsyncMock(
        return_value={"encrypted_password": "hash"}
    )

    result = await service.validate_invite_link("raw-token")

    assert result["is_existing_user"] is True
    assert result["has_password"] is True


@pytest.mark.asyncio
async def test_accept_invite_new_signup(monkeypatch):
    """Accept path creates member and marks invite accepted."""
    service = _invite_service()
    invite = _pending_invite()
    service.invite_repository.get_invite_by_token = AsyncMock(return_value=invite)
    service.invite_repository.check_user_membership = AsyncMock(return_value=False)
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={
            "id": ORG_ID,
            "settings": '{"isometrik_application_details": {"appId": "app-1"}}',
        }
    )
    service.role_repository.get_role_by_id = AsyncMock(
        return_value={"id": ROLE_ID, "name": "Member"}
    )
    service.user_repository.get_auth_user_by_email = AsyncMock(return_value=None)

    session = MagicMock(
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
        expires_at=999,
    )
    user = MagicMock(
        id=USER_ID,
        email="invitee@example.com",
        user_metadata={"first_name": "Jane"},
    )
    auth_result = MagicMock(session=session, user=user)
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.sign_up_supabase_user",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.create_isometrik_user",
        AsyncMock(return_value={"userId": "iso-1"}),
    )
    service.organization_member_repository.add_member = AsyncMock()
    service.invite_repository.update_invite_status = AsyncMock()
    service.organization_repository.update_subscription_users = AsyncMock()
    service._add_invitee_to_team = AsyncMock()  # pylint: disable=protected-access

    body = InviteAcceptBySettingPasswordRequest(token="invite-token", password="Secret123!")
    outcome = await service.accept_and_set_password(body)

    assert outcome.message_key == INVITE_ACCEPT_MSG_KEY_NEW_ACCOUNT
    assert outcome.response.access_token == "access"
    service.invite_repository.update_invite_status.assert_awaited_once()
    service.organization_member_repository.add_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_invite_already_member():
    """Accept rejects when email is already an org member."""
    service = _invite_service()
    service.invite_repository.get_invite_by_token = AsyncMock(return_value=_pending_invite())
    service.invite_repository.check_user_membership = AsyncMock(return_value=True)

    body = InviteAcceptBySettingPasswordRequest(token="invite-token", password="Secret123!")
    with pytest.raises(ConflictException):
        await service.accept_and_set_password(body)


@pytest.mark.asyncio
async def test_get_organization_invitations():
    """List invitations returns paginated items."""
    service = _invite_service(ctx=_ctx())
    service.invite_repository.get_organization_invites = AsyncMock(return_value=[_pending_invite()])
    service.invite_repository.get_organization_invites_count = AsyncMock(return_value=1)

    result = await service.get_organization_invitations(ORG_ID, page=1, page_size=10)

    assert result["total_count"] == 1
    assert result["items"][0]["email"] == "invitee@example.com"


@pytest.mark.asyncio
async def test_patch_invitation_success():
    """Patch updates role on pending invitation."""
    service = _invite_service(ctx=_ctx())
    new_role = "aa0e8400-e29b-41d4-a716-446655440099"
    service.invite_repository.patch_pending_invitation = AsyncMock(
        return_value=PatchPendingInviteResult(
            updated_row={"id": "inv-1"},
            invite_ok=True,
            role_ok=True,
            previous_role_id=ROLE_ID,
        )
    )

    audit_old, audit_new = await service.patch_invitation(
        "inv-1",
        PatchInviteRequest(role_id=UUID(new_role)),
    )

    assert audit_old["role_id"] == ROLE_ID
    assert audit_new["role_id"] == new_role


@pytest.mark.asyncio
async def test_patch_invitation_role_not_found():
    """Patch raises when new role is missing."""
    service = _invite_service(ctx=_ctx())
    service.invite_repository.patch_pending_invitation = AsyncMock(
        return_value=PatchPendingInviteResult(
            updated_row=None,
            invite_ok=True,
            role_ok=False,
        )
    )

    with pytest.raises(NotFoundException):
        await service.patch_invitation(
            "inv-1",
            PatchInviteRequest(role_id=UUID(ROLE_ID)),
        )


@pytest.mark.asyncio
async def test_delete_invitation_delegates_to_repo():
    """Delete forwards to repository with org scope."""
    service = _invite_service(ctx=_ctx())
    service.invite_repository.delete_invite = AsyncMock()

    await service.delete_invitation("inv-1")

    service.invite_repository.delete_invite.assert_awaited_once_with("inv-1", ORG_ID)


@pytest.mark.asyncio
async def test_validate_subscription_expired():
    """Subscription validation rejects expired plans."""
    service = _invite_service()
    service.organization_member_repository.get_users_total_count = AsyncMock(return_value=1)
    org = {
        "id": ORG_ID,
        "subscription": {
            "max_users": 10,
            "end_date": "2020-01-01T00:00:00+00:00",
        },
    }

    with pytest.raises(ForbiddenException):
        await service.validate_organization_subscription(org)


@pytest.mark.asyncio
async def test_validate_subscription_missing():
    """Subscription validation rejects organizations without subscription."""
    service = _invite_service()
    with pytest.raises(ForbiddenException):
        await service.validate_organization_subscription({"id": ORG_ID})


@pytest.mark.asyncio
async def test_validate_subscription_max_users_exceeded():
    """Subscription validation rejects when member count meets max_users."""
    service = _invite_service()
    service.organization_member_repository.get_users_total_count = AsyncMock(return_value=10)
    org = {
        "id": ORG_ID,
        "subscription": {
            "max_users": 10,
            "end_date": "2099-01-01T00:00:00+00:00",
        },
    }
    with pytest.raises(ConflictException):
        await service.validate_organization_subscription(org)


@pytest.mark.asyncio
async def test_validate_subscription_json_string():
    """Subscription validation parses JSON-encoded subscription payloads."""
    service = _invite_service()
    service.organization_member_repository.get_users_total_count = AsyncMock(return_value=1)
    org = {
        "id": ORG_ID,
        "subscription": '{"max_users": 10, "end_date": "2099-01-01T00:00:00+00:00"}',
    }
    assert await service.validate_organization_subscription(org) is True


@pytest.mark.asyncio
async def test_create_invitation_org_not_found():
    """Create rejects when organization does not exist."""
    service = _invite_service(ctx=_ctx())
    service.organization_repository.get_organization_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await service.create_invitation(ORG_ID, _create_body())


@pytest.mark.asyncio
async def test_get_organization_invitations_forbidden():
    """List invitations rejects cross-organization access."""
    service = _invite_service(ctx=_ctx())
    with pytest.raises(ForbiddenException):
        await service.get_organization_invitations("00000000-0000-4000-8000-000000000001")


@pytest.mark.asyncio
async def test_resend_invitation_success(monkeypatch):
    """Resend generates a fresh token and sends email."""
    service = _invite_service(ctx=_ctx())
    service.invite_repository.get_invite_by_id = AsyncMock(
        return_value={
            "id": "inv-1",
            "organization_id": ORG_ID,
            "email": "invitee@example.com",
            "role_id": ROLE_ID,
            "invited_by": INVITER_ID,
            "metadata": {"first_name": "Jane", "last_name": "Doe"},
        }
    )
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Acme"}
    )
    service.role_repository.get_role_by_id = AsyncMock(
        return_value={"id": ROLE_ID, "name": "Member"}
    )
    service.invite_repository.update_invite_token_and_expiration = AsyncMock(
        return_value={"id": "inv-1"}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.get_user_by_id",
        AsyncMock(return_value={"user_metadata": {"first_name": "Admin"}}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.invite_service.send_organization_invitation_email",
        lambda **kwargs: True,
    )

    result = await service.resend_invitation("inv-1")

    assert result["invite_id"] == "inv-1"
    assert "invite_url" in result


@pytest.mark.asyncio
async def test_authenticate_existing_user_invalid_credentials():
    """_authenticate_existing_user maps 400 AuthApiError to BadRequestException."""
    service = _invite_service()
    service.supabase_anon_client = MagicMock()
    auth_error = AuthApiError("invalid", status=400, code="invalid_credentials")
    with patch(
        "apps.user_service.app.services.invite_service.login_user",
        AsyncMock(side_effect=auth_error),
    ):
        with pytest.raises(BadRequestException):
            await service._authenticate_existing_user(  # pylint: disable=protected-access
                "invitee@example.com",
                "bad-password",
            )


@pytest.mark.asyncio
async def test_authenticate_or_signup_existing_passwordless():
    """Existing passwordless users authenticate via magic link exchange."""
    service = _invite_service()
    service.supabase_admin_client = MagicMock()
    service.supabase_anon_client = MagicMock()
    service.user_repository.get_auth_user_by_email = AsyncMock(
        return_value={"encrypted_password": None}
    )
    auth_result = MagicMock(session=MagicMock(access_token="tok"), user=MagicMock(id=USER_ID))
    with patch(
        "apps.user_service.app.services.invite_service.generate_magiclink_and_exchange_for_session",
        AsyncMock(return_value=auth_result),
    ):
        result, kind = await service._authenticate_or_signup_user(  # pylint: disable=protected-access
            email="invitee@example.com",
            password=None,
            inv_meta={"first_name": "Jane"},
            phone_number=None,
            phone_isd_code=None,
        )
    assert result is auth_result
    assert kind == InviteAcceptAuthKind.EXISTING_PASSWORDLESS


@pytest.mark.asyncio
async def test_authenticate_or_signup_requires_password_for_new_user():
    """New users must provide a password during invite acceptance."""
    service = _invite_service()
    service.user_repository.get_auth_user_by_email = AsyncMock(return_value=None)
    with pytest.raises(BadRequestException):
        await service._authenticate_or_signup_user(  # pylint: disable=protected-access
            email="invitee@example.com",
            password=None,
            inv_meta={"first_name": "Jane"},
            phone_number=None,
            phone_isd_code=None,
        )


def test_build_invite_list_item_phone_and_metadata_string():
    """build_invite_list_item parses metadata JSON and merges phone fields."""
    service = InviteService(user_context=None, db_connection=None)
    item = service.build_invite_list_item(
        {
            "id": "inv-1",
            "email": "invitee@example.com",
            "role_id": ROLE_ID,
            "status": "pending",
            "invited_by": INVITER_ID,
            "expires_at": "2024-12-26T10:00:00Z",
            "created_at": "2024-12-19T10:00:00Z",
            "updated_at": "2024-12-19T10:00:00Z",
            "metadata": '{"first_name":"Jane","phone_number":"1234567890","phone_isd_code":"+1"}',
        }
    )
    assert item["phone"] == "+11234567890"
    assert item["first_name"] == "Jane"


@pytest.mark.asyncio
async def test_patch_invitation_not_found():
    """Patch raises when invitation cannot be found."""
    service = _invite_service(ctx=_ctx())
    service.invite_repository.patch_pending_invitation = AsyncMock(
        return_value=PatchPendingInviteResult(
            updated_row=None,
            invite_ok=False,
            role_ok=False,
        )
    )
    with pytest.raises(NotFoundException):
        await service.patch_invitation(
            "inv-1",
            PatchInviteRequest(role_id=UUID(ROLE_ID)),
        )


@pytest.mark.asyncio
async def test_get_role_data_not_found():
    """_get_role_data raises when role is missing."""
    service = _invite_service()
    service.role_repository.get_role_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await service._get_role_data(ROLE_ID, ORG_ID)  # pylint: disable=protected-access
