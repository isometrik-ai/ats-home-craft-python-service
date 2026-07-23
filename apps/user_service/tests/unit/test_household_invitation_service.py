"""Unit tests for HouseholdInvitationService helpers and flows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from supabase import AuthApiError

from apps.user_service.app.schemas.enums import (
    HouseholdInvitationStatus,
    HouseholdMemberStatus,
)
from apps.user_service.app.services.household_invitation_service import (
    HouseholdInvitationService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    GoneException,
    InternalServerErrorException,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
CONTACT_ID = "22222222-2222-2222-2222-222222222222"
CONTACT_UNIT_ID = "44444444-4444-4444-4444-444444444444"
INVITATION_ID = "33333333-3333-3333-3333-333333333333"
TOKEN = "raw-token"


def _pending_invitation(**overrides: Any) -> dict[str, Any]:
    """Build a pending household invitation row."""
    row = {
        "id": INVITATION_ID,
        "organization_id": ORG_ID,
        "contact_id": CONTACT_ID,
        "contact_unit_id": CONTACT_UNIT_ID,
        "phone_isd_code": "+91",
        "phone_number": "9876543210",
        "status": HouseholdInvitationStatus.PENDING.value,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
    }
    row.update(overrides)
    return row


def _service(*, ctx: UserContext | None = None) -> HouseholdInvitationService:
    """Build service with mocked Supabase clients."""
    return HouseholdInvitationService(
        db_connection=object(),
        user_context=ctx,
        supabase_client=MagicMock(),
        supabase_anon_client=MagicMock(),
    )


def test_derive_member_status_branches() -> None:
    """derive_member_status covers joined, invited, and revoked mappings."""
    assert (
        HouseholdInvitationService.derive_member_status(
            portal_access=True,
            unit_link_status="pending",
            invitation_status=HouseholdInvitationStatus.PENDING.value,
        )
        == HouseholdMemberStatus.INVITED.value
    )
    assert (
        HouseholdInvitationService.derive_member_status(
            portal_access=True,
            unit_link_status="pending",
            invitation_status=HouseholdInvitationStatus.CANCELLED.value,
        )
        == HouseholdMemberStatus.REVOKED.value
    )
    assert (
        HouseholdInvitationService.derive_member_status(
            portal_access=False,
            unit_link_status="active",
            invitation_status=None,
        )
        == HouseholdMemberStatus.JOINED.value
    )
    assert (
        HouseholdInvitationService.derive_member_status(
            portal_access=True,
            unit_link_status="active",
            invitation_status=None,
            has_user=True,
        )
        == HouseholdMemberStatus.JOINED.value
    )


def test_validate_invitation_branches() -> None:
    """_validate_invitation rejects missing, accepted, declined, and expired rows."""
    service = _service()
    with pytest.raises(GoneException):
        service._validate_invitation(None)  # pylint: disable=protected-access

    with pytest.raises(ConflictException):
        service._validate_invitation(  # pylint: disable=protected-access
            _pending_invitation(status=HouseholdInvitationStatus.ACCEPTED.value)
        )

    with pytest.raises(GoneException):
        service._validate_invitation(  # pylint: disable=protected-access
            _pending_invitation(status=HouseholdInvitationStatus.DECLINED.value)
        )

    with pytest.raises(GoneException):
        service._validate_invitation(  # pylint: disable=protected-access
            _pending_invitation(
                status="unknown",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )


def test_normalize_login_phone() -> None:
    """_normalize_login_phone strips non-digits from combined phone values."""
    assert HouseholdInvitationService._normalize_login_phone("+91", "98765-43210") == "919876543210"


@pytest.mark.asyncio
async def test_create_and_send_inserts_new_invitation() -> None:
    """create_and_send inserts a new invitation when none exists."""
    service = _service(ctx=UserContext(user_id="u1", email="o@example.com", organization_id=ORG_ID))
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_contact_unit = AsyncMock(return_value=None)
    service.invitations_repo.insert_invitation = AsyncMock(
        return_value={"id": INVITATION_ID, "phone_isd_code": "+91", "phone_number": "9112233000"}
    )

    with patch.object(HouseholdInvitationService, "_dispatch_sms", AsyncMock()):
        result = await service.create_and_send(
            primary_contact_id="primary-1",
            family_contact_id=CONTACT_ID,
            contact_unit_id=CONTACT_UNIT_ID,
            phone_isd_code="+91",
            phone_number="9112233000",
            invitee_first_name="Ashya",
            invitee_last_name="S",
            inviter_first_name="Owner",
            inviter_last_name="One",
        )

    service.invitations_repo.insert_invitation.assert_awaited_once()
    assert result["invitation_id"] == INVITATION_ID
    assert result["member_status"] == HouseholdMemberStatus.INVITED.value


@pytest.mark.asyncio
async def test_create_and_send_pending_conflict() -> None:
    """create_and_send rejects when a pending invitation already exists."""
    service = _service(ctx=UserContext(user_id="u1", email="o@example.com", organization_id=ORG_ID))
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_contact_unit = AsyncMock(
        return_value={"status": HouseholdInvitationStatus.PENDING.value}
    )

    with pytest.raises(ConflictException):
        await service.create_and_send(
            primary_contact_id="primary-1",
            family_contact_id=CONTACT_ID,
            contact_unit_id=CONTACT_UNIT_ID,
            phone_isd_code="+91",
            phone_number="9112233000",
            invitee_first_name="Ashya",
            invitee_last_name=None,
            inviter_first_name="Owner",
            inviter_last_name=None,
        )


@pytest.mark.asyncio
async def test_validate_token_returns_invite_details() -> None:
    """validate_token returns invitee and organization details."""
    service = _service()
    invitation = _pending_invitation()
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_token_hash = AsyncMock(return_value=invitation)
    service.organization_repo = MagicMock()
    service.organization_repo.get_organization_by_id = AsyncMock(return_value={"name": "Acme Org"})
    service.contacts_repo = MagicMock()
    service.contacts_repo.get_contact_details = AsyncMock(
        return_value={"first_name": "Jane", "last_name": "Doe"}
    )

    result = await service.validate_token(token=TOKEN)

    assert result["organization_name"] == "Acme Org"
    assert result["invitee_name"] == "Jane Doe"
    assert result["already_accepted"] is False


@pytest.mark.asyncio
async def test_decline_already_declined_is_idempotent() -> None:
    """decline returns current state when invitation is already declined."""
    service = _service()
    invitation = _pending_invitation(status=HouseholdInvitationStatus.DECLINED.value)
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_token_hash = AsyncMock(return_value=invitation)

    result = await service.decline(token=TOKEN)

    assert result["invitation_status"] == HouseholdInvitationStatus.DECLINED.value
    assert result["contact_deleted"] is False


@pytest.mark.asyncio
async def test_decline_deletes_orphan_contact() -> None:
    """decline removes link and soft-deletes contact when no links remain."""
    service = _service()
    invitation = _pending_invitation()
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_token_hash = AsyncMock(return_value=invitation)
    service.invitations_repo.mark_declined = AsyncMock(return_value={"id": INVITATION_ID})
    service.contact_units_repo = MagicMock()
    service.contact_units_repo.delete_link = AsyncMock()
    service.contact_units_repo.count_links_for_contact = AsyncMock(return_value=0)
    service.contacts_repo = MagicMock()
    service.contacts_repo.soft_delete_contact = AsyncMock()

    result = await service.decline(token=TOKEN)

    assert result["contact_deleted"] is True
    service.contacts_repo.soft_delete_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_renews_pending_invitation() -> None:
    """resend regenerates token and dispatches SMS for pending invitation."""
    service = _service(ctx=UserContext(user_id="u1", email="o@example.com", organization_id=ORG_ID))
    service.contact_units_repo = MagicMock()
    service.contact_units_repo.get_household_link = AsyncMock(
        return_value={"contact_id": CONTACT_ID}
    )
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_pending_by_contact_unit = AsyncMock(
        return_value={"id": INVITATION_ID}
    )
    service.invitations_repo.renew_invitation = AsyncMock(
        return_value={
            "id": INVITATION_ID,
            "phone_isd_code": "+91",
            "phone_number": "9876543210",
        }
    )
    service.contacts_repo = MagicMock()
    service.contacts_repo.get_contact_details = AsyncMock(
        return_value={"first_name": "Jane", "last_name": "Doe"}
    )

    with patch.object(HouseholdInvitationService, "_dispatch_sms", AsyncMock()):
        result = await service.resend(
            primary_contact_id="primary-1",
            contact_unit_id=CONTACT_UNIT_ID,
            inviter_first_name="Owner",
            inviter_last_name="One",
        )

    assert result["invitation_id"] == INVITATION_ID
    assert "invite_url" in result


@pytest.mark.asyncio
async def test_cancel_for_contact_unit_delegates() -> None:
    """cancel_for_contact_unit forwards to repository."""
    service = _service()
    service.invitations_repo = MagicMock()
    service.invitations_repo.cancel_by_contact_unit = AsyncMock()

    await service.cancel_for_contact_unit(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    service.invitations_repo.cancel_by_contact_unit.assert_awaited_once_with(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )


@pytest.mark.asyncio
async def test_sign_in_provisioned_member_auth_errors() -> None:
    """_sign_in_provisioned_member maps Supabase auth failures to API errors."""
    service = _service()
    auth_error = AuthApiError("invalid", status=400, code="invalid_credentials")
    with (
        patch.object(
            HouseholdInvitationService,
            "_update_member_password",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.household_invitation_service.login_user_with_phone",
            AsyncMock(side_effect=auth_error),
        ),
    ):
        with pytest.raises(BadRequestException):
            await service._sign_in_provisioned_member(  # pylint: disable=protected-access
                user_id="auth-1",
                phone="919876543210",
                password="Secret@123",
            )


@pytest.mark.asyncio
async def test_update_member_password_requires_client() -> None:
    """_update_member_password raises when Supabase admin client is missing."""
    service = HouseholdInvitationService(db_connection=object(), supabase_client=None)
    with pytest.raises(InternalServerErrorException):
        await service._update_member_password(user_id="auth-1", password="Secret@123")  # pylint: disable=protected-access
