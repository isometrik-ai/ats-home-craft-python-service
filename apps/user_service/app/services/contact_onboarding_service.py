"""Contact onboarding wizard orchestration."""

from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg
from supabase import AsyncClient, ClientOptions, create_async_client
from supabase_auth.errors import AuthApiError, AuthSessionMissingError

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    CONTACT_LEVEL_STEP_KEYS,
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_roles_repository import (
    ContactRolesRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.daily_help_repository import (
    DailyHelpRepository,
)
from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.pets_repository import PetsRepository
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository
from apps.user_service.app.schemas.common import Phone
from apps.user_service.app.schemas.contact_onboarding import (
    CompleteOnboardingRequest,
    CompleteProfileRequest,
    ConfirmPhoneChangeRequest,
    CreateHouseholdMemberRequest,
    HouseholdSummaryCountsResponse,
    SendPhoneChangeRequest,
    UpdateHouseholdMemberRequest,
)
from apps.user_service.app.schemas.contacts import (
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactUnitRelationship,
    ContactUnitStatus,
    HouseholdInvitationStatus,
    HouseholdMemberStatus,
    SetupStepStatus,
)
from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.household_invitation_service import (
    HouseholdInvitationService,
)
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_any,
)
from apps.user_service.app.utils.contact_session_utils import (
    revoke_contact_portal_sessions,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_db.supabase_db.auth_repository import get_user_by_id, update_phone
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    UnauthorizedException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("contact-onboarding-service")

TERMINAL_STEP_STATUSES = {
    SetupStepStatus.COMPLETED.value,
    SetupStepStatus.SKIPPED.value,
}


class ContactOnboardingService:
    """Orchestrates the contact onboarding wizard."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.onboarding_repo = ContactOnboardingRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.vehicles_repo = VehiclesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.contact_roles_repo = ContactRolesRepository(db_connection)
        self.daily_help_repo = DailyHelpRepository(db_connection)
        self.pets_repo = PetsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.contact_units_service = ContactUnitsService(
            db_connection=db_connection,
            user_context=user_context,
        )
        self.vehicles_service = VehiclesService(
            db_connection=db_connection,
            user_context=user_context,
        )
        self.household_invitation_service = HouseholdInvitationService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=supabase_client,
        )

    async def _ensure_onboarding(self, contact_id: str) -> None:
        """Ensure wizard steps exist (profile-only for household-only contacts)."""
        org_id = self.user_context.organization_id
        assert org_id
        if await self._is_household_only_contact(contact_id):
            await self.onboarding_repo.ensure_profile_step(
                organization_id=org_id,
                contact_id=contact_id,
            )
            return
        await self.onboarding_repo.ensure_steps(
            organization_id=org_id,
            contact_id=contact_id,
        )

    async def _is_household_only_contact(self, contact_id: str) -> bool:
        """True when every open unit link is a household member (relationship != self)."""
        org_id = self.user_context.organization_id
        assert org_id
        links = await self.contact_units_repo.list_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            statuses=[
                ContactUnitStatus.ACTIVE.value,
                ContactUnitStatus.PENDING.value,
            ],
        )
        if not links:
            return False
        return all(
            str(link.get("relationship") or "") != ContactUnitRelationship.SELF.value
            for link in links
        )

    async def _is_profile_complete(self, *, contact_id: str) -> bool:
        """True when the contact has completed or skipped the profile step."""
        org_id = self.user_context.organization_id
        assert org_id
        steps = await self.onboarding_repo.list_profile_step(
            organization_id=org_id,
            contact_id=contact_id,
        )
        profile_status = self._step_status(
            steps,
            ContactOnboardingStep.COMPLETE_PROFILE.value,
        )
        return profile_status in TERMINAL_STEP_STATUSES

    async def _is_review_complete(self, *, contact_id: str) -> bool:
        """True when the contact finalized onboarding via POST /complete."""
        org_id = self.user_context.organization_id
        assert org_id
        steps = await self.onboarding_repo.list_steps(
            organization_id=org_id,
            contact_id=contact_id,
        )
        review_status = self._step_status(steps, ContactOnboardingStep.REVIEW.value)
        return review_status in TERMINAL_STEP_STATUSES

    @staticmethod
    def _resolve_is_completed(
        *,
        profile_complete: bool,
        active_count: int,
        pending_self_unit_count: int,
        household_only: bool,
        review_complete: bool,
    ) -> bool:
        """Derive onboarding completion from wizard state, not empty prompts alone."""
        if household_only or (active_count == 0 and pending_self_unit_count == 0):
            return profile_complete
        if active_count > 0:
            return review_complete
        return False

    async def _list_pending_self_units(self, *, contact_id: str) -> list[dict[str, Any]]:
        """Pending allotments where the contact is the primary occupant (relationship=self)."""
        org_id = self.user_context.organization_id
        assert org_id
        links = await self.contact_units_repo.list_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            statuses=[ContactUnitStatus.PENDING.value],
        )
        return [
            link
            for link in links
            if str(link.get("relationship") or "") == ContactUnitRelationship.SELF.value
        ]

    def _build_onboarding_prompts(
        self,
        *,
        profile_complete: bool,
        pending_self_units: list[dict[str, Any]],
        requires_default_unit: bool,
        active_count: int,
        household_only: bool,
        review_complete: bool,
    ) -> list[dict[str, Any]]:
        """Build optional home-screen prompts (nothing here blocks app usage)."""
        prompts: list[dict[str, Any]] = []
        if not profile_complete:
            prompts.append({"type": "complete_profile"})
        for link in pending_self_units:
            prompts.append(
                {
                    "type": "accept_unit",
                    "contact_unit_id": str(link["id"]),
                    "unit_id": str(link.get("unit_id") or ""),
                }
            )
        if requires_default_unit:
            prompts.append({"type": "choose_default_unit"})
        ready_for_onboarding_completion = (
            not household_only
            and active_count > 0
            and profile_complete
            and not pending_self_units
            and not requires_default_unit
            and not review_complete
        )
        if ready_for_onboarding_completion:
            prompts.append({"type": "complete_onboarding"})
        return prompts

    async def _auto_complete_legacy_wizard_steps(self, *, contact_id: str) -> None:
        """Mark legacy wizard steps completed so older integrations stay green."""
        org_id = self.user_context.organization_id
        assert org_id
        for step_key in CONTACT_LEVEL_STEP_KEYS:
            await self.onboarding_repo.complete_step(
                organization_id=org_id,
                contact_id=contact_id,
                step_key=step_key,
            )

    @staticmethod
    def _step_status(steps: list[dict[str, Any]], step_key: str) -> str:
        """Return the status string for a step key, defaulting to not_started."""
        match = next((row for row in steps if row.get("step_key") == step_key), None)
        return str((match or {}).get("status") or SetupStepStatus.NOT_STARTED.value)

    async def get_status(
        self,
        *,
        contact_id: str,
        contact_type: str | None = None,  # — kept for API compatibility
    ) -> dict[str, Any]:
        """Return optional onboarding prompts for the contact."""
        if contact_type is not None:
            pass  # Reserved for API compatibility; prompt logic is contact-driven.
        org_id = self.user_context.organization_id
        assert org_id

        await self._ensure_onboarding(contact_id)

        profile_complete = await self._is_profile_complete(contact_id=contact_id)
        pending_self_units = await self._list_pending_self_units(contact_id=contact_id)
        active_count = await self.contact_units_repo.count_active_units(
            organization_id=org_id,
            contact_id=contact_id,
        )
        has_default = await self.contact_units_repo.has_default_login(
            organization_id=org_id,
            contact_id=contact_id,
        )
        requires_default_unit = active_count > 1 and not has_default
        household_only = await self._is_household_only_contact(contact_id)
        review_complete = await self._is_review_complete(contact_id=contact_id)
        requires_default = requires_default_unit and not household_only

        prompts = self._build_onboarding_prompts(
            profile_complete=profile_complete,
            pending_self_units=pending_self_units,
            requires_default_unit=requires_default,
            active_count=active_count,
            household_only=household_only,
            review_complete=review_complete,
        )
        is_completed = self._resolve_is_completed(
            profile_complete=profile_complete,
            active_count=active_count,
            pending_self_unit_count=len(pending_self_units),
            household_only=household_only,
            review_complete=review_complete,
        )

        from apps.user_service.app.services.app_access_service import AppAccessService

        app_access = await AppAccessService(
            self.db_connection, user_context=self.user_context
        ).onboarding_app_access(organization_id=org_id, contact_id=contact_id)
        return {
            "profile_complete": profile_complete,
            "pending_unit_count": len(pending_self_units),
            "active_unit_count": active_count,
            "requires_default_unit": requires_default,
            "prompts": prompts,
            "is_completed": is_completed,
            "app_access": app_access,
        }

    async def get_profile(self, *, contact_id: str) -> dict[str, Any]:
        """Return the contact profile for the onboarding wizard."""
        org_id = self.user_context.organization_id
        assert org_id
        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        )
        return await contacts_service.get_contact_details(contact_id=contact_id)

    @staticmethod
    def _phones_with_existing_primary(
        existing_phones: list[Any],
        submitted_phones: list[Phone],
    ) -> list[Phone]:
        """Keep the stored primary phone and replace only the non-primary numbers."""
        primary_item = next(
            (
                item
                for item in existing_phones
                if isinstance(item, dict) and item.get("is_primary") is True
            ),
            None,
        )
        stored_number = str((primary_item or {}).get("phone_number") or "").strip()
        if not isinstance(primary_item, dict) or not stored_number:
            return list(submitted_phones)

        primary = Phone(
            phone_number=str(primary_item.get("phone_number") or ""),
            phone_isd_code=str(primary_item.get("phone_isd_code") or ""),
            label=primary_item.get("label"),
            is_primary=True,
        )
        primary_key = re.sub(r"\D", "", f"{primary.phone_isd_code}{primary.phone_number}")
        secondary = [
            phone
            for phone in submitted_phones
            if re.sub(r"\D", "", f"{phone.phone_isd_code}{phone.phone_number}") != primary_key
        ]
        return [primary, *secondary]

    @staticmethod
    def _to_update_contact_request(body: CompleteProfileRequest) -> UpdateContactRequest:
        """Map onboarding profile payload to the shared contact update request."""
        return UpdateContactRequest(**body.model_dump(exclude_unset=True))

    async def complete_profile(
        self,
        *,
        contact_id: str,
        body: CompleteProfileRequest,
    ) -> dict[str, Any]:
        """Update contact profile and complete the profile step."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_onboarding(contact_id)

        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        )
        update_body = self._to_update_contact_request(body)
        if body.phones is not None:
            existing_phones = await self.contacts_repo.get_contact_phones_for_update(
                contact_id=contact_id,
                organization_id=org_id,
            )
            update_body.phones = self._phones_with_existing_primary(
                existing_phones or [],
                body.phones,
            )
        await contacts_service.update_contact(
            contact_id=contact_id,
            body=update_body,
            sync_login_phone=False,
        )
        profile = await contacts_service.get_contact_details(contact_id=contact_id)

        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.COMPLETE_PROFILE.value,
        )
        return profile

    async def send_phone_change(
        self,
        *,
        user_id: str,
        body: SendPhoneChangeRequest,
        access_token: str,
    ) -> dict[str, Any]:
        """Text an OTP when the number is free. Skip sending if Auth already has it."""
        phone_isd_code, phone_number, phone_digits, e164 = self._phone_parts(
            phone_isd_code=body.phone_isd_code,
            phone_number=body.phone_number,
        )
        del phone_isd_code, phone_number
        await self._assert_phone_available(user_id=user_id, phone_digits=phone_digits, e164=e164)
        if await self._current_auth_phone_digits(user_id) == phone_digits:
            return {"otp_sent": False, "already_verified": True}

        await self._request_auth_phone_change(access_token=access_token, e164=e164)
        return {"otp_sent": True, "already_verified": False}

    async def confirm_phone_change(
        self,
        *,
        contact_id: str,
        user_id: str,
        body: ConfirmPhoneChangeRequest,
    ) -> dict[str, Any]:
        """Verify the SMS code, then store the number as the contact primary phone.

        A retry whose Auth phone already matches skips the code and only writes the contact.
        """
        phone_isd_code, phone_number, phone_digits, e164 = self._phone_parts(
            phone_isd_code=body.phone_isd_code,
            phone_number=body.phone_number,
        )
        await self._assert_phone_available(user_id=user_id, phone_digits=phone_digits, e164=e164)
        if await self._current_auth_phone_digits(user_id) != phone_digits:
            code = (body.code or "").strip()
            if not code:
                raise BadRequestException(
                    message_key="contact_onboarding.errors.phone_otp_required",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            await self._verify_phone_change_otp(e164=e164, code=code)

        await self._save_contact_primary_phone(
            contact_id=contact_id,
            phone_isd_code=phone_isd_code,
            phone_number=phone_number,
        )
        await self._sync_auth_phone_metadata(
            user_id=user_id,
            phone_isd_code=phone_isd_code,
            phone_number=phone_number,
        )
        return {"phone_isd_code": phone_isd_code, "phone_number": phone_number}

    @staticmethod
    def _phone_parts(*, phone_isd_code: str, phone_number: str) -> tuple[str, str, str, str]:
        """Return stored ISD, national number, digits-only full number, and E.164."""
        isd_digits = re.sub(r"\D", "", phone_isd_code or "")
        number_digits = re.sub(r"\D", "", phone_number or "")
        if not isd_digits or not number_digits:
            raise BadRequestException(
                message_key="contact_onboarding.errors.phone_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )
        phone_digits = f"{isd_digits}{number_digits}"
        return f"+{isd_digits}", number_digits, phone_digits, f"+{phone_digits}"

    async def _assert_phone_available(self, *, user_id: str, phone_digits: str, e164: str) -> None:
        """Reject a number that already belongs to a different auth user."""
        user_repo = UserRepository(self.db_connection)
        for candidate in (phone_digits, e164):
            existing = await user_repo.get_auth_user_by_phone(candidate)
            if existing and str(existing["id"]) != user_id:
                raise ConflictException(
                    message_key="clients.errors.phone_number_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                )

    async def _require_supabase(self) -> AsyncClient:
        """Return the service-role client used for auth user reads and metadata writes."""
        if self.supabase_client is None:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        return self.supabase_client

    async def _current_auth_phone_digits(self, user_id: str) -> str:
        """Return the digits-only phone currently stored on the auth user."""
        client = await self._require_supabase()
        auth_user = await get_user_by_id(client, user_id)
        if not auth_user:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        return re.sub(r"\D", "", str(auth_user.get("phone") or ""))

    async def _anon_auth_client(self) -> AsyncClient:
        """Return a fresh anon client so this request does not change the shared one."""
        return await create_async_client(
            shared_settings.supabase.url,
            shared_settings.supabase.anon_key,
            options=ClientOptions(persist_session=False, auto_refresh_token=False),
        )

    async def _request_auth_phone_change(self, *, access_token: str, e164: str) -> None:
        """Ask Supabase Auth to send the phone-change OTP through the SMS hook."""
        if not access_token:
            raise UnauthorizedException(
                message_key="errors.authentication_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        client = await self._anon_auth_client()
        try:
            await client.auth.set_session(access_token, "")
            await client.auth.update_user({"phone": e164})
        except AuthApiError as exc:
            logger.error("Phone change rejected: code=%s", exc.code)
            if exc.code in {"phone_exists", "user_already_exists"}:
                raise ConflictException(
                    message_key="clients.errors.phone_number_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise BadRequestException(
                message_key="contact_onboarding.errors.phone_change_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from exc
        except AuthSessionMissingError as exc:
            raise UnauthorizedException(
                message_key="errors.authentication_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from exc

    async def _verify_phone_change_otp(self, *, e164: str, code: str) -> None:
        """Confirm the phone-change OTP with Supabase Auth."""
        anon_client = await self._anon_auth_client()
        try:
            await anon_client.auth.verify_otp(
                {"phone": e164, "token": code, "type": "phone_change"}
            )
        except AuthApiError as exc:
            logger.error("Phone change OTP rejected: %s", exc.code)
            raise BadRequestException(
                message_key="contact_onboarding.errors.phone_otp_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from exc

    async def _save_contact_primary_phone(
        self,
        *,
        contact_id: str,
        phone_isd_code: str,
        phone_number: str,
    ) -> None:
        """Replace the contact's primary phone, keeping other numbers on the list."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.contacts_repo.get_contact_phones_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if existing is None:
            raise NotFoundException(
                message_key="contact_onboarding.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        phones = [dict(item) for item in existing if isinstance(item, dict)]
        primary = next((item for item in phones if item.get("is_primary") is True), None)
        if primary is None and phones:
            primary = phones[0]
        if primary is None:
            phones.append(
                {
                    "id": str(uuid.uuid4()),
                    "phone_isd_code": phone_isd_code,
                    "phone_number": phone_number,
                    "is_primary": True,
                }
            )
        else:
            for item in phones:
                item["is_primary"] = item is primary
            primary["phone_isd_code"] = phone_isd_code
            primary["phone_number"] = phone_number
            primary["is_primary"] = True

        updated = await self.contacts_repo.update_contact(
            contact_id=contact_id,
            organization_id=org_id,
            update_data={"phones": phones},
        )
        if not updated:
            raise NotFoundException(
                message_key="contact_onboarding.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def _sync_auth_phone_metadata(
        self,
        *,
        user_id: str,
        phone_isd_code: str,
        phone_number: str,
    ) -> None:
        """Store the phone on auth metadata and organization membership."""
        client = await self._require_supabase()
        auth_user = await get_user_by_id(client, user_id)
        updated = await update_phone(
            client,
            user_id,
            auth_user.get("user_metadata") or {},
            phone_number,
            phone_isd_code,
        )
        if not updated:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        await OrganizationMemberRepository(self.db_connection).update_user_phone_by_user_id(
            user_id,
            phone_number,
            phone_isd_code,
        )

    async def get_review(self, *, contact_id: str) -> dict[str, Any]:
        """Aggregate contact, units, vehicles, and household for the review screen."""
        org_id = self.user_context.organization_id
        assert org_id
        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        )
        contact = await contacts_service.get_contact_details(contact_id=contact_id)
        units = await self.contact_units_service.list_my_properties(contact_id=contact_id)
        vehicles = await self.vehicles_service.list_vehicles(contact_id=contact_id)
        household = await self.list_household(
            contact_id=contact_id,
            include_caller_family_links=True,
        )
        return {
            "contact": contact,
            "units": units,
            "vehicles": vehicles,
            "household": household,
        }

    async def complete_onboarding(
        self,
        *,
        contact_id: str,
        body: CompleteOnboardingRequest | None = None,
    ) -> dict[str, Any]:
        """Finalize onboarding and activate confirmed properties."""
        org_id = self.user_context.organization_id
        assert org_id
        payload = body or CompleteOnboardingRequest()

        active_unit_ids = await self.contact_units_repo.list_active_contact_unit_ids(
            organization_id=org_id,
            contact_id=contact_id,
        )
        if not active_unit_ids:
            raise ValidationException(
                message_key="contact_onboarding.errors.no_active_units",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        status = await self.get_status(contact_id=contact_id)
        if status["is_completed"]:
            raise ConflictException(
                message_key="contact_onboarding.errors.already_completed",
                custom_code=CustomStatusCode.CONFLICT,
            )

        if payload.contact_unit_ids is None:
            completing_unit_ids = active_unit_ids
            defer_unit_ids: list[str] = []
        else:
            completing_unit_ids = list(dict.fromkeys(payload.contact_unit_ids))
            invalid_ids = set(completing_unit_ids) - set(active_unit_ids)
            if invalid_ids:
                raise ValidationException(
                    message_key="contact_onboarding.errors.partial_complete_units_not_active",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            defer_unit_ids = [
                unit_id for unit_id in active_unit_ids if unit_id not in completing_unit_ids
            ]

        if not completing_unit_ids:
            raise ValidationException(
                message_key="contact_onboarding.errors.no_active_units",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        default_unit_id = await self.contact_units_repo.get_default_login_contact_unit_id(
            organization_id=org_id,
            contact_id=contact_id,
        )
        if not default_unit_id or default_unit_id not in completing_unit_ids:
            await self.contact_units_repo.set_default_login(
                organization_id=org_id,
                contact_id=contact_id,
                contact_unit_id=completing_unit_ids[0],
            )

        if defer_unit_ids:
            await self.contact_units_repo.defer_active_units_to_pending(
                organization_id=org_id,
                contact_id=contact_id,
                contact_unit_ids=defer_unit_ids,
            )

        await self.contact_units_repo.activate_units_by_ids(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=completing_unit_ids,
        )
        await self._auto_complete_legacy_wizard_steps(contact_id=contact_id)

        result = await self.get_status(contact_id=contact_id)
        result["completed_contact_unit_ids"] = completing_unit_ids
        if defer_unit_ids:
            result["deferred_contact_unit_ids"] = defer_unit_ids
        return result

    _REISSUABLE_HOUSEHOLD_INVITATION_STATUSES = frozenset(
        {
            HouseholdInvitationStatus.CANCELLED.value,
            HouseholdInvitationStatus.EXPIRED.value,
            HouseholdInvitationStatus.DECLINED.value,
        }
    )

    @staticmethod
    def _can_resend_household_invitation(
        *,
        invitation_status: str | None,
    ) -> bool:
        """True when the primary can resend SMS or re-issue after revoke/decline."""
        if invitation_status == HouseholdInvitationStatus.PENDING.value:
            return True
        if invitation_status in ContactOnboardingService._REISSUABLE_HOUSEHOLD_INVITATION_STATUSES:
            return True
        return False

    @staticmethod
    def _format_household_member(row: dict[str, Any]) -> dict[str, Any]:
        """Map a household member query row to API response shape."""
        portal_access = bool(row.get("portal_access", False))
        unit_link_status = str(row.get("unit_link_status") or ContactUnitStatus.ACTIVE.value)
        invitation_status = row.get("invitation_status")
        has_user = bool(row.get("user_id"))
        member_status = HouseholdInvitationService.derive_member_status(
            portal_access=portal_access,
            unit_link_status=unit_link_status,
            invitation_status=invitation_status,
            has_user=has_user,
        )
        item: dict[str, Any] = {
            "contact_id": str(row["contact_id"]),
            "contact_unit_id": str(row["contact_unit_id"]),
            "unit_id": str(row["unit_id"]),
            "first_name": row.get("first_name"),
            "last_name": row.get("last_name"),
            "relationship": row.get("relationship"),
            "contact_type": row.get("contact_type"),
            "portal_access": portal_access,
            "member_status": member_status,
            "invitation_status": invitation_status,
            "can_resend_invitation": ContactOnboardingService._can_resend_household_invitation(
                invitation_status=invitation_status,
            ),
            "phones": parse_json_any(row.get("phones"), default=[]),
            "emails": parse_json_any(row.get("emails"), default=[]),
        }
        if invitation_status == HouseholdInvitationStatus.PENDING.value and row.get(
            "invitation_token"
        ):
            item["invite_url"] = HouseholdInvitationService._generate_invite_url(
                str(row["invitation_token"])
            )
            item["invitation_expires_at"] = format_iso_datetime(row.get("invitation_expires_at"))
            item["invitation_sent_at"] = format_iso_datetime(row.get("invitation_sent_at"))
        return item

    async def list_household(
        self,
        *,
        contact_id: str,
        unit_id: str | None = None,
        include_caller_family_links: bool = False,
    ) -> list[dict[str, Any]]:
        """List household-visible contacts on units the caller actively occupies."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.contact_units_repo.list_household_for_contact(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
            include_caller_family_links=include_caller_family_links,
        )
        return [self._format_household_member(row) for row in rows]

    async def _assert_primary_occupant_for_unit(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> None:
        """Ensure the contact is the primary occupant (relationship=self) on the unit."""
        org_id = self.user_context.organization_id
        assert org_id
        if not await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_not_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not await self.contact_units_repo.owner_has_active_unit(
            organization_id=org_id,
            owner_contact_id=contact_id,
            unit_id=unit_id,
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.primary_occupant_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def get_household_summary(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> HouseholdSummaryCountsResponse:
        """Return household dashboard counts for one unit the contact can access."""
        org_id = self.user_context.organization_id
        assert org_id
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_not_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        family_count = await self.contact_units_repo.count_family_members_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        daily_help_count = await self.daily_help_repo.count_active_links_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        vehicles_count, _ = await self.units_repo.count_unit_vehicles(
            organization_id=org_id,
            unit_id=unit_id,
        )
        tenant_count = await self.contact_roles_repo.count_active_tenants_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        pets_count = await self.pets_repo.count_active_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        return HouseholdSummaryCountsResponse(
            unit_id=unit_id,
            family_count=family_count,
            daily_help_count=daily_help_count,
            vehicles_count=vehicles_count,
            tenant_count=tenant_count,
            pets_count=pets_count,
        )

    async def _load_household_member(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Load one household member or raise not found."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.contact_units_repo.get_household_member(
            organization_id=org_id,
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.household_member_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    @staticmethod
    def _primary_phone_from_contact(contact: dict[str, Any]) -> dict[str, Any] | None:
        """Return the primary phone dict from a contact row."""
        phones = parse_json_any(contact.get("phones"), default=[])
        primary = next((phone for phone in phones if phone.get("is_primary")), None)
        return primary or (phones[0] if phones else None)

    async def _apply_household_portal_access_change(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
        family_contact_id: str,
        member_row: dict[str, Any],
        portal_access: bool,
    ) -> None:
        """Enable or disable portal access for an existing household member."""
        org_id = self.user_context.organization_id
        assert org_id
        current_portal_access = bool(member_row.get("portal_access", False))
        if portal_access == current_portal_access:
            return

        if portal_access:
            invitations_repo = self.household_invitation_service.invitations_repo
            pending_invitation = await invitations_repo.get_pending_by_contact_unit(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
            )
            if pending_invitation:
                raise ValidationException(
                    message_key="contact_onboarding.errors.household_portal_access_invite_pending",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

            if member_row.get("user_id"):
                # Member already has Supabase auth (e.g. created with portal_access=false).
                # Flip the flag only — no SMS invite or pending unit link.
                await self.contacts_repo.update_contact(
                    contact_id=family_contact_id,
                    organization_id=org_id,
                    update_data={"portal_access": True},
                )
                return

            contact = await self.contacts_repo.get_contact_details(
                contact_id=family_contact_id,
                organization_id=org_id,
            )
            if not contact:
                raise NotFoundException(
                    message_key="contacts.errors.contact_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            primary_phone = self._primary_phone_from_contact(contact)
            if not primary_phone:
                raise ValidationException(
                    message_key="contact_onboarding.errors.household_portal_access_requires_phone",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

            await self.contacts_repo.update_contact(
                contact_id=family_contact_id,
                organization_id=org_id,
                update_data={"portal_access": True},
            )
            await self.contact_units_repo.update_household_link_status(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
                status=ContactUnitStatus.PENDING.value,
            )
            primary_contact = await ContactsService(
                db_connection=self.db_connection,
                user_context=self.user_context,
                supabase_client=self.supabase_client,
            ).get_contact_details(contact_id=primary_contact_id)
            await self.household_invitation_service.create_and_send(
                primary_contact_id=primary_contact_id,
                family_contact_id=family_contact_id,
                contact_unit_id=contact_unit_id,
                phone_isd_code=str(primary_phone["phone_isd_code"]),
                phone_number=str(primary_phone["phone_number"]),
                invitee_first_name=contact.get("first_name"),
                invitee_last_name=contact.get("last_name"),
                inviter_first_name=primary_contact.get("first_name"),
                inviter_last_name=primary_contact.get("last_name"),
            )
            return

        await self.household_invitation_service.cancel_for_contact_unit(
            organization_id=org_id,
            contact_unit_id=contact_unit_id,
        )
        await self.contacts_repo.update_contact(
            contact_id=family_contact_id,
            organization_id=org_id,
            update_data={"portal_access": False},
        )
        if str(member_row.get("unit_link_status")) == ContactUnitStatus.PENDING.value:
            await self.contact_units_repo.update_household_link_status(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
                status=ContactUnitStatus.ACTIVE.value,
            )

    async def update_household_member(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
        body: UpdateHouseholdMemberRequest,
    ) -> dict[str, Any]:
        """Patch a household member's contact details, relationship, or portal access."""
        org_id = self.user_context.organization_id
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

        member_row = await self._load_household_member(
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        family_contact_id = str(link["contact_id"])
        contact_update: dict[str, Any] = {}
        if body.first_name is not None:
            contact_update["first_name"] = body.first_name
        if body.last_name is not None:
            contact_update["last_name"] = body.last_name

        if contact_update:
            updated = await self.contacts_repo.update_contact(
                contact_id=family_contact_id,
                organization_id=org_id,
                update_data=contact_update,
            )
            if not updated:
                raise NotFoundException(
                    message_key="contacts.errors.contact_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        if body.relationship is not None:
            await self.contact_units_repo.update_household_relationship(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
                relationship=body.relationship.value,
            )

        if body.portal_access is not None:
            await self._apply_household_portal_access_change(
                primary_contact_id=primary_contact_id,
                contact_unit_id=contact_unit_id,
                family_contact_id=family_contact_id,
                member_row=member_row,
                portal_access=body.portal_access,
            )

        row = await self._load_household_member(
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        return self._format_household_member(row)

    async def add_household_member(
        self,
        *,
        primary_contact_id: str,
        body: CreateHouseholdMemberRequest,
    ) -> dict[str, Any]:
        """Create a family contact and link them to a unit."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_onboarding(primary_contact_id)

        await self._assert_primary_occupant_for_unit(
            contact_id=primary_contact_id,
            unit_id=body.unit_id,
        )
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=body.unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        )
        primary_contact = await contacts_service.get_contact_details(contact_id=primary_contact_id)

        create_result = await contacts_service.create_contact(
            CreateContactRequest(
                portal_access=body.portal_access,
                first_name=body.first_name,
                last_name=body.last_name,
                gender=body.gender,
                phones=body.phones,
                emails=body.emails or [],
            ),
        )
        family_contact_id = create_result["contact_id"]
        link_status = (
            ContactUnitStatus.PENDING.value
            if body.portal_access
            else ContactUnitStatus.ACTIVE.value
        )
        link = await self.contact_units_repo.insert_household_link(
            organization_id=org_id,
            project_id=unit["project_id"],
            unit_id=body.unit_id,
            contact_id=family_contact_id,
            relationship=body.relationship.value,
            status=link_status,
        )
        await self.contact_roles_repo.insert_family_role(
            organization_id=org_id,
            contact_id=family_contact_id,
            project_id=str(unit["project_id"]),
            unit_id=body.unit_id,
            contact_unit_id=str(link["id"]),
            relationship=body.relationship.value,
        )

        member_status = HouseholdMemberStatus.JOINED.value
        invitation_data: dict[str, Any] | None = None
        if body.portal_access:
            primary_phone = next(
                (phone for phone in body.phones if phone.is_primary),
                body.phones[0],
            )
            invitation_data = await self.household_invitation_service.create_and_send(
                primary_contact_id=primary_contact_id,
                family_contact_id=family_contact_id,
                contact_unit_id=link["id"],
                phone_isd_code=primary_phone.phone_isd_code,
                phone_number=primary_phone.phone_number,
                invitee_first_name=body.first_name,
                invitee_last_name=body.last_name,
                inviter_first_name=primary_contact.get("first_name"),
                inviter_last_name=primary_contact.get("last_name"),
            )
            member_status = invitation_data["member_status"]

        return {
            "contact_id": family_contact_id,
            "contact_unit_id": link["id"],
            "member_status": member_status,
            "invitation_id": invitation_data.get("invitation_id") if invitation_data else None,
            "phone_masked": invitation_data.get("phone_masked") if invitation_data else None,
            "invite_url": invitation_data.get("invite_url") if invitation_data else None,
            # "contact": create_result["new_data"],
        }

    async def resend_household_invitation(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Resend a pending invite, or create a fresh one after revoke."""
        org_id = self.user_context.organization_id
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

        primary_contact = await ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        ).get_contact_details(contact_id=primary_contact_id)

        invitations_repo = self.household_invitation_service.invitations_repo
        pending_invitation = await invitations_repo.get_pending_by_contact_unit(
            organization_id=org_id,
            contact_unit_id=contact_unit_id,
        )
        if pending_invitation:
            return await self.household_invitation_service.resend(
                primary_contact_id=primary_contact_id,
                contact_unit_id=contact_unit_id,
                inviter_first_name=primary_contact.get("first_name"),
                inviter_last_name=primary_contact.get("last_name"),
            )

        member_row = await self._load_household_member(
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        invitation_status = member_row.get("invitation_status")
        if invitation_status == HouseholdInvitationStatus.ACCEPTED.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.household_portal_access_already_enabled",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if invitation_status not in self._REISSUABLE_HOUSEHOLD_INVITATION_STATUSES:
            raise NotFoundException(
                message_key="contact_onboarding.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        family_contact_id = str(link["contact_id"])
        if not bool(member_row.get("portal_access")):
            await self.contacts_repo.update_contact(
                contact_id=family_contact_id,
                organization_id=org_id,
                update_data={"portal_access": True},
            )

        if (
            not member_row.get("user_id")
            and str(member_row.get("unit_link_status") or ContactUnitStatus.ACTIVE.value)
            == ContactUnitStatus.ACTIVE.value
        ):
            await self.contact_units_repo.update_household_link_status(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
                status=ContactUnitStatus.PENDING.value,
            )

        family_contact = await self.contacts_repo.get_contact_details(
            contact_id=family_contact_id,
            organization_id=org_id,
        )
        if not family_contact:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        primary_phone = self._primary_phone_from_contact(family_contact)
        if not primary_phone:
            raise ValidationException(
                message_key="contact_onboarding.errors.household_portal_access_requires_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        return await self.household_invitation_service.create_and_send(
            primary_contact_id=primary_contact_id,
            family_contact_id=family_contact_id,
            contact_unit_id=contact_unit_id,
            phone_isd_code=str(primary_phone["phone_isd_code"]),
            phone_number=str(primary_phone["phone_number"]),
            invitee_first_name=family_contact.get("first_name"),
            invitee_last_name=family_contact.get("last_name"),
            inviter_first_name=primary_contact.get("first_name"),
            inviter_last_name=primary_contact.get("last_name"),
        )

    async def revoke_household_invitation(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Cancel a pending portal invitation without removing the household member."""
        org_id = self.user_context.organization_id
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

        member_row = await self._load_household_member(
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        pending_invitation = (
            await self.household_invitation_service.invitations_repo.get_pending_by_contact_unit(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
            )
        )
        portal_access = bool(member_row.get("portal_access"))
        unit_link_status = str(member_row.get("unit_link_status") or ContactUnitStatus.ACTIVE.value)

        if not pending_invitation and not (
            portal_access and unit_link_status == ContactUnitStatus.PENDING.value
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.invitation_not_pending",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if portal_access:
            await self._apply_household_portal_access_change(
                primary_contact_id=primary_contact_id,
                contact_unit_id=contact_unit_id,
                family_contact_id=str(link["contact_id"]),
                member_row=member_row,
                portal_access=False,
            )
        elif pending_invitation:
            await self.household_invitation_service.cancel_for_contact_unit(
                organization_id=org_id,
                contact_unit_id=contact_unit_id,
            )

        row = await self._load_household_member(
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
        )
        item = self._format_household_member(row)
        item["invitation_status"] = HouseholdInvitationStatus.CANCELLED.value
        return item

    async def remove_household_member(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Remove a family member's link; delete the contact if now orphaned."""
        org_id = self.user_context.organization_id
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

        family_contact_id = link["contact_id"]
        await self.household_invitation_service.cancel_for_contact_unit(
            organization_id=org_id,
            contact_unit_id=contact_unit_id,
        )
        await self.contact_units_repo.delete_link(
            organization_id=org_id,
            contact_unit_id=contact_unit_id,
        )

        remaining = await self.contact_units_repo.count_links_for_contact(
            organization_id=org_id,
            contact_id=family_contact_id,
        )
        if remaining == 0:
            family_contact = await self.contacts_repo.get_contact_details(
                contact_id=family_contact_id,
                organization_id=org_id,
            )
            await self.contacts_repo.soft_delete_contact(
                contact_id=family_contact_id,
                organization_id=org_id,
            )
            await revoke_contact_portal_sessions(
                db_connection=self.db_connection,
                organization_id=org_id,
                user_id=family_contact.get("user_id") if family_contact else None,
            )
        return {
            "contact_unit_id": contact_unit_id,
            "contact_id": family_contact_id,
            "contact_deleted": remaining == 0,
        }
