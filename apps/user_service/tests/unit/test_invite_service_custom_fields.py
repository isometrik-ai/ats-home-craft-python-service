"""Unit tests for invite custom fields handling."""

import json
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import BloodGroup, Gender, InviteStatus
from apps.user_service.app.schemas.invites import InviteCreateRequest
from apps.user_service.app.services.invite_service import InviteService
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "550e8400-e29b-41d4-a716-446655440001"
ROLE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _invite_service() -> InviteService:
    """Build InviteService with a fixed admin user context for unit tests."""
    return InviteService(
        user_context=UserContext(
            user_id="550e8400-e29b-41d4-a716-446655440002",
            email="admin@example.com",
            organization_id=ORG_ID,
        ),
        db_connection=MagicMock(),
    )


def test_build_invite_list_item_includes_custom_fields() -> None:
    """Pending invite list items expose custom_fields from metadata."""
    service = _invite_service()
    custom_fields = [{"field_id": "f1", "value": "Legal", "type": "text", "instance_id": "i1"}]

    item = service.build_invite_list_item(
        {
            "id": "inv-1",
            "email": "user@example.com",
            "role_id": "role-1",
            "status": InviteStatus.PENDING.value,
            "invited_by": "admin-1",
            "expires_at": "2024-12-26T10:00:00Z",
            "created_at": "2024-12-19T10:00:00Z",
            "updated_at": "2024-12-19T10:00:00Z",
            "metadata": {
                "first_name": "Jane",
                "last_name": "Doe",
                "custom_fields": custom_fields,
            },
        }
    )

    assert item["custom_fields"] == custom_fields


def test_invite_list_item_legacy_metadata_no_fields() -> None:
    """Legacy invites without custom_fields in metadata remain compatible."""
    service = _invite_service()

    item = service.build_invite_list_item(
        {
            "id": "inv-1",
            "email": "user@example.com",
            "role_id": "role-1",
            "status": InviteStatus.PENDING.value,
            "invited_by": "admin-1",
            "expires_at": "2024-12-26T10:00:00Z",
            "created_at": "2024-12-19T10:00:00Z",
            "updated_at": "2024-12-19T10:00:00Z",
            "metadata": {
                "first_name": "Jane",
                "last_name": "Doe",
            },
        }
    )

    assert item["custom_fields"] == []


def test_build_invite_metadata_includes_profile_fields() -> None:
    """Invite metadata stores optional member profile fields for acceptance."""
    service = _invite_service()
    body = InviteCreateRequest(
        first_name="Jane",
        email="user@example.com",
        role_id=ROLE_ID,
        avatar_url="house-of-apps-legal-ai/user-id/avatar.jpg",
        gender=Gender.FEMALE,
        dob=date(1990, 5, 15),
        blood_group=BloodGroup.A_POSITIVE,
        designation="Community Manager",
    )

    metadata = service._build_invite_metadata(body)

    assert metadata["avatar_url"] == "house-of-apps-legal-ai/user-id/avatar.jpg"
    assert metadata["gender"] == Gender.FEMALE.value
    assert metadata["dob"] == "1990-05-15"
    assert metadata["blood_group"] == BloodGroup.A_POSITIVE.value
    assert metadata["designation"] == "Community Manager"


def test_build_invite_list_item_includes_profile_fields() -> None:
    """Pending invite list items expose profile fields from metadata."""
    service = _invite_service()

    item = service.build_invite_list_item(
        {
            "id": "inv-1",
            "email": "user@example.com",
            "role_id": "role-1",
            "status": InviteStatus.PENDING.value,
            "invited_by": "admin-1",
            "expires_at": "2024-12-26T10:00:00Z",
            "created_at": "2024-12-19T10:00:00Z",
            "updated_at": "2024-12-19T10:00:00Z",
            "metadata": {
                "first_name": "Jane",
                "last_name": "Doe",
                "avatar_url": "avatars/jane.jpg",
                "gender": Gender.FEMALE.value,
                "dob": "1990-05-15",
                "blood_group": BloodGroup.B_POSITIVE.value,
                "designation": "Secretary",
            },
        }
    )

    assert item["avatar_url"] == "avatars/jane.jpg"
    assert item["gender"] == Gender.FEMALE.value
    assert item["dob"] == "1990-05-15"
    assert item["blood_group"] == BloodGroup.B_POSITIVE.value
    assert item["designation"] == "Secretary"


@pytest.mark.asyncio
async def test_create_invite_omits_custom_fields_ok() -> None:
    """Omitting custom_fields on invite create does not require field validation."""
    service = _invite_service()

    service.organization_repository = MagicMock()
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Org", "settings": {}}
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
        return_value={"id": "role-1", "name": "Member"}
    )

    mock_user_service = MagicMock()
    mock_user_service.validate_member_custom_fields_for_create = AsyncMock(return_value=[])

    with (
        patch(
            "apps.user_service.app.services.invite_service.UserService",
            return_value=mock_user_service,
        ),
        patch(
            "apps.user_service.app.services.invite_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value={"user_metadata": {"first_name": "Admin", "last_name": "User"}},
        ),
        patch(
            "apps.user_service.app.services.invite_service.send_organization_invitation_email",
        ),
    ):
        from apps.user_service.app.schemas.invites import InviteCreateRequest

        body = InviteCreateRequest(
            first_name="Jane",
            email="user@example.com",
            role_id=ROLE_ID,
        )
        await service.create_invitation(ORG_ID, body)

    mock_user_service.validate_member_custom_fields_for_create.assert_awaited_once_with(
        [],
        enforce_required=False,
    )
    invite_data: dict[str, Any] = service.invite_repository.create_invite.await_args.args[0]
    assert invite_data["metadata"]["custom_fields"] == []


@pytest.mark.asyncio
async def test_create_invite_stores_custom_fields_metadata() -> None:
    """create_invitation validates custom_fields and persists them in invite metadata."""
    service = _invite_service()
    validated = [{"field_id": "f1", "value": "Legal", "type": "text", "instance_id": "i1"}]

    service.organization_repository = MagicMock()
    service.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"id": ORG_ID, "name": "Org", "settings": {}}
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
        return_value={"id": "role-1", "name": "Member"}
    )

    mock_user_service = MagicMock()
    mock_user_service.validate_member_custom_fields_for_create = AsyncMock(return_value=validated)

    with (
        patch(
            "apps.user_service.app.services.invite_service.UserService",
            return_value=mock_user_service,
        ),
        patch(
            "apps.user_service.app.services.invite_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value={"user_metadata": {"first_name": "Admin", "last_name": "User"}},
        ),
        patch(
            "apps.user_service.app.services.invite_service.send_organization_invitation_email",
        ),
    ):
        from apps.user_service.app.schemas.invites import InviteCreateRequest

        body = InviteCreateRequest(
            first_name="Jane",
            email="user@example.com",
            role_id=ROLE_ID,
            custom_fields=[{"field_id": "f1", "value": "Legal"}],
        )
        await service.create_invitation(ORG_ID, body)

    mock_user_service.validate_member_custom_fields_for_create.assert_awaited_once_with(
        [{"field_id": "f1", "value": "Legal"}],
        enforce_required=False,
    )
    invite_data: dict[str, Any] = service.invite_repository.create_invite.await_args.args[0]
    metadata = invite_data["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata["custom_fields"] == validated
