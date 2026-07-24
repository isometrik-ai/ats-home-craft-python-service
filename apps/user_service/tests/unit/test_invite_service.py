"""Unit tests for InviteService team assignment on invite create/accept."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from apps.user_service.app.schemas.invites import InviteCreateRequest
from apps.user_service.app.services.invite_service import InviteService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

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


def _create_body(
    *, team_id: UUID | None = None, tags: list[str] | None = None
) -> InviteCreateRequest:
    """Build InviteCreateRequest with optional team_id and tags."""
    return InviteCreateRequest(
        email="invitee@example.com",
        first_name="Jane",
        last_name="Doe",
        role_id=UUID(ROLE_ID),
        team_id=team_id,
        tags=tags,
    )


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
    assert "tags" not in metadata


@pytest.mark.asyncio
async def test_metadata_includes_tags():
    """Metadata stores tags when provided on create request."""
    service = InviteService(user_context=None, db_connection=None)
    metadata = service._build_invite_metadata(  # pylint: disable=protected-access
        _create_body(tags=[" sales ", "onboarding", ""])
    )

    assert metadata["tags"] == ["sales", "onboarding"]


@pytest.mark.asyncio
async def test_metadata_includes_empty_tags():
    """Metadata stores an empty tags list when explicitly provided."""
    service = InviteService(user_context=None, db_connection=None)
    metadata = service._build_invite_metadata(_create_body(tags=[]))  # pylint: disable=protected-access

    assert metadata["tags"] == []


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


def test_list_item_includes_tags():
    """List response exposes tags from invitation metadata."""
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
            "metadata": {"first_name": "Jane", "tags": ["sales", "vip"]},
        }
    )

    assert item["tags"] == ["sales", "vip"]
