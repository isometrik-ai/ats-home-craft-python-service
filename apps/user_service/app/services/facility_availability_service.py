"""Availability and quote helpers for bookable facilities."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.facility_booking_config_repository import (
    FacilityBookingConfigRepository,
)
from apps.user_service.app.db.repositories.facility_booking_inventory_repository import (
    FacilityBookingInventoryRepository,
)
from apps.user_service.app.db.repositories.facility_reservations_repository import (
    FacilityReservationsRepository,
)
from apps.user_service.app.schemas.facility_booking import (
    DraftEvaluationResponse,
    ReservationDraftRequest,
    ValidationIssueResponse,
)
from apps.user_service.app.services.facility_booking import availability, pricing
from apps.user_service.app.services.facility_booking.availability import (
    AvailabilityContext,
)
from apps.user_service.app.services.facility_booking.snapshot import (
    engine_reservation,
    snapshot_from_rows,
    week_bounds,
)
from apps.user_service.app.services.facility_booking.types import (
    BookingDraft,
    Participant,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class FacilityAvailabilityService:
    """Load facility snapshots and answer availability / pricing questions."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.reservations_repo = FacilityReservationsRepository(db_connection)
        self.config_repo = FacilityBookingConfigRepository(db_connection)
        self.inventory_repo = FacilityBookingInventoryRepository(db_connection)
        self.config_service = FacilityBookingConfigService(
            db_connection=db_connection, user_context=user_context
        )
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def load_snapshot(
        self, *, project_id: str, facility_id: str, active_from: date | None = None
    ) -> tuple[Any, dict[str, Any]]:
        """Return pricing engine snapshot and raw config row."""
        await self.setup_service.ensure_project(project_id=project_id)
        config = await self.config_repo.get_config(
            organization_id=self._org_id, project_id=project_id, facility_id=facility_id
        )
        if not config:
            raise NotFoundException(
                message_key="facility_booking.errors.config_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        inventory = await self.inventory_repo.load_all(
            organization_id=self._org_id,
            facility_id=facility_id,
            active_from=active_from,
        )
        return snapshot_from_rows(config, inventory), config

    async def build_context(
        self,
        *,
        project_id: str,
        facility_id: str,
        range_start: date,
        range_end: date,
        host_contact_id: str | None = None,
    ) -> tuple[AvailabilityContext, Any]:
        """Build availability context for a date range (optionally scoped to a host)."""
        snapshot, config = await self.load_snapshot(
            project_id=project_id, facility_id=facility_id, active_from=range_start
        )
        active_rows = await self.reservations_repo.list_active_in_range(
            organization_id=self._org_id,
            facility_id=facility_id,
            start=range_start,
            end=range_end,
        )
        ids = [str(row["id"]) for row in active_rows]
        participants = await self.reservations_repo.participants_by_reservation(ids)
        active = [engine_reservation(row, participants.get(str(row["id"]))) for row in active_rows]
        weekly_pool = active
        if host_contact_id:
            week_start, week_end = week_bounds(range_start)
            weekly_rows = await self.reservations_repo.list_weekly_pool(
                organization_id=self._org_id,
                facility_id=facility_id,
                contact_id=host_contact_id,
                week_start=week_start,
                week_end=week_end,
            )
            weekly_ids = [str(row["id"]) for row in weekly_rows]
            weekly_parts = await self.reservations_repo.participants_by_reservation(weekly_ids)
            weekly_pool = [
                engine_reservation(row, weekly_parts.get(str(row["id"]))) for row in weekly_rows
            ]
        now = await self.config_service.local_now(project_id)
        return (
            AvailabilityContext(facility=snapshot, active=active, now=now, weekly_pool=weekly_pool),
            config,
        )

    @staticmethod
    def draft(body: ReservationDraftRequest, *, host_contact_id: str) -> BookingDraft:
        """Map an API draft request to the pricing engine draft."""
        return BookingDraft(
            facility_id=body.facility_id,
            unit_id=body.unit_id,
            local_date=body.local_date,
            end_local_date=body.resolved_end_date,
            start_min=body.start_min,
            end_min=body.end_min,
            host_contact_id=host_contact_id,
            participants=[
                Participant(
                    kind=item.kind.value,
                    name=(item.name or "").strip() or "Resident",
                    contact_id=item.contact_id,
                )
                for item in body.participants
            ],
        )

    async def evaluate_draft(
        self,
        *,
        project_id: str,
        body: ReservationDraftRequest,
        host_contact_id: str,
        staff_override: bool = False,
    ) -> dict[str, Any]:
        """Validate a draft and return errors plus an optional price quote."""
        draft = self.draft(body, host_contact_id=host_contact_id)
        ctx, _ = await self.build_context(
            project_id=project_id,
            facility_id=body.facility_id,
            range_start=draft.local_date,
            range_end=draft.end_local_date,
            host_contact_id=host_contact_id,
        )
        validation = availability.validate_draft(ctx, draft)
        if staff_override:
            validation = availability.without_codes(
                validation, availability.STAFF_OVERRIDABLE_CODES
            )
        quote = pricing.quote_booking(ctx.facility, draft)
        return DraftEvaluationResponse(
            ok=validation.ok,
            errors=[
                ValidationIssueResponse(code=issue.code, message=issue.message)
                for issue in validation.errors
            ],
            quote=quote,
        ).model_dump(mode="json")

    async def quote(
        self,
        *,
        project_id: str,
        body: ReservationDraftRequest,
        host_contact_id: str,
        staff_override: bool = False,
    ) -> dict[str, Any]:
        """Return a price quote for a valid draft or raise validation errors."""
        result = await self.evaluate_draft(
            project_id=project_id,
            body=body,
            host_contact_id=host_contact_id,
            staff_override=staff_override,
        )
        if not result["ok"]:
            raise ValidationException(
                message_key="facility_booking.errors.invalid_draft",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                errors=result["errors"],
            )
        return result["quote"]

    async def availability_day(
        self, *, project_id: str, facility_id: str, local_date: date
    ) -> dict[str, Any]:
        """Return slot-level availability for one local calendar day."""
        ctx, _ = await self.build_context(
            project_id=project_id,
            facility_id=facility_id,
            range_start=local_date,
            range_end=local_date,
        )
        return availability.get_day_availability(ctx, local_date).model_dump(mode="json")

    async def availability_month(
        self, *, project_id: str, facility_id: str, start: date
    ) -> list[dict[str, Any]]:
        """Return month overview cells for a facility."""
        end = start + timedelta(days=41)
        ctx, _ = await self.build_context(
            project_id=project_id, facility_id=facility_id, range_start=start, range_end=end
        )
        return [
            item.model_dump(mode="json") for item in availability.get_month_overview(ctx, start)
        ]

    async def availability_month_summary(
        self, *, project_id: str, facility_id: str, start: date
    ) -> list[dict[str, Any]]:
        """Return summarized month availability with slot counts."""
        end = start + timedelta(days=41)
        ctx, _ = await self.build_context(
            project_id=project_id, facility_id=facility_id, range_start=start, range_end=end
        )
        return [item.model_dump(mode="json") for item in availability.get_month_summary(ctx, start)]

    async def next_availability(
        self, *, project_id: str, facility_id: str
    ) -> dict[str, Any] | None:
        """Return the next bookable slot within roughly 60 days, if any."""
        today = (await self.config_service.local_now(project_id)).date()
        ctx, _ = await self.build_context(
            project_id=project_id,
            facility_id=facility_id,
            range_start=today,
            range_end=today + timedelta(days=60),
        )
        result = availability.next_availability(ctx)
        return result.model_dump(mode="json") if result else None

    async def room_availability(
        self, *, project_id: str, facility_id: str, check_in: date, check_out: date
    ) -> list[dict[str, Any]]:
        """Return room-unit availability for a stay window."""
        ctx, _ = await self.build_context(
            project_id=project_id,
            facility_id=facility_id,
            range_start=check_in,
            range_end=check_out,
        )
        return [
            item.model_dump(mode="json")
            for item in availability.room_availability(ctx, check_in, check_out)
        ]

    async def weekly_usage(
        self, *, project_id: str, facility_id: str, contact_id: str, local_date: date
    ) -> dict[str, Any]:
        """Return weekly booking usage against the facility cap for a contact."""
        ctx, _ = await self.build_context(
            project_id=project_id,
            facility_id=facility_id,
            range_start=local_date,
            range_end=local_date,
            host_contact_id=contact_id,
        )
        usage = availability.weekly_usage(
            ctx.weekly_pool or ctx.active, contact_id, facility_id, local_date
        )
        return {
            "usage": usage,
            "cap": ctx.facility.policies.weekly_cap,
            "week_start": availability.week_start(local_date).isoformat(),
        }
