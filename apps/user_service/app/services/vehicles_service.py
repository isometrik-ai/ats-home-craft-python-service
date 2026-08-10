"""Vehicle business logic for contact onboarding."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_unit_onboarding_repository import (
    ContactUnitOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository
from apps.user_service.app.schemas.contact_onboarding import (
    CreateVehicleRequest,
    ResubmitVehicleRequest,
    ReviewVehicleRequest,
    UpdateVehicleRequest,
    VehicleResponse,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    VehicleFuelType,
    VehicleStatus,
    VehicleType,
)
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
    unit_label_from_row,
)
from apps.user_service.app.services.units_service import (
    format_contact_display_name,
    format_primary_contact_email,
    format_primary_contact_phone,
    serialize_unit_list_item,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_any,
)
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class VehiclesService:
    """CRUD for contact vehicles."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = VehiclesRepository(db_connection)
        self.parking_slots_repo = ParkingSlotsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)
        self.unit_onboarding_repo = ContactUnitOnboardingRepository(db_connection)
        self._push_dispatcher: PushNotificationDispatcher | None = None

    def _push(self) -> PushNotificationDispatcher:
        if self._push_dispatcher is None:
            self._push_dispatcher = PushNotificationDispatcher(db_connection=self.db_connection)
        return self._push_dispatcher

    def _normalize_vehicle(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a vehicles row to API response shape."""
        out = dict(row)
        for key in (
            "id",
            "organization_id",
            "project_id",
            "contact_id",
            "unit_id",
            "parking_slot_id",
            "approved_by_user_id",
            "rejected_by_user_id",
        ):
            if out.get(key) is not None:
                out[key] = str(out[key])
        photo_paths = out.get("photo_paths") or []
        out["photo_paths"] = list(photo_paths)
        out["created_at"] = format_iso_datetime(out.get("created_at"))
        out["updated_at"] = format_iso_datetime(out.get("updated_at"))
        out["status_updated_at"] = format_iso_datetime(out.get("status_updated_at"))
        return out

    _OWNER_ROW_KEYS = (
        "owner_contact_id",
        "owner_prefix",
        "owner_first_name",
        "owner_last_name",
        "owner_phones",
        "owner_emails",
        "owner_primary_phone",
        "owner_primary_email",
        "owner_profile_photo_url",
    )

    _UNIT_ROW_KEYS = (
        "unit_code",
        "unit_label",
        "unit_status",
        "unit_tower_id",
        "unit_config_id",
        "unit_plot_item_id",
        "unit_sort_order",
        "unit_tower_name",
        "unit_tower_type",
        "unit_floor_display_name",
        "unit_floor_level_number",
        "unit_config_kind",
        "unit_config_display_label",
        "unit_config_name",
        "unit_plot_description",
        "unit_resolved_property_type",
        "unit_resolved_config_kind",
    )

    _PARKING_ROW_KEYS = (
        "parking_slot_row_id",
        "parking_slot_number",
        "parking_slot_status",
        "parking_facility_id",
        "parking_facility_name",
        "parking_facility_location_type",
        "parking_facility_floor_level",
        "parking_facility_wing",
        "parking_facility_tower_id",
    )

    _REVIEWER_ROW_KEYS = (
        "approved_by_salutation",
        "approved_by_first_name",
        "approved_by_last_name",
        "approved_by_email",
        "approved_by_phone_isd_code",
        "approved_by_phone_number",
        "approved_by_avatar_url",
        "rejected_by_salutation",
        "rejected_by_first_name",
        "rejected_by_last_name",
        "rejected_by_email",
        "rejected_by_phone_isd_code",
        "rejected_by_phone_number",
        "rejected_by_avatar_url",
    )

    def _build_unit_owner(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build owner summary for a vehicle's unit."""
        if not row.get("owner_contact_id"):
            return None
        phone = row.get("owner_primary_phone") or format_primary_contact_phone(
            parse_json_any(row.get("owner_phones"), default=[])
        )
        email = row.get("owner_primary_email") or format_primary_contact_email(
            parse_json_any(row.get("owner_emails"), default=[])
        )
        profile_photo_url = row.get("owner_profile_photo_url")
        return {
            "contact_id": str(row["owner_contact_id"]),
            "display_name": format_contact_display_name(
                prefix=row.get("owner_prefix"),
                first_name=row.get("owner_first_name"),
                last_name=row.get("owner_last_name"),
            )
            or None,
            "phone": str(phone).strip() if phone else None,
            "email": str(email).strip() if email else None,
            "profile_photo_url": str(profile_photo_url).strip() if profile_photo_url else None,
        }

    def _build_vehicle_unit(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build unit summary for the vehicle's assigned unit."""
        unit_id = row.get("unit_id")
        if not unit_id:
            return None
        unit_item = serialize_unit_list_item(
            {
                "id": str(unit_id),
                "code": row.get("unit_code") or "",
                "unit_label": row.get("unit_label"),
                "status": row.get("unit_status") or "",
                "sort_order": row.get("unit_sort_order") or 0,
                "tower_id": row.get("unit_tower_id"),
                "config_id": row.get("unit_config_id"),
                "plot_item_id": row.get("unit_plot_item_id"),
                "tower_name": row.get("unit_tower_name"),
                "tower_type": row.get("unit_tower_type"),
                "floor_display_name": row.get("unit_floor_display_name"),
                "floor_level_number": row.get("unit_floor_level_number"),
                "config_kind": row.get("unit_config_kind"),
                "config_display_label": row.get("unit_config_display_label"),
                "config_name": row.get("unit_config_name"),
                "plot_description": row.get("unit_plot_description"),
                "resolved_property_type": row.get("unit_resolved_property_type"),
                "resolved_config_kind": row.get("unit_resolved_config_kind"),
            }
        )
        unit_item.pop("owner", None)
        return unit_item

    def _build_parking_allotment(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build parking slot summary when a slot is assigned to the vehicle."""
        slot_id = row.get("parking_slot_row_id") or row.get("parking_slot_id")
        slot_number = row.get("parking_slot_number")
        if not slot_id or slot_number is None:
            return None
        facility = None
        facility_id = row.get("parking_facility_id")
        if facility_id:
            facility = {
                "id": str(facility_id),
                "name": row.get("parking_facility_name") or "",
                "location_type": row.get("parking_facility_location_type"),
                "floor_level": row.get("parking_facility_floor_level"),
                "wing": row.get("parking_facility_wing"),
                "tower_id": (
                    str(row["parking_facility_tower_id"])
                    if row.get("parking_facility_tower_id")
                    else None
                ),
            }
        return {
            "id": str(slot_id),
            "slot_number": int(slot_number),
            "status": row.get("parking_slot_status") or "assigned",
            "facility": facility,
        }

    def _build_vehicle_reviewer(
        self,
        row: dict[str, Any],
        *,
        kind: str,
    ) -> dict[str, Any] | None:
        """Build org-member summary for the admin who approved or rejected."""
        user_id = row.get(f"{kind}_by_user_id")
        if not user_id:
            return None
        prefix = f"{kind}_by"
        display_name = (
            build_full_name(
                str(row.get(f"{prefix}_salutation") or "").strip(),
                str(row.get(f"{prefix}_first_name") or "").strip(),
                str(row.get(f"{prefix}_last_name") or "").strip(),
            ).strip()
            or None
        )
        isd = str(row.get(f"{prefix}_phone_isd_code") or "").strip()
        number = str(row.get(f"{prefix}_phone_number") or "").strip()
        phone = f"{isd}{number}".strip() or None
        email = str(row.get(f"{prefix}_email") or "").strip() or None
        avatar_url = str(row.get(f"{prefix}_avatar_url") or "").strip() or None
        return {
            "user_id": str(user_id),
            "display_name": display_name,
            "email": email,
            "phone": phone,
            "avatar_url": avatar_url,
        }

    def _serialize_contact_vehicle(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a contact vehicle list row to API shape with parking allotment only."""
        out = self._normalize_vehicle(row)
        out["parking_allotment"] = self._build_parking_allotment(row)
        for key in self._PARKING_ROW_KEYS:
            out.pop(key, None)
        payload = VehicleResponse.model_validate(out).model_dump(mode="json")
        for key in ("unit", "owner", "approved_by", "rejected_by"):
            payload.pop(key, None)
        return payload

    def _serialize_admin_vehicle(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a project vehicle row to the admin list API contract."""
        payload = self._normalize_project_vehicle(row)
        return VehicleResponse.model_validate(payload).model_dump(mode="json")

    def _normalize_project_vehicle(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a project vehicle row to admin list response shape."""
        out = self._normalize_vehicle(row)
        out["owner"] = self._build_unit_owner(row)
        out["unit"] = self._build_vehicle_unit(row)
        out["parking_allotment"] = self._build_parking_allotment(row)
        out["approved_by"] = self._build_vehicle_reviewer(row, kind="approved")
        out["rejected_by"] = self._build_vehicle_reviewer(row, kind="rejected")
        for key in (
            *self._OWNER_ROW_KEYS,
            *self._UNIT_ROW_KEYS,
            *self._PARKING_ROW_KEYS,
            *self._REVIEWER_ROW_KEYS,
        ):
            out.pop(key, None)
        return out

    async def _validate_unit_for_contact(self, *, contact_id: str, unit_id: str) -> str:
        """Ensure the unit is actively assigned to the contact; return project_id."""
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
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit["project_id"]

    async def _assert_parking_entitlement_available(
        self,
        *,
        unit_id: str,
        exclude_vehicle_id: str | None = None,
    ) -> None:
        """Ensure the unit has not reached its configured parking entitlement."""
        org_id = self.user_context.organization_id
        assert org_id
        entitlement = await self.units_repo.get_parking_entitlement_by_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        current = await self.repo.count_entitlement_consuming_by_unit(
            organization_id=org_id,
            unit_id=unit_id,
            exclude_vehicle_id=exclude_vehicle_id,
        )
        if current >= entitlement:
            raise ValidationException(
                message_key="contact_onboarding.errors.parking_entitlement_exceeded",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"parking_entitlement": entitlement},
            )

    async def release_for_move_out(
        self,
        *,
        contact_id: str | None = None,
        unit_id: str | None = None,
    ) -> None:
        """Withdraw pending and soft-remove approved vehicles when occupancy ends."""
        org_id = self.user_context.organization_id
        assert org_id
        if contact_id and unit_id:
            vehicles = await self.repo.list_by_contact(
                organization_id=org_id,
                contact_id=contact_id,
                unit_id=unit_id,
            )
        elif unit_id:
            vehicles = await self.repo.list_by_unit(
                organization_id=org_id,
                unit_id=unit_id,
            )
        elif contact_id:
            vehicles = await self.repo.list_by_contact(
                organization_id=org_id,
                contact_id=contact_id,
            )
        else:
            return

        for vehicle in vehicles:
            vehicle_contact_id = str(vehicle["contact_id"])
            vehicle_id = str(vehicle["id"])
            status = str(vehicle.get("status") or "")
            if status == VehicleStatus.PENDING.value:
                await self.repo.delete(
                    organization_id=org_id,
                    contact_id=vehicle_contact_id,
                    vehicle_id=vehicle_id,
                )
                continue
            if status != VehicleStatus.APPROVED.value:
                continue
            slot_id = vehicle.get("parking_slot_id")
            if slot_id:
                await self.parking_slots_repo.release_slot(
                    organization_id=org_id,
                    project_id=str(vehicle["project_id"]),
                    slot_id=str(slot_id),
                )
            await self.repo.soft_remove(
                organization_id=org_id,
                contact_id=vehicle_contact_id,
                vehicle_id=vehicle_id,
            )

    async def list_vehicles(
        self,
        *,
        contact_id: str,
        unit_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active vehicles for the contact, optionally filtered by unit."""
        org_id = self.user_context.organization_id
        assert org_id
        if unit_id:
            await self._validate_unit_for_contact(contact_id=contact_id, unit_id=unit_id)
        rows = await self.repo.list_details_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        return [self._serialize_contact_vehicle(row) for row in rows]

    async def get_vehicle_detail(
        self,
        *,
        contact_id: str,
        vehicle_id: str,
    ) -> dict[str, Any]:
        """Return one vehicle with unit and parking slot details for the contact."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.repo.get_detail_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._serialize_admin_vehicle(row)

    async def create_vehicle(
        self,
        *,
        contact_id: str,
        body: CreateVehicleRequest,
    ) -> dict[str, Any]:
        """Create a vehicle linked to an assigned unit."""
        org_id = self.user_context.organization_id
        assert org_id
        project_id = await self._validate_unit_for_contact(
            contact_id=contact_id,
            unit_id=body.unit_id,
        )
        await self._assert_parking_entitlement_available(unit_id=body.unit_id)
        try:
            row = await self.repo.create(
                organization_id=org_id,
                project_id=project_id,
                contact_id=contact_id,
                unit_id=body.unit_id,
                vehicle_type=body.vehicle_type.value,
                registration_number=body.registration_number.strip().upper(),
                make=body.make,
                model=body.model,
                color=body.color,
                photo_paths=body.photo_paths,
                fuel_type=body.fuel_type.value if body.fuel_type else None,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="contact_onboarding.errors.vehicle_registration_duplicate",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        normalized = self._normalize_vehicle(row)
        await self._push().send_to_org_members(
            organization_id=org_id,
            message_key="notifications.push.vehicle.submitted",
            notification_type="NOTIFICATION_TYPE_VEHICLE",
            feed_type="vehicle",
            params={
                "registration_number": normalized.get("registration_number")
                or body.registration_number,
                "unit_label": unit_label_from_row({"unit_id": body.unit_id}),
            },
            data={
                "vehicle_id": normalized.get("id"),
                "project_id": project_id,
                "unit_id": body.unit_id,
                "screen": "vehicle_request_detail",
            },
            entity={"kind": "vehicle", "id": str(normalized.get("id") or "")},
            options={
                "click_action": "OPEN_VEHICLE_REQUEST",
                "idempotency_key": f"vehicle:{normalized.get('id')}:submitted",
            },
        )
        return normalized

    async def update_vehicle(
        self,
        *,
        contact_id: str,
        vehicle_id: str,
        body: UpdateVehicleRequest,
    ) -> dict[str, Any]:
        """Patch a vehicle owned by the contact."""
        org_id = self.user_context.organization_id
        assert org_id
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "vehicle_type" in patch and isinstance(patch["vehicle_type"], VehicleType):
            patch["vehicle_type"] = patch["vehicle_type"].value
        if "fuel_type" in patch and isinstance(patch["fuel_type"], VehicleFuelType):
            patch["fuel_type"] = patch["fuel_type"].value
        if "registration_number" in patch and patch["registration_number"]:
            patch["registration_number"] = patch["registration_number"].strip().upper()
        if "unit_id" in patch and patch["unit_id"]:
            project_id = await self._validate_unit_for_contact(
                contact_id=contact_id,
                unit_id=patch["unit_id"],
            )
            patch["project_id"] = project_id
        try:
            row = await self.repo.update(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="contact_onboarding.errors.vehicle_registration_duplicate",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_vehicle(row)

    async def resubmit_vehicle(
        self,
        *,
        contact_id: str,
        vehicle_id: str,
        body: ResubmitVehicleRequest,
    ) -> dict[str, Any]:
        """Return a rejected vehicle to pending and optionally patch its details."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_id(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if existing.get("status") != VehicleStatus.REJECTED.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_resubmit_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit_id = str(existing["unit_id"])
        project_id = str(existing["project_id"])
        await self._assert_parking_entitlement_available(unit_id=unit_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "vehicle_type" in patch and isinstance(patch["vehicle_type"], VehicleType):
            patch["vehicle_type"] = patch["vehicle_type"].value
        if "fuel_type" in patch and isinstance(patch["fuel_type"], VehicleFuelType):
            patch["fuel_type"] = patch["fuel_type"].value
        if "registration_number" in patch and patch["registration_number"]:
            patch["registration_number"] = patch["registration_number"].strip().upper()
        patch["status"] = VehicleStatus.PENDING.value
        patch["rejection_reason"] = None
        patch["approved_by_user_id"] = None
        patch["rejected_by_user_id"] = None
        try:
            row = await self.repo.update(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="contact_onboarding.errors.vehicle_registration_duplicate",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        normalized = self._normalize_vehicle(row)
        await self._push().send_to_org_members(
            organization_id=org_id,
            message_key="notifications.push.vehicle.submitted",
            notification_type="NOTIFICATION_TYPE_VEHICLE",
            feed_type="vehicle",
            params={
                "registration_number": normalized.get("registration_number")
                or existing.get("registration_number"),
                "unit_label": unit_label_from_row({"unit_id": unit_id}),
            },
            data={
                "vehicle_id": normalized.get("id"),
                "project_id": project_id,
                "unit_id": unit_id,
                "screen": "vehicle_request_detail",
            },
            entity={"kind": "vehicle", "id": str(normalized.get("id") or "")},
            options={
                "click_action": "OPEN_VEHICLE_REQUEST",
                "idempotency_key": f"vehicle:{normalized.get('id')}:resubmitted",
            },
        )
        return normalized

    async def withdraw_vehicle(self, *, contact_id: str, vehicle_id: str) -> None:
        """Hard-delete a pending vehicle request (before admin approval)."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_id(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        status = existing.get("status")
        if status != VehicleStatus.PENDING.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_withdraw_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.delete(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )

    async def remove_vehicle(
        self,
        *,
        contact_id: str,
        vehicle_id: str,
        rejection_reason: str | None = None,
    ) -> dict[str, Any]:
        """Soft-remove an approved vehicle (status removed, row retained)."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_id(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        status = existing.get("status")
        if status == VehicleStatus.PENDING.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_use_withdraw",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if status != VehicleStatus.APPROVED.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_remove_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if existing.get("parking_slot_id"):
            await self.parking_slots_repo.release_slot(
                organization_id=org_id,
                project_id=existing["project_id"],
                slot_id=existing["parking_slot_id"],
            )
        row = await self.repo.soft_remove(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
            rejection_reason=rejection_reason,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_vehicle(row)

    async def admin_delete_vehicle(
        self,
        *,
        contact_id: str,
        vehicle_id: str,
        rejection_reason: str,
    ) -> dict[str, Any] | None:
        """Admin delete/remove a vehicle for a contact."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_id(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        status = str(existing.get("status") or "")
        if status == VehicleStatus.PENDING.value:
            await self.repo.update(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
                update_data={
                    "status": VehicleStatus.REJECTED.value,
                    "rejection_reason": rejection_reason,
                },
            )
            await self.repo.delete(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
            )
            return None
        if status == VehicleStatus.APPROVED.value:
            return await self.remove_vehicle(
                contact_id=contact_id,
                vehicle_id=vehicle_id,
                rejection_reason=rejection_reason,
            )
        if status == VehicleStatus.REJECTED.value:
            await self.repo.update(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
                update_data={"rejection_reason": rejection_reason},
            )
            await self.repo.delete(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
            )
            return None

        raise ValidationException(
            message_key="contact_onboarding.errors.vehicle_remove_not_allowed",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    async def admin_delete_project_vehicle(
        self,
        *,
        project_id: str,
        vehicle_id: str,
        rejection_reason: str,
    ) -> dict[str, Any] | None:
        """Admin delete/remove a vehicle scoped to a project."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_project(
            organization_id=org_id,
            project_id=project_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self.admin_delete_vehicle(
            contact_id=str(existing["contact_id"]),
            vehicle_id=vehicle_id,
            rejection_reason=rejection_reason,
        )

    async def list_project_vehicles(
        self,
        *,
        project_id: str,
        status: VehicleStatus | None = None,
        vehicle_type: VehicleType | None = None,
        fuel_type: VehicleFuelType | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List vehicles for a project (admin)."""
        org_id = self.user_context.organization_id
        assert org_id
        normalized_search = search.strip() if search else None
        if normalized_search == "":
            normalized_search = None
        rows = await self.repo.list_by_project(
            organization_id=org_id,
            project_id=project_id,
            status=status.value if status else None,
            vehicle_type=vehicle_type.value if vehicle_type else None,
            fuel_type=fuel_type.value if fuel_type else None,
            search=normalized_search,
        )
        return [self._serialize_admin_vehicle(row) for row in rows]

    async def review_vehicle(
        self,
        *,
        project_id: str,
        vehicle_id: str,
        body: ReviewVehicleRequest,
    ) -> dict[str, Any]:
        """Approve or reject a vehicle request and assign a parking slot on approval."""
        org_id = self.user_context.organization_id
        assert org_id
        reviewer_user_id = self.user_context.user_id
        vehicle = await self.repo.get_by_project(
            organization_id=org_id,
            project_id=project_id,
            vehicle_id=vehicle_id,
        )
        if not vehicle:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if vehicle.get("status") != VehicleStatus.PENDING.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_not_pending",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if body.status == VehicleStatus.APPROVED:
            assert body.parking_slot_id
            unit_id = str(vehicle.get("unit_id") or "")
            if not unit_id:
                raise ValidationException(
                    message_key="contact_onboarding.errors.unit_not_found",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            await self._assert_parking_entitlement_available(
                unit_id=unit_id,
                exclude_vehicle_id=vehicle_id,
            )
            slot = await self.parking_slots_repo.get_slot(
                organization_id=org_id,
                project_id=project_id,
                slot_id=body.parking_slot_id,
            )
            if not slot or slot.get("status") != "available":
                raise ValidationException(
                    message_key="contact_onboarding.errors.parking_slot_unavailable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            assigned = await self.parking_slots_repo.assign_slot(
                organization_id=org_id,
                project_id=project_id,
                slot_id=body.parking_slot_id,
            )
            if not assigned:
                raise ValidationException(
                    message_key="contact_onboarding.errors.parking_slot_unavailable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            row = await self.repo.update_by_project(
                organization_id=org_id,
                project_id=project_id,
                vehicle_id=vehicle_id,
                update_data={
                    "status": VehicleStatus.APPROVED.value,
                    "parking_slot_id": body.parking_slot_id,
                    "rejection_reason": None,
                    "approved_by_user_id": reviewer_user_id,
                    "rejected_by_user_id": None,
                },
            )
        else:
            row = await self.repo.update_by_project(
                organization_id=org_id,
                project_id=project_id,
                vehicle_id=vehicle_id,
                update_data={
                    "status": VehicleStatus.REJECTED.value,
                    "rejection_reason": body.rejection_reason,
                    "parking_slot_id": None,
                    "approved_by_user_id": None,
                    "rejected_by_user_id": reviewer_user_id,
                },
            )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        detail = await self.repo.get_detail_by_project(
            organization_id=org_id,
            project_id=project_id,
            vehicle_id=vehicle_id,
        )
        normalized = self._serialize_admin_vehicle(detail or row)
        message_key = (
            "notifications.push.vehicle.approved"
            if body.status == VehicleStatus.APPROVED
            else "notifications.push.vehicle.rejected"
        )
        suffix = "approved" if body.status == VehicleStatus.APPROVED else "rejected"
        await self._push().send_to_contact(
            organization_id=org_id,
            contact_id=str(vehicle.get("contact_id") or ""),
            message_key=message_key,
            notification_type="NOTIFICATION_TYPE_VEHICLE",
            feed_type="vehicle",
            params={"registration_number": normalized.get("registration_number") or ""},
            data={
                "vehicle_id": normalized.get("id"),
                "project_id": project_id,
                "screen": "vehicle_detail",
            },
            entity={"kind": "vehicle", "id": str(normalized.get("id") or "")},
            options={
                "click_action": "OPEN_VEHICLE",
                "idempotency_key": f"vehicle:{normalized.get('id')}:{suffix}",
            },
        )
        return normalized

    async def complete_vehicles_step(
        self,
        *,
        contact_id: str,
        contact_unit_id: str,
    ) -> None:
        """Mark the vehicles onboarding step complete for one unit."""
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
        await self.unit_onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
            step_key=ContactOnboardingStep.VEHICLES.value,
        )
