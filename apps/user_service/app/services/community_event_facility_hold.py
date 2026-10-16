"""Freeze bookable facility slots while a community event is published."""

from __future__ import annotations

from datetime import time
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)
from apps.user_service.app.db.repositories.facility_booking_inventory_repository import (
    FacilityBookingInventoryRepository,
)
from apps.user_service.app.db.repositories.facility_reservations_repository import (
    FacilityReservationsRepository,
)
from apps.user_service.app.schemas.facility_booking_config import MINUTES_PER_DAY
from apps.user_service.app.schemas.facility_booking_inventory import (
    CreateSlotBlockRequest,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

SLOT_BLOCK_CATEGORY = "community_event"


def event_hold_minutes(*, start_time: time | None, end_time: time | None) -> tuple[int, int]:
    """Map event clock times to a half-open minute window. Missing times freeze the day."""
    from_min = start_time.hour * 60 + start_time.minute if start_time is not None else 0
    to_min = end_time.hour * 60 + end_time.minute if end_time is not None else MINUTES_PER_DAY
    if to_min <= from_min:
        raise ValidationException(
            message_key="community_events.errors.invalid_event_hours",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    return from_min, to_min


def _hold_reason(title: str) -> str:
    """Resident-facing slot-block reason, capped to the inventory field."""
    label = (title or "Community event").strip() or "Community event"
    reason = f"Community event: {label}"
    return reason[:200]


class CommunityEventFacilityHoldService:
    """Hold a bookable venue via the existing facility slot-block APIs."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.facilities_repo = FacilitiesRepository(db_connection)
        self.inventory_repo = FacilityBookingInventoryRepository(db_connection)
        self.reservations_repo = FacilityReservationsRepository(db_connection)
        self.config = FacilityBookingConfigService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def ensure(self, *, project_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """Hold the venue calendar when the facility is bookable; otherwise drop any hold."""
        event_id = str(event["id"])
        facility_id = event.get("facility_id")
        if not facility_id:
            await self.release(project_id=project_id, event_id=event_id)
            return None
        facility = await self.facilities_repo.get_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=str(facility_id),
        )
        if not facility or not facility.get("is_bookable"):
            await self.release(project_id=project_id, event_id=event_id)
            return None

        from_min, to_min = event_hold_minutes(
            start_time=event.get("start_time"),
            end_time=event.get("end_time"),
        )
        await self._assert_no_reservation_overlap(
            facility_id=str(facility_id),
            start_date=event["start_date"],
            end_date=event["end_date"],
            from_min=from_min,
            to_min=to_min,
        )
        await self.release(project_id=project_id, event_id=event_id)
        return await self.config.create_slot_block(
            project_id=project_id,
            facility_id=str(facility_id),
            body=CreateSlotBlockRequest(
                unit_id=None,
                starts_on=event["start_date"],
                ends_on=event["end_date"],
                from_min=from_min,
                to_min=to_min,
                reason=_hold_reason(str(event.get("title") or "")),
                category=SLOT_BLOCK_CATEGORY,
                community_event_id=event_id,
            ),
        )

    async def release(self, *, project_id: str, event_id: str) -> None:
        """Remove the calendar hold for this event if one exists."""
        existing = await self.inventory_repo.get_slot_block_for_event(
            organization_id=self._org_id, event_id=event_id
        )
        if not existing:
            return
        try:
            await self.config.delete_inventory(
                project_id=project_id,
                facility_id=str(existing["facility_id"]),
                table="facility_slot_blocks",
                row_id=str(existing["id"]),
            )
        except NotFoundException:
            return

    async def _assert_no_reservation_overlap(
        self,
        *,
        facility_id: str,
        start_date: Any,
        end_date: Any,
        from_min: int,
        to_min: int,
    ) -> None:
        """Reject a hold when an active reservation already occupies the window."""
        rows = await self.reservations_repo.list_active_in_range(
            organization_id=self._org_id,
            facility_id=facility_id,
            start=start_date,
            end=end_date,
        )
        if any(int(row["start_min"]) < to_min and from_min < int(row["end_min"]) for row in rows):
            raise ConflictException(
                message_key="community_events.errors.facility_slots_busy",
                custom_code=CustomStatusCode.CONFLICT,
            )
