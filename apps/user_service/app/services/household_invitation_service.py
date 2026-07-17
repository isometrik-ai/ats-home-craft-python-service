"""Household invitation business logic (phone-based, standalone flow)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from supabase import AsyncClient, AuthApiError

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
from libs.shared_db.supabase_db.auth_repository import (
    login_user_with_phone,
    update_password_by_user_id,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    GoneException,
    InternalServerErrorException,
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
        supabase_anon_client: AsyncClient | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.supabase_anon_client = supabase_anon_client
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
        if status == HouseholdInvitationStatus.DECLINED.value:
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_already_declined",
                custom_code=CustomStatusCode.GONE,
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
        invitation_row = await self.invitations_repo.get_by_token_hash(hash_token(token))
        already_accepted = (
            invitation_row is not None
            and invitation_row.get("status") == HouseholdInvitationStatus.ACCEPTED.value
        )
        invitation = self._validate_invitation(
            invitation_row,
            allow_accepted=True,
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
            "invitation_status": invitation.get("status"),
            "already_accepted": already_accepted,
        }

    @staticmethod
    def _normalize_login_phone(phone_isd_code: str, phone_number: str) -> str:
        """Normalize phone for Supabase sign-in (digits only, matches contact provisioning)."""
        combined = f"{phone_isd_code or ''}{phone_number or ''}".strip()
        return "".join(ch for ch in combined if ch.isdigit())

    async def _update_member_password(self, *, user_id: str, password: str) -> None:
        """Set the Supabase auth password for a provisioned household member."""
        if not self.supabase_client:
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )
        await update_password_by_user_id(
            user_id=user_id,
            new_password=password,
            sb_client=self.supabase_client,
        )

    async def _sign_in_provisioned_member(
        self,
        *,
        user_id: str,
        phone: str,
        password: str,
    ) -> Any:
        """Sign in the household member with phone + password after auth provisioning."""
        if not self.supabase_anon_client:
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        await self._update_member_password(user_id=user_id, password=password)

        try:
            return await login_user_with_phone(
                phone=phone,
                password=password,
                sb_client=self.supabase_anon_client,
            )
        except AuthApiError as auth_error:
            if auth_error.status == 400:
                raise BadRequestException(
                    message_key="auth.errors.invalid_credentials",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                ) from auth_error
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from auth_error
        except Exception as login_error:
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from login_error

    @staticmethod
    def _build_accept_response(
        *,
        invitation: dict[str, Any],
        contact_id: str,
        org_id: str,
        phone_isd_code: str,
        phone_number: str,
        user_id: str,
        auth_result: Any | None = None,
        contact: dict[str, Any] | None = None,
        already_accepted: bool = False,
        auth_bypassed: bool = False,
    ) -> dict[str, Any]:
        """Build the accept/re-accept API payload."""
        if auth_bypassed:
            return {
                "contact_id": contact_id,
                "organization_id": org_id,
                "contact_unit_id": str(invitation["contact_unit_id"]),
                "member_status": HouseholdMemberStatus.JOINED.value,
                "invitation_status": HouseholdInvitationStatus.ACCEPTED.value,
                "already_accepted": already_accepted,
                "auth_bypassed": True,
                "phone_masked": mask_phone(
                    phone_isd_code=phone_isd_code,
                    phone_number=phone_number,
                ),
                "access_token": None,
                "refresh_token": None,
                "expires_in": None,
                "expires_at": None,
                "user": {
                    "id": user_id or contact_id,
                    "email": (contact or {}).get("email"),
                    "first_name": (contact or {}).get("first_name"),
                    "last_name": (contact or {}).get("last_name"),
                    "phone_number": phone_number,
                    "phone_isd_code": phone_isd_code,
                },
            }

        session = auth_result.session
        user = auth_result.user
        user_metadata = getattr(user, "user_metadata", {}) or {}
        return {
            "contact_id": contact_id,
            "organization_id": org_id,
            "contact_unit_id": str(invitation["contact_unit_id"]),
            "member_status": HouseholdMemberStatus.JOINED.value,
            "invitation_status": HouseholdInvitationStatus.ACCEPTED.value,
            "already_accepted": already_accepted,
            "auth_bypassed": False,
            "phone_masked": mask_phone(
                phone_isd_code=phone_isd_code,
                phone_number=phone_number,
            ),
            "access_token": session.access_token,
            "refresh_token": getattr(session, "refresh_token", None),
            "expires_in": getattr(session, "expires_in", None),
            "expires_at": getattr(session, "expires_at", None),
            "user": {
                "id": getattr(user, "id", user_id),
                "email": getattr(user, "email", None),
                "first_name": user_metadata.get("first_name"),
                "last_name": user_metadata.get("last_name"),
                "phone_number": user_metadata.get("phone_number") or phone_number,
                "phone_isd_code": user_metadata.get("phone_isd_code") or phone_isd_code,
            },
        }

    async def _complete_invitation_acceptance(
        self,
        *,
        org_id: str,
        contact_id: str,
        invitation: dict[str, Any],
        already_accepted: bool,
    ) -> None:
        """Activate the unit link, seed onboarding, and mark the invitation accepted."""
        if already_accepted:
            return
        await self.contact_units_repo.activate_contact_unit(
            organization_id=org_id,
            contact_unit_id=str(invitation["contact_unit_id"]),
        )
        await self.onboarding_repo.ensure_profile_step(
            organization_id=org_id,
            contact_id=contact_id,
        )
        await self.invitations_repo.mark_accepted(invitation_id=str(invitation["id"]))

    async def accept(self, *, token: str, password: str) -> dict[str, Any]:
        """Accept a phone invitation: provision auth, activate unit, seed onboarding, sign in."""
        invitation_row = await self.invitations_repo.get_by_token_hash(
            hash_token(token),
            for_update=True,
        )
        if not invitation_row:
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )

        already_accepted = invitation_row.get("status") == HouseholdInvitationStatus.ACCEPTED.value
        invitation = (
            invitation_row if already_accepted else self._validate_invitation(invitation_row)
        )
        org_id = str(invitation["organization_id"])
        contact_id = str(invitation["contact_id"])
        phone_isd_code = str(invitation["phone_isd_code"])
        phone_number = str(invitation["phone_number"])
        login_phone = self._normalize_login_phone(phone_isd_code, phone_number)

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
        provisioned = await contacts_service.provision_auth_for_existing_contact(
            contact_id=contact_id,
            password=password,
        )
        user_id = str(provisioned.get("user_id") or "")

        auth_bypassed = app_settings.household_invitation_bypass_supabase_auth
        auth_result = None
        if auth_bypassed:
            if user_id:
                await self._update_member_password(user_id=user_id, password=password)
        else:
            if not user_id:
                raise InternalServerErrorException(
                    message_key="contacts.errors.auth_user_creation_failed",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                )
            auth_result = await self._sign_in_provisioned_member(
                user_id=user_id,
                phone=login_phone,
                password=password,
            )
            session = auth_result.session
            if not session or not getattr(session, "access_token", None):
                raise InternalServerErrorException(
                    message_key="auth.errors.authentication_failed",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                )

        await self._complete_invitation_acceptance(
            org_id=org_id,
            contact_id=contact_id,
            invitation=invitation,
            already_accepted=already_accepted,
        )

        return self._build_accept_response(
            invitation=invitation,
            contact_id=contact_id,
            org_id=org_id,
            phone_isd_code=phone_isd_code,
            phone_number=phone_number,
            user_id=user_id,
            auth_result=auth_result,
            contact=provisioned if isinstance(provisioned, dict) else contact,
            already_accepted=already_accepted,
            auth_bypassed=auth_bypassed,
        )

    async def decline(self, *, token: str) -> dict[str, Any]:
        """Decline a phone invitation: mark declined, remove link, delete orphan contact."""
        invitation = await self.invitations_repo.get_by_token_hash(
            hash_token(token),
            for_update=True,
        )
        if not invitation:
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )

        status = invitation.get("status")
        org_id = str(invitation["organization_id"])
        contact_id = str(invitation["contact_id"])
        contact_unit_id = str(invitation["contact_unit_id"])

        if status == HouseholdInvitationStatus.DECLINED.value:
            return {
                "contact_id": contact_id,
                "organization_id": org_id,
                "contact_unit_id": contact_unit_id,
                "invitation_status": HouseholdInvitationStatus.DECLINED.value,
                "contact_deleted": False,
            }

        invitation = self._validate_invitation(invitation)

        declined = await self.invitations_repo.mark_declined(
            invitation_id=str(invitation["id"]),
        )
        if not declined:
            raise GoneException(
                message_key="contact_onboarding.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )

        await self.contact_units_repo.delete_link(
            organization_id=org_id,
            contact_unit_id=contact_unit_id,
        )
        remaining = await self.contact_units_repo.count_links_for_contact(
            organization_id=org_id,
            contact_id=contact_id,
        )
        contact_deleted = False
        if remaining == 0:
            await self.contacts_repo.soft_delete_contact(
                contact_id=contact_id,
                organization_id=org_id,
            )
            contact_deleted = True

        return {
            "contact_id": contact_id,
            "organization_id": org_id,
            "contact_unit_id": contact_unit_id,
            "invitation_status": HouseholdInvitationStatus.DECLINED.value,
            "contact_deleted": contact_deleted,
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
