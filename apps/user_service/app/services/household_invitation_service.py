"""Household invitation business logic (phone-based, standalone flow)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.household_invitations_repository import (
    HouseholdInvitationsRepository,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.schemas.enums import (
    HouseholdInvitationStatus,
    HouseholdMemberStatus,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    hash_token,
)
from apps.user_service.app.utils.household_invitation_sms import (
    mask_phone,
    send_household_invitation_sms,
)
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_utils.http_exceptions import (
    ConflictException,
    GoneException,
    NotFoundException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class HouseholdInvitationService:
    """Phone-based household invitations for portal-access family members."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.invitations_repo = HouseholdInvitationsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)
        self.organization_repo = OrganizationRepository(db_connection)

    @staticmethod
    def _generate_token() -> tuple[str, str]:
        """Return (raw_token, token_hash)."""
        token = secrets.token_urlsafe(32)
        return token, hash_token(token)

    @staticmethod
    def _generate_invite_url(token: str) -> str:
        """Build the mobile deep-link the SMS should open."""
        return (
            f"{app_settings.shared_settings.website_url.rstrip('/')}/household-invite?token={token}"
        )

    @staticmethod
    def derive_member_status(
        *,
        portal_access: bool,
        unit_link_status: str,
    ) -> str:
        """Map stored rows to API member_status."""
        if not portal_access:
            return HouseholdMemberStatus.JOINED.value
        if unit_link_status == "pending":
            return HouseholdMemberStatus.INVITED.value
        return HouseholdMemberStatus.JOINED.value

    def _validate_invitation(
        self,
        invitation: dict[str, Any] | None,
        *,
        allow_accepted: bool = False,
    ) -> dict[str, Any]:
        """Ensure invitation exists, is usable, and not expired."""
        if not invitation:
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )
        status = invitation.get("status")
        if status == HouseholdInvitationStatus.ACCEPTED.value:
            if allow_accepted:
                return invitation
            raise ConflictException(
                message_key="contact_onboarding.errors.invitation_already_accepted",
                custom_code=CustomStatusCode.CONFLICT,
            )
        if status != HouseholdInvitationStatus.PENDING.value:
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )
        expires_at = invitation.get("expires_at")
        if isinstance(expires_at, datetime) and expires_at <= datetime.now(timezone.utc):
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )
        return invitation

    async def _dispatch_sms(
        self,
        *,
        phone_isd_code: str,
        phone_number: str,
        inviter_name: str,
        invitee_name: str,
        invite_url: str,
    ) -> None:
        """Send the invitation SMS (provider wired in household_invitation_sms)."""
        send_household_invitation_sms(
            phone_isd_code=phone_isd_code,
            phone_number=phone_number,
            inviter_name=inviter_name,
            invitee_name=invitee_name,
            invite_url=invite_url,
        )

    async def create_and_send(
        self,
        *,
        primary_contact_id: str,
        family_contact_id: str,
        contact_unit_id: str,
        phone_isd_code: str,
        phone_number: str,
        invitee_first_name: str | None,
        invitee_last_name: str | None,
        inviter_first_name: str | None,
        inviter_last_name: str | None,
    ) -> dict[str, Any]:
        """Create a phone invitation and dispatch the SMS."""
        org_id = self.user_context.organization_id if self.user_context else None
        assert org_id

        token, token_hash = self._generate_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=app_settings.invite_expiry_days)
        invitation = await self.invitations_repo.insert_invitation(
            {
                "organization_id": org_id,
                "contact_id": family_contact_id,
                "contact_unit_id": contact_unit_id,
                "invited_by_contact_id": primary_contact_id,
                "phone_isd_code": phone_isd_code,
                "phone_number": phone_number,
                "token": token,
                "token_hash": token_hash,
                "expires_at": expires_at,
            }
        )

        invite_url = self._generate_invite_url(token)
        inviter_name = build_full_name(None, inviter_first_name, inviter_last_name)
        invitee_name = build_full_name(None, invitee_first_name, invitee_last_name)
        await self._dispatch_sms(
            phone_isd_code=phone_isd_code,
            phone_number=phone_number,
            inviter_name=inviter_name.strip() or "A household member",
            invitee_name=invitee_name.strip() or "there",
            invite_url=invite_url,
        )
        return {
            "invitation_id": str(invitation["id"]),
            "contact_unit_id": contact_unit_id,
            "member_status": HouseholdMemberStatus.INVITED.value,
            "phone_masked": mask_phone(
                phone_isd_code=phone_isd_code,
                phone_number=phone_number,
            ),
            "invite_url": invite_url,
        }

    async def validate_token(self, *, token: str) -> dict[str, Any]:
        """Return invite details for the acceptance screen."""
        invitation = self._validate_invitation(
            await self.invitations_repo.get_by_token_hash(hash_token(token))
        )
        org_id = str(invitation["organization_id"])
        organization = await self.organization_repo.get_organization_by_id(org_id)
        contact = await self.contacts_repo.get_contact_details(
            contact_id=str(invitation["contact_id"]),
            organization_id=org_id,
        )
        invitee_name = build_full_name(
            contact.get("prefix") if contact else None,
            contact.get("first_name") if contact else None,
            contact.get("last_name") if contact else None,
        )
        return {
            "invitee_name": invitee_name.strip() or None,
            "organization_name": organization.get("name") if organization else None,
            "phone_masked": mask_phone(
                phone_isd_code=str(invitation["phone_isd_code"]),
                phone_number=str(invitation["phone_number"]),
            ),
            "expires_at": format_iso_datetime(invitation.get("expires_at")),
        }

    async def accept(self, *, token: str) -> dict[str, Any]:
        """Accept a phone invitation: provision auth, activate unit, seed onboarding."""
        invitation = self._validate_invitation(
            await self.invitations_repo.get_by_token_hash(
                hash_token(token),
                for_update=True,
            )
        )
        org_id = str(invitation["organization_id"])
        contact_id = str(invitation["contact_id"])

        contact = await self.contacts_repo.get_contact_details(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not contact:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        user_context = UserContext(
            user_id=None,
            email=None,
            organization_id=org_id,
        )
        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=user_context,
            supabase_client=self.supabase_client,
        )
        await contacts_service.provision_auth_for_existing_contact(contact_id=contact_id)
        await self.contact_units_repo.activate_contact_unit(
            organization_id=org_id,
            contact_unit_id=str(invitation["contact_unit_id"]),
        )
        await self.onboarding_repo.ensure_steps(
            organization_id=org_id,
            contact_id=contact_id,
        )
        await self.invitations_repo.mark_accepted(invitation_id=str(invitation["id"]))

        return {
            "contact_id": contact_id,
            "organization_id": org_id,
            "contact_unit_id": str(invitation["contact_unit_id"]),
            "member_status": HouseholdMemberStatus.JOINED.value,
            "phone_masked": mask_phone(
                phone_isd_code=str(invitation["phone_isd_code"]),
                phone_number=str(invitation["phone_number"]),
            ),
        }

    async def resend(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
        inviter_first_name: str | None,
        inviter_last_name: str | None,
    ) -> dict[str, Any]:
        """Regenerate token and resend the invitation SMS."""
        org_id = self.user_context.organization_id if self.user_context else None
        assert org_id

        link = await self.contact_units_repo.get_household_link(
            organization_id=org_id,
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        if not link:
            raise NotFoundException(
                message_key="contact_onboarding.errors.household_member_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        invitation = await self.invitations_repo.get_pending_by_contact_unit(
            organization_id=org_id,
            contact_unit_id=contact_unit_id,
        )
        if not invitation:
            raise NotFoundException(
                message_key="contact_onboarding.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        contact = await self.contacts_repo.get_contact_details(
            contact_id=str(link["contact_id"]),
            organization_id=org_id,
        )
        token, token_hash = self._generate_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=app_settings.invite_expiry_days)
        renewed = await self.invitations_repo.renew_invitation(
            invitation_id=str(invitation["id"]),
            token=token,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        if not renewed:
            raise NotFoundException(
                message_key="contact_onboarding.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        inviter_name = build_full_name(None, inviter_first_name, inviter_last_name)
        invitee_name = build_full_name(
            contact.get("prefix") if contact else None,
            contact.get("first_name") if contact else None,
            contact.get("last_name") if contact else None,
        )
        invite_url = self._generate_invite_url(token)
        await self._dispatch_sms(
            phone_isd_code=str(renewed["phone_isd_code"]),
            phone_number=str(renewed["phone_number"]),
            inviter_name=inviter_name.strip() or "A household member",
            invitee_name=invitee_name.strip() or "there",
            invite_url=invite_url,
        )
        return {
            "invitation_id": str(renewed["id"]),
            "contact_unit_id": contact_unit_id,
            "member_status": HouseholdMemberStatus.INVITED.value,
            "phone_masked": mask_phone(
                phone_isd_code=str(renewed["phone_isd_code"]),
                phone_number=str(renewed["phone_number"]),
            ),
            "invite_url": invite_url,
        }

    async def cancel_for_contact_unit(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> None:
        """Cancel a pending invitation when a household member is removed."""
        await self.invitations_repo.cancel_by_contact_unit(
            organization_id=organization_id,
            contact_unit_id=contact_unit_id,
        )
