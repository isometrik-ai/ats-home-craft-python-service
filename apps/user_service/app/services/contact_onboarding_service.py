"""Contact onboarding wizard orchestration."""

from __future__ import annotations

from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    CONTACT_LEVEL_STEP_KEYS,
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_unit_onboarding_repository import (
    UNIT_ONBOARDING_STEP_KEYS,
    ContactUnitOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository
from apps.user_service.app.schemas.contact_onboarding import (
    CompleteProfileRequest,
    CreateHouseholdMemberRequest,
    UpdateHouseholdMemberRequest,
)
from apps.user_service.app.schemas.contacts import (
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactType,
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
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

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
        self.unit_onboarding_repo = ContactUnitOnboardingRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.vehicles_repo = VehiclesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
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

    async def _ensure_onboarding(
        self,
        contact_id: str,
        *,
        contact_type: str | None = None,
    ) -> None:
        """Ensure wizard steps exist for the contact (profile-only for Family)."""
        org_id = self.user_context.organization_id
        assert org_id
        if contact_type is None:
            contact = await self.contacts_repo.get_contact_details(
                contact_id=contact_id,
                organization_id=org_id,
            )
            contact_type = str(contact.get("contact_type") or "") if contact else ""
        if contact_type == ContactType.FAMILY.value:
            await self.onboarding_repo.ensure_profile_step(
                organization_id=org_id,
                contact_id=contact_id,
            )
            return
        await self.onboarding_repo.ensure_steps(
            organization_id=org_id,
            contact_id=contact_id,
        )

    @staticmethod
    def _is_family_contact(contact_type: str | None) -> bool:
        """True when the contact is a household family member."""
        return contact_type == ContactType.FAMILY.value

    def _normalize_step(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a contact_onboarding_steps row to API response shape."""
        return {
            "step_key": row.get("step_key"),
            "status": row.get("status"),
            "completed_at": format_iso_datetime(row.get("completed_at")),
        }

    @staticmethod
    def _step_status(steps: list[dict[str, Any]], step_key: str) -> str:
        """Return the status string for a step key, defaulting to not_started."""
        match = next((row for row in steps if row.get("step_key") == step_key), None)
        return str((match or {}).get("status") or SetupStepStatus.NOT_STARTED.value)

    def _build_unit_onboarding_progress(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Group flat unit step rows into per-unit progress payloads."""
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            contact_unit_id = str(row["contact_unit_id"])
            bucket = grouped.setdefault(
                contact_unit_id,
                {
                    "contact_unit_id": contact_unit_id,
                    "unit_id": str(row.get("unit_id") or ""),
                    "unit_code": row.get("unit_code"),
                    "steps": [],
                },
            )
            bucket["steps"].append(
                {
                    "step_key": row.get("step_key"),
                    "status": row.get("status"),
                    "completed_at": format_iso_datetime(row.get("completed_at")),
                }
            )
        return list(grouped.values())

    def _derive_navigation(
        self,
        *,
        contact_steps: list[dict[str, Any]],
        unit_step_rows: list[dict[str, Any]],
        active_count: int,
        has_default: bool,
    ) -> tuple[str | None, str | None]:
        """Return (current_step, current_contact_unit_id) for the wizard."""
        profile_status = self._step_status(
            contact_steps,
            ContactOnboardingStep.COMPLETE_PROFILE.value,
        )
        if profile_status not in TERMINAL_STEP_STATUSES:
            return ContactOnboardingStep.COMPLETE_PROFILE.value, None

        select_status = self._step_status(
            contact_steps,
            ContactOnboardingStep.SELECT_PROPERTIES.value,
        )
        if select_status not in TERMINAL_STEP_STATUSES:
            return ContactOnboardingStep.SELECT_PROPERTIES.value, None

        grouped = self._build_unit_onboarding_progress(unit_step_rows)
        for unit in grouped:
            contact_unit_id = unit["contact_unit_id"]
            for step_key in UNIT_ONBOARDING_STEP_KEYS:
                status = self._step_status(unit["steps"], step_key)
                if status not in TERMINAL_STEP_STATUSES:
                    return step_key, contact_unit_id

        choose_status = self._step_status(
            contact_steps,
            ContactOnboardingStep.CHOOSE_UNIT.value,
        )
        if active_count > 1 and not has_default and choose_status not in TERMINAL_STEP_STATUSES:
            return ContactOnboardingStep.CHOOSE_UNIT.value, None

        review_status = self._step_status(contact_steps, ContactOnboardingStep.REVIEW.value)
        if review_status not in TERMINAL_STEP_STATUSES:
            return ContactOnboardingStep.REVIEW.value, None
        return None, None

    async def get_status(
        self,
        *,
        contact_id: str,
        contact_type: str | None = None,
    ) -> dict[str, Any]:
        """Return wizard progress and derived current step."""
        org_id = self.user_context.organization_id
        assert org_id
        if contact_type is None:
            contact = await self.contacts_repo.get_contact_details(
                contact_id=contact_id,
                organization_id=org_id,
            )
            contact_type = str(contact.get("contact_type") or "") if contact else ""

        await self._ensure_onboarding(contact_id, contact_type=contact_type)

        if self._is_family_contact(contact_type):
            steps = await self.onboarding_repo.list_profile_step(
                organization_id=org_id,
                contact_id=contact_id,
            )
            normalized = [self._normalize_step(row) for row in steps]
            profile_status = self._step_status(
                steps,
                ContactOnboardingStep.COMPLETE_PROFILE.value,
            )
            is_completed = profile_status in TERMINAL_STEP_STATUSES
            current_step = None if is_completed else ContactOnboardingStep.COMPLETE_PROFILE.value
            return {
                "setup_current_step": current_step,
                "current_contact_unit_id": None,
                "is_completed": is_completed,
                "steps": normalized,
                "unit_onboarding": [],
            }

        steps = await self.onboarding_repo.list_steps(
            organization_id=org_id,
            contact_id=contact_id,
        )
        normalized = [self._normalize_step(row) for row in steps]
        unit_step_rows = await self.unit_onboarding_repo.list_steps_for_contact(
            organization_id=org_id,
            contact_id=contact_id,
        )
        unit_onboarding = self._build_unit_onboarding_progress(unit_step_rows)
        active_count = await self.contact_units_repo.count_active_units(
            organization_id=org_id,
            contact_id=contact_id,
        )
        has_default = await self.contact_units_repo.has_default_login(
            organization_id=org_id,
            contact_id=contact_id,
        )
        contact_completed = await self.onboarding_repo.is_wizard_completed(
            organization_id=org_id,
            contact_id=contact_id,
        )
        units_completed = await self.unit_onboarding_repo.all_unit_steps_terminal(
            organization_id=org_id,
            contact_id=contact_id,
        )
        is_completed = contact_completed and units_completed and active_count > 0
        current_step, current_contact_unit_id = (
            (None, None)
            if is_completed
            else self._derive_navigation(
                contact_steps=steps,
                unit_step_rows=unit_step_rows,
                active_count=active_count,
                has_default=has_default,
            )
        )
        return {
            "setup_current_step": current_step,
            "current_contact_unit_id": current_contact_unit_id,
            "is_completed": is_completed,
            "steps": normalized,
            "unit_onboarding": unit_onboarding,
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
    def _to_update_contact_request(body: CompleteProfileRequest) -> UpdateContactRequest:
        """Map onboarding profile payload to the shared contact update request."""
        return UpdateContactRequest(
            prefix=body.prefix,
            first_name=body.first_name,
            last_name=body.last_name,
            date_of_birth=body.date_of_birth,
            profile_photo_url=body.profile_photo_url,
            gender=body.gender,
            blood_group=body.blood_group,
            communication_preferences=body.communication_preferences,
            phones=body.phones,
            emails=body.emails,
        )

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
        await contacts_service.update_contact(
            contact_id=contact_id,
            body=self._to_update_contact_request(body),
        )
        profile = await contacts_service.get_contact_details(contact_id=contact_id)

        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.COMPLETE_PROFILE.value,
        )
        return profile

    @staticmethod
    def _format_household_member(row: dict[str, Any]) -> dict[str, Any]:
        """Map a household member query row to API response shape."""
        portal_access = bool(row.get("portal_access", False))
        unit_link_status = str(row.get("unit_link_status") or ContactUnitStatus.ACTIVE.value)
        member_status = HouseholdInvitationService.derive_member_status(
            portal_access=portal_access,
            unit_link_status=unit_link_status,
        )
        item: dict[str, Any] = {
            "contact_id": str(row["contact_id"]),
            "contact_unit_id": str(row["contact_unit_id"]),
            "unit_id": str(row["unit_id"]),
            "first_name": row.get("first_name"),
            "last_name": row.get("last_name"),
            "relationship": row.get("relationship"),
            "portal_access": portal_access,
            "member_status": member_status,
            "phones": parse_json_any(row.get("phones"), default=[]),
            "emails": parse_json_any(row.get("emails"), default=[]),
        }
        if (
            member_status == HouseholdMemberStatus.INVITED.value
            and row.get("invitation_status") == "pending"
            and row.get("invitation_token")
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
    ) -> list[dict[str, Any]]:
        """List family contacts linked to the primary contact's units."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.contact_units_repo.list_household_by_primary(
            organization_id=org_id,
            primary_contact_id=contact_id,
            unit_id=unit_id,
        )
        return [self._format_household_member(row) for row in rows]

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
            if member_row.get("user_id"):
                raise ValidationException(
                    message_key="contact_onboarding.errors.household_portal_access_already_enabled",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
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

        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=primary_contact_id,
            unit_id=body.unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_not_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
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
                contact_type=ContactType.FAMILY,
                portal_access=body.portal_access,
                first_name=body.first_name,
                last_name=body.last_name,
                gender=body.gender,
                phones=body.phones,
                emails=body.emails or [],
            ),
            provision_auth=not body.portal_access,
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
            "contact": create_result["new_data"],
        }

    async def resend_household_invitation(
        self,
        *,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Resend a pending portal invitation for a household member."""
        primary_contact = await ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        ).get_contact_details(contact_id=primary_contact_id)
        return await self.household_invitation_service.resend(
            primary_contact_id=primary_contact_id,
            contact_unit_id=contact_unit_id,
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
            await self.contacts_repo.soft_delete_contact(
                contact_id=family_contact_id,
                organization_id=org_id,
            )
        return {
            "contact_unit_id": contact_unit_id,
            "contact_id": family_contact_id,
            "contact_deleted": remaining == 0,
        }

    async def complete_household_step(
        self,
        *,
        contact_id: str,
        contact_unit_id: str,
    ) -> None:
        """Mark the household onboarding step complete for one unit."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._assert_owned_contact_unit(
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
        )
        await self.unit_onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
            step_key=ContactOnboardingStep.HOUSEHOLD.value,
        )

    async def _assert_owned_contact_unit(
        self,
        *,
        contact_id: str,
        contact_unit_id: str,
    ) -> None:
        """Ensure the contact_unit belongs to the onboarding contact."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.contact_units_repo.get_owned_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.contact_unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def skip_step(
        self,
        *,
        contact_id: str,
        step_key: str,
        contact_unit_id: str | None = None,
    ) -> None:
        """Skip an optional unit-level onboarding step (vehicles or household)."""
        org_id = self.user_context.organization_id
        assert org_id
        allowed_skip = {
            ContactOnboardingStep.VEHICLES.value,
            ContactOnboardingStep.HOUSEHOLD.value,
        }
        if step_key not in allowed_skip:
            raise ValidationException(
                message_key="contact_onboarding.errors.step_not_skippable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not contact_unit_id:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_step_requires_contact_unit",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self._assert_owned_contact_unit(
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
        )
        await self.unit_onboarding_repo.skip_step(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
            step_key=step_key,
        )

    async def get_review(self, *, contact_id: str) -> dict[str, Any]:
        """Aggregate contact, units, vehicles, household, and step status."""
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
        household = await self.list_household(contact_id=contact_id)
        status = await self.get_status(contact_id=contact_id)
        return {
            "contact": contact,
            "units": units,
            "vehicles": vehicles,
            "household": household,
            "steps": status["steps"],
            "unit_onboarding": status["unit_onboarding"],
        }

    async def complete_onboarding(self, *, contact_id: str) -> dict[str, Any]:
        """Validate prerequisites, activate units, and complete the review step."""
        org_id = self.user_context.organization_id
        assert org_id
        status = await self.get_status(contact_id=contact_id)
        if status["is_completed"]:
            raise ConflictException(
                message_key="contact_onboarding.errors.already_completed",
                custom_code=CustomStatusCode.CONFLICT,
            )

        active_count = await self.contact_units_repo.count_active_units(
            organization_id=org_id,
            contact_id=contact_id,
        )
        if active_count == 0:
            raise ValidationException(
                message_key="contact_onboarding.errors.no_active_units",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if active_count > 1:
            has_default = await self.contact_units_repo.has_default_login(
                organization_id=org_id,
                contact_id=contact_id,
            )
            if not has_default:
                raise ValidationException(
                    message_key="contact_onboarding.errors.no_default_unit",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

        for step_key in CONTACT_LEVEL_STEP_KEYS:
            if step_key == ContactOnboardingStep.REVIEW.value:
                continue
            step = next((s for s in status["steps"] if s["step_key"] == step_key), None)
            if not step or step["status"] not in TERMINAL_STEP_STATUSES:
                raise ValidationException(
                    message_key="contact_onboarding.errors.step_prerequisite",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"step_key": step_key},
                )

        if not await self.unit_onboarding_repo.all_unit_steps_terminal(
            organization_id=org_id,
            contact_id=contact_id,
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_steps_incomplete",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        await self.contact_units_repo.activate_for_contact(
            organization_id=org_id,
            contact_id=contact_id,
        )
        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.REVIEW.value,
        )
        return await self.get_status(contact_id=contact_id)
