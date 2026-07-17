"""Unit tests for idempotent household invitation accept."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import HouseholdInvitationStatus
from apps.user_service.app.services.household_invitation_service import (
    HouseholdInvitationService,
)

TOKEN = "test-token"
CONTACT_ID = "22222222-2222-2222-2222-222222222222"
ORG_ID = "11111111-1111-1111-1111-111111111111"
INVITATION_ID = "33333333-3333-3333-3333-333333333333"
CONTACT_UNIT_ID = "44444444-4444-4444-4444-444444444444"


def _accepted_invitation() -> dict[str, Any]:
    """Build a household invitation row in accepted status."""
    return {
        "id": INVITATION_ID,
        "organization_id": ORG_ID,
        "contact_id": CONTACT_ID,
        "contact_unit_id": CONTACT_UNIT_ID,
        "phone_isd_code": "+91",
        "phone_number": "9876543210",
        "status": HouseholdInvitationStatus.ACCEPTED.value,
    }


def _service() -> HouseholdInvitationService:
    """Build HouseholdInvitationService with mocked Supabase clients."""
    return HouseholdInvitationService(
        db_connection=object(),
        supabase_client=MagicMock(),
        supabase_anon_client=MagicMock(),
    )


@pytest.mark.asyncio
async def test_accept_reaccept_skips_side_effects() -> None:
    """Re-accept signs in without re-running activation or mark-accepted."""
    service = _service()
    invitation = _accepted_invitation()
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_token_hash = AsyncMock(return_value=invitation)
    service.contacts_repo = MagicMock()
    service.contacts_repo.get_contact_details = AsyncMock(
        return_value={"id": CONTACT_ID, "first_name": "A", "last_name": "B"}
    )
    service.contact_units_repo = MagicMock()
    service.contact_units_repo.activate_contact_unit = AsyncMock()
    service.onboarding_repo = MagicMock()
    service.onboarding_repo.ensure_steps = AsyncMock()
    service.invitations_repo.mark_accepted = AsyncMock()

    session = MagicMock(access_token="access-token", refresh_token="refresh-token")
    user = MagicMock(id="auth-user-1", email=None, user_metadata={"first_name": "A"})
    auth_result = MagicMock(session=session, user=user)

    with (
        patch(
            "apps.user_service.app.services.household_invitation_service.app_settings"
        ) as mock_settings,
        patch.object(
            HouseholdInvitationService,
            "_sign_in_provisioned_member",
            new_callable=AsyncMock,
            return_value=auth_result,
        ) as mock_sign_in,
        patch(
            "apps.user_service.app.services.household_invitation_service.ContactsService"
        ) as mock_contacts_cls,
    ):
        mock_settings.household_invitation_bypass_supabase_auth = False
        mock_contacts_cls.return_value.provision_auth_for_existing_contact = AsyncMock(
            return_value={"user_id": "auth-user-1"}
        )

        result = await service.accept(token=TOKEN, password="Secret@123")

    assert result["already_accepted"] is True
    assert result["access_token"] == "access-token"
    service.contact_units_repo.activate_contact_unit.assert_not_awaited()
    service.onboarding_repo.ensure_steps.assert_not_awaited()
    service.invitations_repo.mark_accepted.assert_not_awaited()
    mock_sign_in.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_bypass_skips_login() -> None:
    """Bypass mode updates password but skips Supabase sign-in."""
    service = _service()
    invitation = _accepted_invitation()
    invitation["status"] = "pending"
    service.invitations_repo = MagicMock()
    service.invitations_repo.get_by_token_hash = AsyncMock(return_value=invitation)
    service.contacts_repo = MagicMock()
    service.contacts_repo.get_contact_details = AsyncMock(
        return_value={
            "id": CONTACT_ID,
            "first_name": "A",
            "last_name": "B",
            "user_id": None,
        }
    )
    service.contact_units_repo = MagicMock()
    service.contact_units_repo.activate_contact_unit = AsyncMock()
    service.onboarding_repo = MagicMock()
    service.onboarding_repo.ensure_steps = AsyncMock()
    service.invitations_repo.mark_accepted = AsyncMock()

    with (
        patch(
            "apps.user_service.app.services.household_invitation_service.app_settings"
        ) as mock_settings,
        patch.object(
            HouseholdInvitationService,
            "_update_member_password",
            new_callable=AsyncMock,
        ) as mock_update_password,
        patch.object(
            HouseholdInvitationService,
            "_sign_in_provisioned_member",
            new_callable=AsyncMock,
        ) as mock_sign_in,
        patch(
            "apps.user_service.app.services.household_invitation_service.ContactsService"
        ) as mock_contacts_cls,
    ):
        mock_settings.household_invitation_bypass_supabase_auth = True
        mock_contacts_cls.return_value.provision_auth_for_existing_contact = AsyncMock(
            return_value={
                "user_id": "auth-user-1",
                "first_name": "A",
                "last_name": "B",
            }
        )

        result = await service.accept(token=TOKEN, password="Secret@123")

    assert result["auth_bypassed"] is True
    assert result["access_token"] is None
    assert result["user"]["id"] == "auth-user-1"
    mock_sign_in.assert_not_awaited()
    mock_update_password.assert_awaited_once_with(
        user_id="auth-user-1",
        password="Secret@123",
    )
    service.contact_units_repo.activate_contact_unit.assert_awaited_once()
    service.invitations_repo.mark_accepted.assert_awaited_once()
