"""Parking allotment admin business logic."""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)
from apps.user_service.app.db.repositories.parking_allotment_repository import (
    ParkingAllotmentRepository,
)
from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)
from apps.user_service.app.schemas.enums import (
    FacilityType,
    ParkingAllotmentBasis,
    ParkingSlotDisplayStatus,
    ParkingSlotEventType,
    ParkingSlotType,
    ParkingUserType,
)
from apps.user_service.app.schemas.parking_allotment import (
    AllotParkingSlotRequest,
    BlockParkingSlotRequest,
    ParkingAllotmentSlotDetailResponse,
    ParkingAllotmentSlotEventResponse,
    ParkingAllotmentSlotHeldResponse,
    ParkingAllotmentSlotListItemResponse,
    ParkingAllotmentSummaryResponse,
    ParkingAllotmentUnitListItemResponse,
    ParkingAllotmentUnitRefResponse,
    ReassignParkingSlotRequest,
    ReleaseParkingSlotRequest,
    UnitAllotParkingSlotRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_SLOT_TYPE_LABELS: dict[str, str] = {
    ParkingSlotType.VISITOR.value: "Visitor",
    ParkingSlotType.CAR_STANDARD.value: "Car — standard",
    ParkingSlotType.EV_CHARGING.value: "EV charging",
    ParkingSlotType.TWO_WHEELER.value: "Two-wheeler",
}


class ParkingAllotmentService:
    """Admin parking allotment screens and mutations."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.organization_id = user_context.organization_id or ""
        self.repo = ParkingAllotmentRepository(db_connection)
        self.slots_repo = ParkingSlotsRepository(db_connection)
        self.facilities_repo = FacilitiesRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )

    @staticmethod
    def _build_slot_code(
        *,
        tower_code: str | None,
        floor_level: str | None,
        slot_number: int | None,
    ) -> str:
        """Format slot code as {tower}-{floor}-{slot_number:03d}."""
        tower = (tower_code or "").strip() or "—"
        floor = (floor_level or "").strip() or "—"
        number = int(slot_number or 0)
        return f"{tower}-{floor}-{number:03d}"

    @classmethod
    def _resolve_slot_code(cls, row: dict[str, Any]) -> str:
        """Prefer persisted slot_code; fall back to tower/floor formatting."""
        stored = row.get("slot_code")
        if stored:
            return str(stored)
        return cls._build_slot_code(
            tower_code=row.get("tower_code"),
            floor_level=row.get("floor_level"),
            slot_number=int(row.get("slot_number") or 0),
        )

    @staticmethod
    def _slot_type_label(slot_type: str) -> str:
        return _SLOT_TYPE_LABELS.get(slot_type, slot_type.replace("_", " ").title())

    @staticmethod
    def _format_date(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    async def _ensure_project(self, *, project_id: str) -> None:
        await self.setup_service.ensure_project(project_id=project_id)

    async def _resolve_list_scope(
        self,
        *,
        project_id: str,
        tower_id: str | None,
        facility_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Validate optional tower/facility filters for read endpoints."""
        if not facility_id:
            return tower_id, None

        facility = await self.facilities_repo.get_facility(
            organization_id=self.organization_id,
            project_id=project_id,
            facility_id=facility_id,
        )
        if (
            not facility
            or str(facility.get("facility_type") or "").lower() != FacilityType.PARKING.value
        ):
            raise NotFoundException(
                message_key="parking_allotment.errors.facility_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        facility_tower_id = str(facility["tower_id"]) if facility.get("tower_id") else None
        if tower_id and facility_tower_id and tower_id != facility_tower_id:
            raise ValidationException(
                message_key="parking_allotment.errors.facility_tower_mismatch",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        return tower_id or facility_tower_id, facility_id

    def _slot_allowed_actions(self, *, display_status: str) -> list[str]:
        if display_status == ParkingSlotDisplayStatus.VISITOR_POOL.value:
            return ["details", "history"]
        if display_status == ParkingSlotDisplayStatus.FREE.value:
            return ["allot", "block", "details", "history"]
        if display_status == ParkingSlotDisplayStatus.ALLOTTED.value:
            return ["reassign", "release", "details", "history"]
        if display_status == ParkingSlotDisplayStatus.BLOCKED.value:
            return ["unblock", "details", "history"]
        return ["details", "history"]

    @staticmethod
    def _slot_vehicle_category(slot_type: str) -> str:
        """Map a parking slot type to two_wheeler or four_wheeler entitlement bucket."""
        if slot_type == ParkingSlotType.TWO_WHEELER.value:
            return ParkingSlotType.TWO_WHEELER.value
        return "four_wheeler"

    def _unit_allowed_actions(
        self,
        *,
        two_wheeler_parking_entitlement: int,
        four_wheeler_parking_entitlement: int,
        included_two_wheeler_slots_assigned: int,
        included_four_wheeler_slots_assigned: int,
        slots_assigned: int,
    ) -> list[str]:
        actions: list[str] = []
        total_entitlement = two_wheeler_parking_entitlement + four_wheeler_parking_entitlement
        if total_entitlement <= 0:
            return actions
        has_included_room = (
            two_wheeler_parking_entitlement > 0
            and included_two_wheeler_slots_assigned < two_wheeler_parking_entitlement
        ) or (
            four_wheeler_parking_entitlement > 0
            and included_four_wheeler_slots_assigned < four_wheeler_parking_entitlement
        )
        if has_included_room:
            actions.append("allot_slot")
        if slots_assigned >= total_entitlement:
            actions.append("add_slot")
        return actions

    async def _validate_unit_for_allotment(
        self,
        *,
        project_id: str,
        unit_id: str,
        allotment_basis: ParkingAllotmentBasis,
        slot_row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        unit = await self.repo.get_unit_allotment_context(
            organization_id=self.organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="parking_allotment.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if bool(unit.get("is_parking")):
            raise ValidationException(
                message_key="parking_allotment.errors.invalid_unit",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if allotment_basis != ParkingAllotmentBasis.INCLUDED_WITH_UNIT:
            return unit

        two_entitlement = int(unit.get("two_wheeler_parking_entitlement") or 0)
        four_entitlement = int(unit.get("four_wheeler_parking_entitlement") or 0)
        two_assigned = int(unit.get("included_two_wheeler_slots_assigned") or 0)
        four_assigned = int(unit.get("included_four_wheeler_slots_assigned") or 0)

        if slot_row is None:
            total_entitlement = two_entitlement + four_entitlement
            included_assigned = int(unit.get("included_slots_assigned") or 0)
            if total_entitlement <= 0:
                raise ValidationException(
                    message_key="parking_allotment.errors.no_entitlement",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if included_assigned >= total_entitlement:
                raise ValidationException(
                    message_key="parking_allotment.errors.entitlement_full",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return unit

        slot_type = str(slot_row.get("slot_type") or ParkingSlotType.CAR_STANDARD.value)
        vehicle_category = self._slot_vehicle_category(slot_type)
        if vehicle_category == ParkingSlotType.TWO_WHEELER.value:
            if two_entitlement <= 0:
                raise ValidationException(
                    message_key="parking_allotment.errors.no_two_wheeler_entitlement",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if two_assigned >= two_entitlement:
                raise ValidationException(
                    message_key="parking_allotment.errors.two_wheeler_entitlement_full",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        else:
            if four_entitlement <= 0:
                raise ValidationException(
                    message_key="parking_allotment.errors.no_four_wheeler_entitlement",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if four_assigned >= four_entitlement:
                raise ValidationException(
                    message_key="parking_allotment.errors.four_wheeler_entitlement_full",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        return unit

    async def _validate_slot_for_allotment(
        self,
        *,
        project_id: str,
        slot_id: str,
    ) -> dict[str, Any]:
        row = await self.repo.get_slot_row(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not row:
            raise NotFoundException(
                message_key="parking_allotment.errors.slot_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        display_status = str(row.get("display_status") or "")
        if display_status != ParkingSlotDisplayStatus.FREE.value:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_available",
                custom_code=CustomStatusCode.CONFLICT,
            )
        if str(row.get("parking_user_type") or "") == ParkingUserType.VISITORS.value:
            raise ValidationException(
                message_key="parking_allotment.errors.visitor_slot_not_allottable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return row

    def _serialize_slot_list_item(
        self, row: dict[str, Any]
    ) -> ParkingAllotmentSlotListItemResponse:
        slot_type = str(row.get("slot_type") or ParkingSlotType.CAR_STANDARD.value)
        display_status = str(row.get("display_status") or ParkingSlotDisplayStatus.FREE.value)
        unit_id = row.get("unit_id")
        allotted_to_unit = None
        if unit_id:
            allotted_to_unit = ParkingAllotmentUnitRefResponse(
                id=str(unit_id),
                code=str(row.get("unit_code") or ""),
            )
        allotted_since = row.get("allotted_at") or row.get("effective_from")
        return ParkingAllotmentSlotListItemResponse(
            id=str(row["id"]),
            slot_code=self._resolve_slot_code(row),
            level_label=row.get("floor_level"),
            bay_label=row.get("wing") or row.get("facility_name"),
            slot_type=ParkingSlotType(slot_type),
            slot_type_label=self._slot_type_label(slot_type),
            status=ParkingSlotDisplayStatus(display_status),
            allotted_to_unit=allotted_to_unit,
            allotted_since=self._format_date(allotted_since),
            facility_id=str(row["facility_id"]),
            slot_number=int(row.get("slot_number") or 0),
            tower_id=str(row["tower_id"]) if row.get("tower_id") else None,
            allowed_actions=self._slot_allowed_actions(display_status=display_status),
        )

    def _serialize_slot_detail(self, row: dict[str, Any]) -> ParkingAllotmentSlotDetailResponse:
        base = self._serialize_slot_list_item(row)
        basis = row.get("allotment_basis")
        return ParkingAllotmentSlotDetailResponse(
            **base.model_dump(),
            allotment_basis=ParkingAllotmentBasis(basis) if basis else None,
            effective_from=self._format_date(row.get("effective_from")),
            facility_name=row.get("facility_name"),
            tower_name=row.get("tower_name"),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    def _serialize_unit_list_item(
        self,
        row: dict[str, Any],
        *,
        slot_rows_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> ParkingAllotmentUnitListItemResponse:
        two_entitlement = int(row.get("two_wheeler_parking_entitlement") or 0)
        four_entitlement = int(row.get("four_wheeler_parking_entitlement") or 0)
        assigned = int(row.get("slots_assigned") or 0)
        two_included_assigned = int(row.get("included_two_wheeler_slots_assigned") or 0)
        four_included_assigned = int(row.get("included_four_wheeler_slots_assigned") or 0)
        short_by = 0
        if two_entitlement > two_included_assigned:
            short_by += two_entitlement - two_included_assigned
        if four_entitlement > four_included_assigned:
            short_by += four_entitlement - four_included_assigned
        if two_entitlement + four_entitlement <= 0:
            entitlement_status = "none"
        elif short_by > 0:
            entitlement_status = "short"
        else:
            entitlement_status = "met"

        slots_held: list[ParkingAllotmentSlotHeldResponse] = []
        for item in row.get("active_allotments") or []:
            if not isinstance(item, dict):
                continue
            slot_id = str(item.get("slot_id") or "")
            slot_meta = (slot_rows_by_id or {}).get(slot_id, {})
            slot_type = str(slot_meta.get("slot_type") or ParkingSlotType.CAR_STANDARD.value)
            slots_held.append(
                ParkingAllotmentSlotHeldResponse(
                    allotment_id=str(item.get("allotment_id") or ""),
                    slot_id=slot_id,
                    slot_code=self._resolve_slot_code(slot_meta),
                    slot_type=ParkingSlotType(slot_type),
                    slot_type_label=self._slot_type_label(slot_type),
                    effective_from=self._format_date(item.get("effective_from")) or "",
                    allotment_basis=ParkingAllotmentBasis(
                        str(
                            item.get("allotment_basis")
                            or ParkingAllotmentBasis.INCLUDED_WITH_UNIT.value
                        )
                    ),
                )
            )

        return ParkingAllotmentUnitListItemResponse(
            id=str(row["id"]),
            code=str(row["code"]),
            configuration_label=row.get("configuration_label"),
            two_wheeler_parking_entitlement=two_entitlement,
            four_wheeler_parking_entitlement=four_entitlement,
            slots_assigned=assigned,
            entitlement_status=entitlement_status,
            entitlement_short_by=short_by,
            slots_held=slots_held,
            allowed_actions=self._unit_allowed_actions(
                two_wheeler_parking_entitlement=two_entitlement,
                four_wheeler_parking_entitlement=four_entitlement,
                included_two_wheeler_slots_assigned=two_included_assigned,
                included_four_wheeler_slots_assigned=four_included_assigned,
                slots_assigned=assigned,
            ),
        )

    async def get_summary(
        self,
        *,
        project_id: str,
        tower_id: str | None = None,
        facility_id: str | None = None,
    ) -> ParkingAllotmentSummaryResponse:
        await self._ensure_project(project_id=project_id)
        scoped_tower_id, scoped_facility_id = await self._resolve_list_scope(
            project_id=project_id,
            tower_id=tower_id,
            facility_id=facility_id,
        )
        row = await self.repo.get_summary(
            organization_id=self.organization_id,
            project_id=project_id,
            tower_id=scoped_tower_id,
            facility_id=scoped_facility_id,
        )
        return ParkingAllotmentSummaryResponse.model_validate(row)

    async def list_slots(
        self,
        *,
        project_id: str,
        tower_id: str | None = None,
        facility_id: str | None = None,
        floor_level: str | None = None,
        slot_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[ParkingAllotmentSlotListItemResponse], int]:
        await self._ensure_project(project_id=project_id)
        scoped_tower_id, scoped_facility_id = await self._resolve_list_scope(
            project_id=project_id,
            tower_id=tower_id,
            facility_id=facility_id,
        )
        rows, total = await self.repo.list_slots(
            organization_id=self.organization_id,
            project_id=project_id,
            tower_id=scoped_tower_id,
            facility_id=scoped_facility_id,
            floor_level=floor_level,
            slot_type=slot_type,
            status=status,
            search=search,
            page=page,
            page_size=page_size,
        )
        return [self._serialize_slot_list_item(row) for row in rows], total

    async def get_slot_detail(
        self,
        *,
        project_id: str,
        slot_id: str,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        row = await self.repo.get_slot_row(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not row:
            raise NotFoundException(
                message_key="parking_allotment.errors.slot_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._serialize_slot_detail(row)

    async def list_slot_history(
        self,
        *,
        project_id: str,
        slot_id: str,
    ) -> list[ParkingAllotmentSlotEventResponse]:
        await self._ensure_project(project_id=project_id)
        if not await self.repo.get_slot_row(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        ):
            raise NotFoundException(
                message_key="parking_allotment.errors.slot_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        rows = await self.repo.list_slot_history(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        return [
            ParkingAllotmentSlotEventResponse(
                id=str(row["id"]),
                event_type=ParkingSlotEventType(str(row["event_type"])),
                unit_id=str(row["unit_id"]) if row.get("unit_id") else None,
                unit_code=row.get("unit_code"),
                allotment_id=str(row["allotment_id"]) if row.get("allotment_id") else None,
                actor_user_id=str(row["actor_user_id"]) if row.get("actor_user_id") else None,
                payload=dict(row.get("payload") or {}),
                occurred_at=format_iso_datetime(row.get("occurred_at")) or "",
            )
            for row in rows
        ]

    async def list_units(
        self,
        *,
        project_id: str,
        tower_id: str | None = None,
        entitlement_status: str | None = None,
        search: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[ParkingAllotmentUnitListItemResponse], int]:
        await self._ensure_project(project_id=project_id)
        rows, total = await self.repo.list_units(
            organization_id=self.organization_id,
            project_id=project_id,
            tower_id=tower_id,
            entitlement_status=entitlement_status,
            search=search,
            page=page,
            page_size=page_size,
        )
        slot_rows_by_id = await self._slot_rows_by_id_for_unit_rows(
            project_id=project_id,
            rows=rows,
        )
        items = [
            self._serialize_unit_list_item(row, slot_rows_by_id=slot_rows_by_id) for row in rows
        ]
        return items, total

    async def _slot_rows_by_id_for_unit_rows(
        self,
        *,
        project_id: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        slot_ids: set[str] = set()
        for row in rows:
            for item in row.get("active_allotments") or []:
                if isinstance(item, dict) and item.get("slot_id"):
                    slot_ids.add(str(item["slot_id"]))
        slot_rows_by_id: dict[str, dict[str, Any]] = {}
        for slot_id in slot_ids:
            slot_row = await self.repo.get_slot_row(
                organization_id=self.organization_id,
                project_id=project_id,
                slot_id=slot_id,
            )
            if slot_row:
                slot_rows_by_id[slot_id] = slot_row
        return slot_rows_by_id

    async def get_unit(
        self,
        *,
        project_id: str,
        unit_id: str,
    ) -> ParkingAllotmentUnitListItemResponse:
        await self._ensure_project(project_id=project_id)
        row = await self.repo.get_unit_for_allotment_view(
            organization_id=self.organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not row:
            raise NotFoundException(
                message_key="parking_allotment.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        slot_rows_by_id = await self._slot_rows_by_id_for_unit_rows(
            project_id=project_id,
            rows=[row],
        )
        return self._serialize_unit_list_item(row, slot_rows_by_id=slot_rows_by_id)

    async def _create_allotment(
        self,
        *,
        project_id: str,
        slot_id: str,
        unit_id: str,
        effective_from: date,
        allotment_basis: ParkingAllotmentBasis,
        event_type: ParkingSlotEventType = ParkingSlotEventType.ALLOTTED,
        payload: dict[str, Any] | None = None,
    ) -> ParkingAllotmentSlotDetailResponse:
        slot_row = await self._validate_slot_for_allotment(
            project_id=project_id,
            slot_id=slot_id,
        )
        await self._validate_unit_for_allotment(
            project_id=project_id,
            unit_id=unit_id,
            allotment_basis=allotment_basis,
            slot_row=slot_row,
        )

        user_id = self.user_context.user_id
        actor_user_id = str(user_id) if user_id else None
        allotment = await self.repo.insert_allotment(
            organization_id=self.organization_id,
            project_id=project_id,
            unit_id=unit_id,
            parking_slot_id=slot_id,
            allotment_basis=allotment_basis.value,
            effective_from=effective_from,
            created_by_user_id=actor_user_id,
        )
        assigned = await self.slots_repo.assign_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not assigned:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_available",
                custom_code=CustomStatusCode.CONFLICT,
            )
        await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            parking_slot_id=slot_id,
            event_type=event_type.value,
            unit_id=unit_id,
            allotment_id=str(allotment["id"]),
            actor_user_id=actor_user_id,
            payload={
                "effective_from": effective_from.isoformat(),
                "allotment_basis": allotment_basis.value,
                **(payload or {}),
            },
        )
        return await self.get_slot_detail(project_id=project_id, slot_id=slot_id)

    async def allot_slot(
        self,
        *,
        project_id: str,
        slot_id: str,
        body: AllotParkingSlotRequest,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        return await self._create_allotment(
            project_id=project_id,
            slot_id=slot_id,
            unit_id=body.unit_id,
            effective_from=body.effective_from,
            allotment_basis=body.allotment_basis,
        )

    async def allot_slot_to_unit(
        self,
        *,
        project_id: str,
        unit_id: str,
        body: UnitAllotParkingSlotRequest,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        return await self._create_allotment(
            project_id=project_id,
            slot_id=body.slot_id,
            unit_id=unit_id,
            effective_from=body.effective_from,
            allotment_basis=body.allotment_basis,
        )

    async def reassign_slot(
        self,
        *,
        project_id: str,
        slot_id: str,
        body: ReassignParkingSlotRequest,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        row = await self.repo.get_slot_row(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not row:
            raise NotFoundException(
                message_key="parking_allotment.errors.slot_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(row.get("display_status") or "") != ParkingSlotDisplayStatus.ALLOTTED.value:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_allotted",
                custom_code=CustomStatusCode.CONFLICT,
            )
        allotment = await self.repo.get_active_allotment_by_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not allotment:
            raise ConflictException(
                message_key="parking_allotment.errors.allotment_not_found",
                custom_code=CustomStatusCode.CONFLICT,
            )

        user_id = self.user_context.user_id
        actor_user_id = str(user_id) if user_id else None
        previous_unit_id = str(allotment["unit_id"])
        released = await self.repo.release_allotment(
            organization_id=self.organization_id,
            project_id=project_id,
            allotment_id=str(allotment["id"]),
            release_reason=body.reason,
            updated_by_user_id=actor_user_id,
        )
        if not released:
            raise ConflictException(
                message_key="parking_allotment.errors.allotment_not_found",
                custom_code=CustomStatusCode.CONFLICT,
            )
        await self.repo.clear_vehicle_slot_references(
            organization_id=self.organization_id,
            parking_slot_id=slot_id,
        )
        await self.slots_repo.release_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            parking_slot_id=slot_id,
            event_type=ParkingSlotEventType.RELEASED.value,
            unit_id=previous_unit_id,
            allotment_id=str(allotment["id"]),
            actor_user_id=actor_user_id,
            payload={"reason": body.reason, "reassign": True},
        )

        await self._validate_unit_for_allotment(
            project_id=project_id,
            unit_id=body.unit_id,
            allotment_basis=body.allotment_basis,
            slot_row=row,
        )
        new_allotment = await self.repo.insert_allotment(
            organization_id=self.organization_id,
            project_id=project_id,
            unit_id=body.unit_id,
            parking_slot_id=slot_id,
            allotment_basis=body.allotment_basis.value,
            effective_from=body.effective_from,
            created_by_user_id=actor_user_id,
        )
        assigned = await self.slots_repo.assign_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not assigned:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_available",
                custom_code=CustomStatusCode.CONFLICT,
            )
        await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            parking_slot_id=slot_id,
            event_type=ParkingSlotEventType.REASSIGNED.value,
            unit_id=body.unit_id,
            allotment_id=str(new_allotment["id"]),
            actor_user_id=actor_user_id,
            payload={
                "previous_unit_id": previous_unit_id,
                "effective_from": body.effective_from.isoformat(),
                "allotment_basis": body.allotment_basis.value,
                "reason": body.reason,
            },
        )
        return await self.get_slot_detail(project_id=project_id, slot_id=slot_id)

    async def release_slot(
        self,
        *,
        project_id: str,
        slot_id: str,
        body: ReleaseParkingSlotRequest,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        allotment = await self.repo.get_active_allotment_by_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not allotment:
            raise ConflictException(
                message_key="parking_allotment.errors.allotment_not_found",
                custom_code=CustomStatusCode.CONFLICT,
            )
        user_id = self.user_context.user_id
        actor_user_id = str(user_id) if user_id else None
        released = await self.repo.release_allotment(
            organization_id=self.organization_id,
            project_id=project_id,
            allotment_id=str(allotment["id"]),
            release_reason=body.reason,
            updated_by_user_id=actor_user_id,
        )
        if not released:
            raise ConflictException(
                message_key="parking_allotment.errors.allotment_not_found",
                custom_code=CustomStatusCode.CONFLICT,
            )
        await self.repo.clear_vehicle_slot_references(
            organization_id=self.organization_id,
            parking_slot_id=slot_id,
        )
        await self.slots_repo.release_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            parking_slot_id=slot_id,
            event_type=ParkingSlotEventType.RELEASED.value,
            unit_id=str(allotment["unit_id"]),
            allotment_id=str(allotment["id"]),
            actor_user_id=actor_user_id,
            payload={"reason": body.reason},
        )
        return await self.get_slot_detail(project_id=project_id, slot_id=slot_id)

    async def block_slot(
        self,
        *,
        project_id: str,
        slot_id: str,
        body: BlockParkingSlotRequest,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        row = await self.repo.get_slot_row(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not row:
            raise NotFoundException(
                message_key="parking_allotment.errors.slot_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(row.get("display_status") or "") != ParkingSlotDisplayStatus.FREE.value:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_available",
                custom_code=CustomStatusCode.CONFLICT,
            )
        blocked = await self.slots_repo.block_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not blocked:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_available",
                custom_code=CustomStatusCode.CONFLICT,
            )
        user_id = self.user_context.user_id
        await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            parking_slot_id=slot_id,
            event_type=ParkingSlotEventType.BLOCKED.value,
            actor_user_id=str(user_id) if user_id else None,
            payload={"reason": body.reason},
        )
        return await self.get_slot_detail(project_id=project_id, slot_id=slot_id)

    async def unblock_slot(
        self,
        *,
        project_id: str,
        slot_id: str,
    ) -> ParkingAllotmentSlotDetailResponse:
        await self._ensure_project(project_id=project_id)
        row = await self.repo.get_slot_row(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not row:
            raise NotFoundException(
                message_key="parking_allotment.errors.slot_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(row.get("display_status") or "") != ParkingSlotDisplayStatus.BLOCKED.value:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_blocked",
                custom_code=CustomStatusCode.CONFLICT,
            )
        unblocked = await self.slots_repo.unblock_slot(
            organization_id=self.organization_id,
            project_id=project_id,
            slot_id=slot_id,
        )
        if not unblocked:
            raise ConflictException(
                message_key="parking_allotment.errors.slot_not_blocked",
                custom_code=CustomStatusCode.CONFLICT,
            )
        user_id = self.user_context.user_id
        await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            parking_slot_id=slot_id,
            event_type=ParkingSlotEventType.UNBLOCKED.value,
            actor_user_id=str(user_id) if user_id else None,
        )
        return await self.get_slot_detail(project_id=project_id, slot_id=slot_id)
