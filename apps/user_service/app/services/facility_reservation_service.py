"""Facility reservation lifecycle: quote, book, approve, check-in, cancel, reschedule."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.facility_reservations_repository import (
    FacilityReservationsRepository,
)
from apps.user_service.app.schemas.enums import (
    FacilityBookingLedgerType,
    FacilityReservationActorType,
    FacilityReservationEventType,
    FacilityReservationListTab,
    FacilityReservationStatus,
)
from apps.user_service.app.schemas.facility_booking import (
    CancelReservationRequest,
    CreateResidentReservationRequest,
    CreateStaffReservationRequest,
    FacilityReservationResponse,
    PriceQuote,
    RejectReservationRequest,
    RescheduleReservationRequest,
    ReservationDraftRequest,
    ReservationNoteRequest,
)
from apps.user_service.app.services.facility_availability_service import (
    FacilityAvailabilityService,
)
from apps.user_service.app.services.facility_booking import availability, pricing
from apps.user_service.app.services.facility_booking.lifecycle import (
    ReservationAction,
    can_transition,
    exclusion_key,
    initial_status,
    target_status,
)
from apps.user_service.app.services.facility_booking.money import ts_round
from apps.user_service.app.services.facility_booking.snapshot import engine_reservation
from apps.user_service.app.services.facility_booking.types import Validation
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.services.facility_booking_ledger_service import (
    FacilityBookingLedgerService,
)
from apps.user_service.app.services.facility_booking_notification_service import (
    FacilityBookingNotificationService,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.facility_booking_access import ensure_host_unit
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_STAFF_ACTOR = FacilityReservationActorType.STAFF.value
_RESIDENT_ACTOR = FacilityReservationActorType.RESIDENT.value


class FacilityReservationService:
    """Create and transition facility reservations."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.reservations_repo = FacilityReservationsRepository(db_connection)
        self.availability = FacilityAvailabilityService(
            db_connection=db_connection, user_context=user_context
        )
        self.config_service = FacilityBookingConfigService(
            db_connection=db_connection, user_context=user_context
        )
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.ledger = FacilityBookingLedgerService(
            db_connection=db_connection, user_context=user_context
        )
        self.notifier = FacilityBookingNotificationService(
            db_connection=db_connection, organization_id=user_context.organization_id
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def _zone(self, project_id: str) -> ZoneInfo:
        """Return the project IANA timezone as a ZoneInfo."""
        name = await self.config_service.timezone_for(project_id)
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _aware(self, local_date: date, minute: int, zone: ZoneInfo) -> datetime:
        """Convert local date + minute offset to timezone-aware datetime."""
        naive = datetime(local_date.year, local_date.month, local_date.day) + timedelta(
            minutes=minute
        )
        return naive.replace(tzinfo=zone)

    def _raise_if_invalid(self, validation: Validation) -> None:
        """Raise when a booking draft fails availability validation."""
        if validation.ok:
            return
        raise ValidationException(
            message_key="facility_booking.errors.invalid_draft",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            errors=[{"code": issue.code, "message": issue.message} for issue in validation.errors],
        )

    async def _create(
        self,
        *,
        project_id: str,
        body: ReservationDraftRequest,
        host_contact_id: str,
        host_unit_id: str | None,
        notes: str | None,
        actor_type: str,
        staff_override: bool,
        rescheduled_from_id: str | None = None,
        approved: bool | None = None,
    ) -> dict[str, Any]:
        """Create a reservation row after validation, quote and notifications."""
        await ensure_host_unit(
            db_connection=self.db_connection,
            organization_id=self._org_id,
            contact_id=host_contact_id,
            project_id=project_id,
            host_unit_id=host_unit_id,
        )
        draft = self.availability.draft(body, host_contact_id=host_contact_id)
        await self.reservations_repo.lock_facility(body.facility_id)
        ctx, _ = await self.availability.build_context(
            project_id=project_id,
            facility_id=body.facility_id,
            range_start=draft.local_date,
            range_end=draft.end_local_date,
            host_contact_id=host_contact_id,
        )
        if not ctx.facility.accepting_bookings:
            raise ValidationException(
                message_key="facility_booking.errors.facility_not_bookable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        validation = availability.validate_draft(ctx, draft)
        if staff_override:
            validation = availability.without_codes(
                validation, availability.STAFF_OVERRIDABLE_CODES
            )
        self._raise_if_invalid(validation)
        quote = pricing.quote_booking(ctx.facility, draft)
        zone = await self._zone(project_id)
        status = (
            FacilityReservationStatus.CONFIRMED.value
            if approved
            else initial_status(ctx.facility.policies.requires_approval)
        )
        now = datetime.now(timezone.utc)
        insert = {
            "organization_id": self._org_id,
            "project_id": project_id,
            "facility_id": body.facility_id,
            "unit_id": body.unit_id,
            "host_contact_id": host_contact_id,
            "host_unit_id": host_unit_id,
            "booked_by_user_id": self.user_context.user_id,
            "booked_by_actor": actor_type,
            "local_date": draft.local_date,
            "end_local_date": draft.end_local_date,
            "start_min": draft.start_min,
            "end_min": draft.end_min,
            "starts_at": self._aware(draft.local_date, draft.start_min, zone),
            "ends_at": self._aware(draft.end_local_date, draft.end_min, zone),
            "status": status,
            "quote": quote.model_dump(mode="json"),
            "rescheduled_from_id": rescheduled_from_id,
            "notes": notes,
            "exclusion_key": exclusion_key(ctx.facility.archetype, ctx.facility.id, body.unit_id),
        }
        if status == FacilityReservationStatus.CONFIRMED.value:
            insert["approved_at"] = now
            insert["approved_by_user_id"] = self.user_context.user_id
        created = await self.reservations_repo.insert_reservation(insert)
        reservation_id = str(created["id"])
        await self.reservations_repo.insert_participants(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            participants=[
                {"kind": p.kind, "contact_id": p.contact_id, "name": p.name}
                for p in draft.participants
            ],
        )
        event_type = (
            FacilityReservationEventType.CREATED.value
            if status == FacilityReservationStatus.CONFIRMED.value
            else FacilityReservationEventType.SUBMITTED.value
        )
        await self.reservations_repo.insert_event(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            event_type=event_type,
            actor_type=actor_type,
            message="Reservation created.",
            actor_user_id=self.user_context.user_id,
        )
        label = self._facility_label(ctx.facility, body.unit_id)
        if rescheduled_from_id is None:
            if status == FacilityReservationStatus.CONFIRMED.value:
                billed_later = pricing.is_billed_later(ctx.facility)
                if quote.total > 0:
                    await self._post_ledger(
                        project_id=project_id,
                        contact_id=host_contact_id,
                        reservation_id=reservation_id,
                        entry_type=FacilityBookingLedgerType.CHARGE.value,
                        description=(
                            f"{label} — monthly billing" if billed_later else f"{label} — booking"
                        ),
                        amount=quote.total,
                    )
                await self.notifier.confirmed(
                    contact_id=host_contact_id,
                    reservation_id=reservation_id,
                    facility_label=label,
                    total=quote.total,
                    billed_later=billed_later,
                )
            else:
                await self.notifier.submitted(
                    contact_id=host_contact_id,
                    reservation_id=reservation_id,
                    facility_label=label,
                )
        created_view = await self.get_reservation(
            project_id=project_id, reservation_id=reservation_id, include_events=True
        )
        if (
            rescheduled_from_id is None
            and status == FacilityReservationStatus.PENDING_APPROVAL.value
        ):
            await self.notifier.approval_requested(
                reservation_id=reservation_id,
                facility_label=label,
                host_name=str(created_view.get("host_name") or "A resident"),
            )
        return created_view

    async def create_resident(
        self, *, project_id: str, contact_id: str, body: CreateResidentReservationRequest
    ) -> dict[str, Any]:
        """Create a reservation on behalf of the signed-in resident."""
        return await self._create(
            project_id=project_id,
            body=body,
            host_contact_id=contact_id,
            host_unit_id=body.host_unit_id,
            notes=body.notes,
            actor_type=_RESIDENT_ACTOR,
            staff_override=False,
        )

    async def create_staff(
        self, *, project_id: str, body: CreateStaffReservationRequest
    ) -> dict[str, Any]:
        """Create a reservation on behalf of a resident (staff override)."""
        has_unit = await self.contact_units_repo.contact_has_active_project_membership(
            organization_id=self._org_id,
            contact_id=body.host_contact_id,
            project_id=project_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="facility_booking.errors.host_not_in_project",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return await self._create(
            project_id=project_id,
            body=body,
            host_contact_id=body.host_contact_id,
            host_unit_id=body.host_unit_id,
            notes=body.notes,
            actor_type=_STAFF_ACTOR,
            staff_override=True,
        )

    def _serialize_reservation(
        self,
        row: dict[str, Any],
        participants: list[dict[str, Any]],
        events: list[dict[str, Any]] | None,
        *,
        snapshot,
        now: datetime,
    ) -> dict[str, Any]:
        """Map DB row + participants to API reservation payload."""
        engine = engine_reservation(row, participants)
        can_move, blocked = availability.can_reschedule(snapshot, engine, now)
        quote = row.get("quote") or {}
        return FacilityReservationResponse(
            id=str(row["id"]),
            facility_id=str(row["facility_id"]),
            facility_name=str(row.get("facility_name") or ""),
            archetype=row["archetype"],
            unit_id=str(row["unit_id"]) if row.get("unit_id") else None,
            unit_name=row.get("unit_name"),
            host_contact_id=str(row["host_contact_id"]),
            host_name=str(row.get("host_name") or ""),
            host_unit_id=str(row["host_unit_id"]) if row.get("host_unit_id") else None,
            local_date=row["local_date"],
            end_local_date=row["end_local_date"],
            start_min=int(row["start_min"]),
            end_min=int(row["end_min"]),
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            status=row["status"],
            quote=PriceQuote.model_validate(quote),
            participants=participants,
            rescheduled_from_id=str(row["rescheduled_from_id"])
            if row.get("rescheduled_from_id")
            else None,
            rescheduled_to_id=str(row["rescheduled_to_id"])
            if row.get("rescheduled_to_id")
            else None,
            reject_reason=row.get("reject_reason"),
            cancel_info=row.get("cancel_info"),
            notes=row.get("notes"),
            booked_by_actor=row.get("booked_by_actor") or _RESIDENT_ACTOR,
            reschedule_cutoff_hours=snapshot.policies.reschedule_cutoff_hours,
            no_show_fee_percent=snapshot.policies.no_show_fee_percent,
            can_reschedule=can_move,
            reschedule_blocked_reason=blocked or None,
            created_at=row["created_at"],
            approved_at=row.get("approved_at"),
            checked_in_at=row.get("checked_in_at"),
            completed_at=row.get("completed_at"),
            cancelled_at=row.get("cancelled_at"),
            events=events,
        ).model_dump(mode="json")

    async def get_reservation(
        self,
        *,
        project_id: str,
        reservation_id: str,
        include_events: bool = False,
        host_contact_id: str | None = None,
        for_update: bool = False,
    ) -> dict[str, Any]:
        """Return one reservation, optionally scoped to the host contact."""
        row = await self.reservations_repo.get_reservation(
            organization_id=self._org_id,
            project_id=project_id,
            reservation_id=reservation_id,
            for_update=for_update,
        )
        if not row:
            raise NotFoundException(
                message_key="facility_booking.errors.reservation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if host_contact_id and str(row["host_contact_id"]) != host_contact_id:
            raise ForbiddenException(
                message_key="facility_booking.errors.reservation_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        parts = await self.reservations_repo.participants_by_reservation([reservation_id])
        events = (
            await self.reservations_repo.list_events([reservation_id]) if include_events else None
        )
        snapshot, _ = await self.availability.load_snapshot(
            project_id=project_id, facility_id=str(row["facility_id"])
        )
        now = await self.config_service.local_now(project_id)
        return self._serialize_reservation(
            row,
            parts.get(reservation_id, []),
            events,
            snapshot=snapshot,
            now=now,
        )

    async def list_reservations(
        self,
        *,
        project_id: str,
        facility_id: str | None = None,
        statuses: list[str] | None = None,
        contact_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        exclude_statuses: list[str] | None = None,
        search: str | None = None,
        descending: bool = False,
        page: int = 1,
        page_size: int = 20,
        include_events: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """List reservations with optional filters and pagination."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows, total = await self.reservations_repo.list_reservations(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            statuses=statuses,
            contact_id=contact_id,
            date_from=date_from,
            date_to=date_to,
            exclude_statuses=exclude_statuses,
            search=search,
            descending=descending,
            page=page,
            page_size=page_size,
        )
        ids = [str(row["id"]) for row in rows]
        participants = await self.reservations_repo.participants_by_reservation(ids)
        events_by_id: dict[str, list[dict[str, Any]]] = {}
        if include_events:
            for event in await self.reservations_repo.list_events(ids):
                events_by_id.setdefault(event["reservation_id"], []).append(event)
        now = await self.config_service.local_now(project_id)
        items: list[dict[str, Any]] = []
        snapshots: dict[str, Any] = {}
        for row in rows:
            facility_id_row = str(row["facility_id"])
            if facility_id_row not in snapshots:
                snapshots[facility_id_row], _ = await self.availability.load_snapshot(
                    project_id=project_id, facility_id=facility_id_row
                )
            items.append(
                self._serialize_reservation(
                    row,
                    participants.get(str(row["id"]), []),
                    events_by_id.get(str(row["id"])) if include_events else None,
                    snapshot=snapshots[facility_id_row],
                    now=now,
                )
            )
        return items, total

    async def list_mine(
        self,
        *,
        project_id: str,
        contact_id: str,
        tab: FacilityReservationListTab,
        facility_id: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List upcoming or past reservations for one contact."""
        today = (await self.config_service.local_now(project_id)).date()
        date_from = today if tab == FacilityReservationListTab.UPCOMING else None
        date_to = today - timedelta(days=1) if tab == FacilityReservationListTab.PAST else None
        return await self.list_reservations(
            project_id=project_id,
            facility_id=facility_id,
            contact_id=contact_id,
            date_from=date_from,
            date_to=date_to,
            exclude_statuses=[FacilityReservationStatus.RESCHEDULED.value],
            descending=tab == FacilityReservationListTab.PAST,
            page=page,
            page_size=page_size,
        )

    def _assert_action(self, action: ReservationAction, status: str) -> None:
        """Raise when the requested lifecycle action is invalid for the status."""
        if not can_transition(action, str(status)):
            raise ValidationException(
                message_key="facility_booking.errors.invalid_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _facility_label(self, snapshot, unit_id: str | None) -> str:
        """Build a display label including unit name when multiple units exist."""
        unit = next((item for item in snapshot.units if item.id == str(unit_id or "")), None)
        if unit and len(snapshot.units) > 1:
            return f"{snapshot.name} · {unit.name}"
        return snapshot.name

    async def _post_ledger(
        self,
        *,
        project_id: str,
        contact_id: str,
        reservation_id: str,
        entry_type: str,
        description: str,
        amount: int,
    ) -> None:
        """Post a non-zero ledger entry for a reservation."""
        if amount == 0:
            return
        await self.ledger.post(
            project_id=project_id,
            contact_id=contact_id,
            reservation_id=reservation_id,
            entry_type=entry_type,
            description=description,
            amount=amount,
        )

    async def _transition(
        self,
        *,
        project_id: str,
        reservation_id: str,
        action: ReservationAction,
        actor_type: str,
        message: str,
        event_type: str,
        extra: dict[str, Any] | None = None,
        host_contact_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply a lifecycle transition and append a timeline event."""
        row = await self.reservations_repo.get_reservation(
            organization_id=self._org_id,
            project_id=project_id,
            reservation_id=reservation_id,
            for_update=True,
        )
        if not row:
            raise NotFoundException(
                message_key="facility_booking.errors.reservation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if host_contact_id and str(row["host_contact_id"]) != host_contact_id:
            raise ForbiddenException(
                message_key="facility_booking.errors.reservation_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        self._assert_action(action, str(row["status"]))
        update = {"status": target_status(action), **(extra or {})}
        await self.reservations_repo.update_reservation(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            update_data=update,
        )
        await self.reservations_repo.insert_event(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            event_type=event_type,
            actor_type=actor_type,
            message=message,
            actor_user_id=self.user_context.user_id,
            actor_contact_id=host_contact_id,
        )
        return await self.get_reservation(
            project_id=project_id, reservation_id=reservation_id, include_events=True
        )

    async def approve(self, *, project_id: str, reservation_id: str) -> dict[str, Any]:
        """Approve a pending reservation and collect deposit when configured."""
        now = datetime.now(timezone.utc)
        result = await self._transition(
            project_id=project_id,
            reservation_id=reservation_id,
            action=ReservationAction.APPROVE,
            actor_type=_STAFF_ACTOR,
            message="Reservation approved.",
            event_type=FacilityReservationEventType.APPROVED.value,
            extra={
                "approved_at": now,
                "approved_by_user_id": self.user_context.user_id,
            },
        )
        quote = PriceQuote.model_validate(result.get("quote") or {})
        label = result.get("facility_name") or "Facility"
        if quote.deposit > 0:
            await self._post_ledger(
                project_id=project_id,
                contact_id=str(result["host_contact_id"]),
                reservation_id=reservation_id,
                entry_type=FacilityBookingLedgerType.DEPOSIT.value,
                description=f"{label} — deposit",
                amount=quote.deposit,
            )
        await self.notifier.approved(
            contact_id=str(result["host_contact_id"]),
            reservation_id=reservation_id,
            facility_label=label,
            deposit=quote.deposit,
        )
        return result

    async def reject(
        self, *, project_id: str, reservation_id: str, body: RejectReservationRequest
    ) -> dict[str, Any]:
        """Reject a pending reservation with a staff reason."""
        result = await self._transition(
            project_id=project_id,
            reservation_id=reservation_id,
            action=ReservationAction.REJECT,
            actor_type=_STAFF_ACTOR,
            message=body.reason,
            event_type=FacilityReservationEventType.REJECTED.value,
            extra={"reject_reason": body.reason},
        )
        await self.notifier.rejected(
            contact_id=str(result["host_contact_id"]),
            reservation_id=reservation_id,
            facility_label=str(result.get("facility_name") or "Facility"),
            reason=body.reason,
        )
        return result

    async def check_in(self, *, project_id: str, reservation_id: str) -> dict[str, Any]:
        """Check in a guest and post any balance due at arrival."""
        now = datetime.now(timezone.utc)
        result = await self._transition(
            project_id=project_id,
            reservation_id=reservation_id,
            action=ReservationAction.CHECK_IN,
            actor_type=_STAFF_ACTOR,
            message="Guest checked in.",
            event_type=FacilityReservationEventType.CHECKED_IN.value,
            extra={
                "checked_in_at": now,
                "checked_in_by_user_id": self.user_context.user_id,
            },
        )
        quote = PriceQuote.model_validate(result.get("quote") or {})
        label = result.get("facility_name") or "Facility"
        if quote.due_later > 0:
            await self._post_ledger(
                project_id=project_id,
                contact_id=str(result["host_contact_id"]),
                reservation_id=reservation_id,
                entry_type=FacilityBookingLedgerType.BALANCE.value,
                description=f"{label} — balance on check-in",
                amount=quote.due_later,
            )
        await self.notifier.checked_in(
            contact_id=str(result["host_contact_id"]),
            reservation_id=reservation_id,
            facility_label=label,
        )
        return result

    async def complete(self, *, project_id: str, reservation_id: str) -> dict[str, Any]:
        """Mark a checked-in reservation as completed."""
        return await self._transition(
            project_id=project_id,
            reservation_id=reservation_id,
            action=ReservationAction.COMPLETE,
            actor_type=_STAFF_ACTOR,
            message="Reservation completed.",
            event_type=FacilityReservationEventType.COMPLETED.value,
            extra={"completed_at": datetime.now(timezone.utc)},
        )

    async def mark_no_show(self, *, project_id: str, reservation_id: str) -> dict[str, Any]:
        """Mark a reservation as no-show and apply configured forfeit/refund."""
        paid = await self.ledger.net_paid_for(project_id=project_id, reservation_id=reservation_id)
        result = await self._transition(
            project_id=project_id,
            reservation_id=reservation_id,
            action=ReservationAction.NO_SHOW,
            actor_type=_STAFF_ACTOR,
            message="Marked as no-show.",
            event_type=FacilityReservationEventType.NO_SHOW.value,
        )
        percent = int(result.get("no_show_fee_percent") or 0)
        forfeit = ts_round((paid * percent) / 100)
        label = result.get("facility_name") or "Facility"
        host_id = str(result["host_contact_id"])
        if paid > 0:
            await self._post_ledger(
                project_id=project_id,
                contact_id=host_id,
                reservation_id=reservation_id,
                entry_type=FacilityBookingLedgerType.REFUND.value,
                description=f"{label} — refund",
                amount=-paid,
            )
        if forfeit > 0:
            await self._post_ledger(
                project_id=project_id,
                contact_id=host_id,
                reservation_id=reservation_id,
                entry_type=FacilityBookingLedgerType.NO_SHOW_FORFEIT.value,
                description=f"{label} — no-show fee ({percent}%)",
                amount=forfeit,
            )
        await self.notifier.no_show(
            contact_id=host_id,
            reservation_id=reservation_id,
            facility_label=label,
            forfeit=forfeit,
        )
        return result

    async def cancel_quote(
        self, *, project_id: str, reservation_id: str, host_contact_id: str | None = None
    ) -> dict[str, Any]:
        """Preview cancellation fee and refund for a reservation."""
        row = await self.reservations_repo.get_reservation(
            organization_id=self._org_id,
            project_id=project_id,
            reservation_id=reservation_id,
        )
        if not row:
            raise NotFoundException(
                message_key="facility_booking.errors.reservation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if host_contact_id and str(row["host_contact_id"]) != host_contact_id:
            raise ForbiddenException(
                message_key="facility_booking.errors.reservation_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        snapshot, _ = await self.availability.load_snapshot(
            project_id=project_id, facility_id=str(row["facility_id"])
        )
        now = await self.config_service.local_now(project_id)
        paid = await self.ledger.net_paid_for(project_id=project_id, reservation_id=reservation_id)
        quote = pricing.cancel_quote(
            snapshot,
            engine_reservation(row),
            paid,
            now,
        )
        return quote.model_dump(mode="json")

    async def cancel(
        self,
        *,
        project_id: str,
        reservation_id: str,
        body: CancelReservationRequest,
        actor_type: str,
        host_contact_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a reservation and post fee/refund ledger entries."""
        action = (
            ReservationAction.STAFF_CANCEL
            if actor_type == _STAFF_ACTOR
            else ReservationAction.RESIDENT_CANCEL
        )
        paid = await self.ledger.net_paid_for(project_id=project_id, reservation_id=reservation_id)
        if actor_type == _STAFF_ACTOR:
            quote = {
                "fee": 0,
                "refund": paid,
                "paid": paid,
                "free": paid == 0,
                "hours_before": 0,
                "tier": None,
            }
        else:
            quote = await self.cancel_quote(
                project_id=project_id,
                reservation_id=reservation_id,
                host_contact_id=host_contact_id,
            )
        now = datetime.now(timezone.utc)
        result = await self._transition(
            project_id=project_id,
            reservation_id=reservation_id,
            action=action,
            actor_type=actor_type,
            message=body.reason or "Reservation cancelled.",
            event_type=FacilityReservationEventType.CANCELLED.value,
            extra={
                "cancelled_at": now,
                "cancelled_by_user_id": self.user_context.user_id,
                "cancel_info": {
                    "fee": quote["fee"],
                    "refund": quote["refund"],
                    "at": now.isoformat(),
                    "reason": body.reason,
                    "by": actor_type,
                },
            },
            host_contact_id=host_contact_id,
        )
        label = result.get("facility_name") or "Facility"
        host_id = str(result["host_contact_id"])
        fee = int(quote["fee"])
        refunded = int(quote["paid"]) if actor_type == _STAFF_ACTOR else int(quote["paid"])
        if refunded > 0:
            description = (
                f"{label} — club cancellation, full refund"
                if actor_type == _STAFF_ACTOR
                else f"{label} — refund on cancellation"
            )
            await self._post_ledger(
                project_id=project_id,
                contact_id=host_id,
                reservation_id=reservation_id,
                entry_type=FacilityBookingLedgerType.REFUND.value,
                description=description,
                amount=-refunded,
            )
        if fee > 0:
            tier = quote.get("tier") or {}
            percent = tier.get("fee_percent") if isinstance(tier, dict) else 0
            await self._post_ledger(
                project_id=project_id,
                contact_id=host_id,
                reservation_id=reservation_id,
                entry_type=FacilityBookingLedgerType.CANCELLATION_FEE.value,
                description=f"{label} — cancellation fee ({percent or 0}%)",
                amount=fee,
            )
        await self.notifier.cancelled(
            contact_id=host_id,
            reservation_id=reservation_id,
            facility_label=label,
            by_staff=actor_type == _STAFF_ACTOR,
            free=bool(quote.get("free")),
            fee=fee,
            refund=int(quote["refund"]),
            reason=body.reason,
        )
        return result

    async def reschedule(
        self,
        *,
        project_id: str,
        reservation_id: str,
        body: RescheduleReservationRequest,
        actor_type: str,
        host_contact_id: str | None = None,
        staff_override: bool = False,
    ) -> dict[str, Any]:
        """Move a reservation to a new time slot (creates a linked replacement)."""
        current = await self.reservations_repo.get_reservation(
            organization_id=self._org_id,
            project_id=project_id,
            reservation_id=reservation_id,
            for_update=True,
        )
        if not current:
            raise NotFoundException(
                message_key="facility_booking.errors.reservation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if host_contact_id and str(current["host_contact_id"]) != host_contact_id:
            raise ForbiddenException(
                message_key="facility_booking.errors.reservation_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        action = (
            ReservationAction.STAFF_RESCHEDULE
            if actor_type == _STAFF_ACTOR
            else ReservationAction.RESIDENT_RESCHEDULE
        )
        self._assert_action(action, str(current["status"]))
        snapshot, _ = await self.availability.load_snapshot(
            project_id=project_id, facility_id=str(current["facility_id"])
        )
        now = await self.config_service.local_now(project_id)
        parts = await self.reservations_repo.participants_by_reservation([reservation_id])
        engine = engine_reservation(current, parts.get(reservation_id, []))
        if actor_type == _RESIDENT_ACTOR:
            allowed, reason = availability.can_reschedule(snapshot, engine, now)
            if not allowed:
                raise ValidationException(
                    message_key="facility_booking.errors.reschedule_not_allowed",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    errors=[{"code": "reschedule_not_allowed", "message": reason}],
                )
        draft_body = ReservationDraftRequest(
            facility_id=str(current["facility_id"]),
            unit_id=body.unit_id if body.unit_id is not None else current.get("unit_id"),
            local_date=body.local_date,
            end_local_date=body.end_local_date,
            start_min=body.start_min,
            end_min=body.end_min,
            participants=body.participants
            or [
                # Reuse existing names; kind/contact reconstructed loosely for validation.
            ],
        )
        if not draft_body.participants:
            from apps.user_service.app.schemas.enums import FacilityParticipantKind
            from apps.user_service.app.schemas.facility_booking import ParticipantInput

            draft_body.participants = [
                ParticipantInput(
                    kind=FacilityParticipantKind(p["kind"]),
                    contact_id=p.get("contact_id"),
                    name=p["name"],
                )
                for p in parts.get(reservation_id, [])
            ]
        created = await self._create(
            project_id=project_id,
            body=draft_body,
            host_contact_id=str(current["host_contact_id"]),
            host_unit_id=str(current["host_unit_id"]) if current.get("host_unit_id") else None,
            notes=current.get("notes"),
            actor_type=actor_type,
            staff_override=staff_override,
            rescheduled_from_id=reservation_id,
            approved=str(current["status"]) == FacilityReservationStatus.CONFIRMED.value,
        )
        await self.reservations_repo.update_reservation(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            update_data={
                "status": FacilityReservationStatus.RESCHEDULED.value,
                "rescheduled_to_id": created["id"],
            },
        )
        await self.reservations_repo.insert_event(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            event_type=FacilityReservationEventType.RESCHEDULED.value,
            actor_type=actor_type,
            message="Reservation rescheduled.",
            actor_user_id=self.user_context.user_id,
            actor_contact_id=host_contact_id,
            payload={"to_id": created["id"]},
        )
        new_quote = PriceQuote.model_validate(created.get("quote") or {})
        paid_old = await self.ledger.net_paid_for(
            project_id=project_id, reservation_id=reservation_id
        )
        new_id = str(created["id"])
        label = created.get("facility_name") or snapshot.name
        if created.get("unit_name") and created.get("unit_name") != label:
            label = f"{label} · {created['unit_name']}"
        pending = created.get("status") == FacilityReservationStatus.PENDING_APPROVAL.value
        if not pending:
            diff = ts_round(new_quote.total - paid_old)
            if diff > 0:
                await self._post_ledger(
                    project_id=project_id,
                    contact_id=str(current["host_contact_id"]),
                    reservation_id=new_id,
                    entry_type=FacilityBookingLedgerType.ADJUSTMENT.value,
                    description=f"{label} — reschedule price difference",
                    amount=diff,
                )
            elif diff < 0:
                await self._post_ledger(
                    project_id=project_id,
                    contact_id=str(current["host_contact_id"]),
                    reservation_id=new_id,
                    entry_type=FacilityBookingLedgerType.REFUND.value,
                    description=f"{label} — reschedule refund",
                    amount=diff,
                )
        await self.notifier.rescheduled(
            contact_id=str(current["host_contact_id"]),
            reservation_id=new_id,
            facility_label=label,
            by_staff=actor_type == _STAFF_ACTOR,
            pending=pending,
        )
        return created

    async def add_note(
        self, *, project_id: str, reservation_id: str, body: ReservationNoteRequest
    ) -> dict[str, Any]:
        """Append a staff note to the reservation timeline."""
        await self.get_reservation(project_id=project_id, reservation_id=reservation_id)
        await self.reservations_repo.insert_event(
            organization_id=self._org_id,
            reservation_id=reservation_id,
            event_type=FacilityReservationEventType.NOTE.value,
            actor_type=_STAFF_ACTOR,
            message=body.message,
            actor_user_id=self.user_context.user_id,
        )
        return await self.get_reservation(
            project_id=project_id, reservation_id=reservation_id, include_events=True
        )
