"""Community event booking and payment logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.community_events_repository import (
    CommunityEventsRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)
from apps.user_service.app.schemas.community_events import (
    BookEventResponse,
    CreateEventBookingRequest,
    MarkBookingPaidRequest,
    MarkBookingWaivedRequest,
    VerifyBookingResponse,
)
from apps.user_service.app.schemas.enums import (
    ALLOWED_EVENT_FACILITY_TYPES,
    CommunityEventAuditAction,
    CommunityEventBookingStatus,
    CommunityEventChildTicketMode,
    CommunityEventPaymentStatus,
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
    CommunityEventType,
)
from apps.user_service.app.services.community_event_notification_service import (
    CommunityEventNotificationService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class CommunityEventBookingService:
    """Booking create/cancel/mark-paid and gate verification."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = CommunityEventsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.facilities_repo = FacilitiesRepository(db_connection)
        self.notifications = CommunityEventNotificationService(db_connection=db_connection)

    @property
    def organization_id(self) -> str:
        """Organization id from context."""
        return self.user_context.organization_id

    @staticmethod
    def compute_booking_amounts(
        *,
        adult_tickets: int,
        child_tickets: int,
        event: dict[str, Any],
    ) -> tuple[int, int, int]:
        """Return subtotal_minor, tax_minor, total_minor."""
        if str(event.get("event_type")) == CommunityEventType.FREE.value:
            return 0, 0, 0

        adult_price = int(event.get("adult_price_minor") or 0)
        child_mode = str(
            event.get("child_ticket_mode") or CommunityEventChildTicketMode.NOT_APPLICABLE.value
        )
        child_price = 0
        if child_mode == CommunityEventChildTicketMode.PRICED.value:
            child_price = int(event.get("child_price_minor") or 0)

        subtotal = adult_tickets * adult_price + child_tickets * child_price
        tax_minor = 0
        if event.get("apply_tax") and subtotal > 0:
            rate = Decimal(str(event.get("tax_rate") or 18))
            tax_minor = int(
                (Decimal(subtotal) * rate / Decimal(100)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        return subtotal, tax_minor, subtotal + tax_minor

    @staticmethod
    def _event_end_at(event: dict[str, Any]) -> datetime:
        """Derive event end datetime for eligibility checks."""
        end_date = event["end_date"]
        end_time = event.get("end_time")
        if end_time:
            return datetime.combine(end_date, end_time, tzinfo=timezone.utc)
        return datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)

    @staticmethod
    def _is_booking_open(event: dict[str, Any]) -> bool:
        """True when residents may still book."""
        now = datetime.now(timezone.utc)
        if str(event.get("publish_status")) != CommunityEventPublishStatus.PUBLISHED.value:
            return False
        if str(event.get("record_status")) != CommunityEventRecordStatus.ACTIVE.value:
            return False
        closes_at = event.get("booking_closes_at")
        if closes_at and closes_at <= now:
            return False
        if CommunityEventBookingService._event_end_at(event) <= now:
            return False
        return True

    async def _ensure_resident_unit(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> str:
        """Validate resident unit access; return project_id."""
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=self.organization_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="community_events.errors.invalid_unit_context",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        unit_row = await self.contact_units_repo.get_unit_project(
            organization_id=self.organization_id,
            unit_id=unit_id,
        )
        if not unit_row:
            raise ValidationException(
                message_key="community_events.errors.invalid_unit_context",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        return str(unit_row["project_id"])

    async def _ensure_owner_or_tenant(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> None:
        """Require Owner or Tenant role on unit."""
        allowed = await self.repo.contact_has_owner_or_tenant_on_unit(
            organization_id=self.organization_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not allowed:
            raise ValidationException(
                message_key="community_events.errors.invalid_unit_context",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

    async def create_booking(
        self,
        *,
        contact_id: str,
        unit_id: str,
        event_id: str,
        body: CreateEventBookingRequest,
    ) -> BookEventResponse:
        """Create resident booking with capacity/waitlist handling."""
        project_id = await self._ensure_resident_unit(contact_id=contact_id, unit_id=unit_id)
        await self._ensure_owner_or_tenant(contact_id=contact_id, unit_id=unit_id)

        event = await self.repo.fetch_resident_event_by_id(
            organization_id=self.organization_id,
            event_id=event_id,
        )
        if not event or str(event.get("project_id")) != project_id:
            raise NotFoundException(
                message_key="community_events.errors.event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if not self._is_booking_open(event):
            raise ValidationException(
                message_key="community_events.errors.booking_closed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        child_mode = str(
            event.get("child_ticket_mode") or CommunityEventChildTicketMode.NOT_APPLICABLE.value
        )
        if (
            body.child_tickets > 0
            and child_mode == CommunityEventChildTicketMode.NOT_APPLICABLE.value
        ):
            raise ValidationException(
                message_key="community_events.errors.child_tickets_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        total_requested = body.adult_tickets + body.child_tickets
        max_per = int(event.get("max_tickets_per_resident") or 4)
        existing = await self.repo.count_active_tickets_for_contact(
            organization_id=self.organization_id,
            event_id=event_id,
            contact_id=contact_id,
        )
        if existing + total_requested > max_per:
            raise ValidationException(
                message_key="community_events.errors.ticket_limit_exceeded",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        capacity = event.get("total_capacity")
        tickets_booked = int(event.get("tickets_booked") or 0)
        booking_status = CommunityEventBookingStatus.CONFIRMED.value
        if capacity is not None and tickets_booked + total_requested > int(capacity):
            booking_status = CommunityEventBookingStatus.WAITLISTED.value

        subtotal, tax_minor, total_minor = self.compute_booking_amounts(
            adult_tickets=body.adult_tickets,
            child_tickets=body.child_tickets,
            event=event,
        )
        payment_status = (
            CommunityEventPaymentStatus.NOT_APPLICABLE.value
            if str(event.get("event_type")) == CommunityEventType.FREE.value
            else CommunityEventPaymentStatus.PENDING.value
        )

        seq = await self.repo.allocate_booking_sequence(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        gate_token = (
            uuid.uuid4().hex
            if booking_status == CommunityEventBookingStatus.CONFIRMED.value
            else None
        )

        booking = await self.repo.insert_booking(
            organization_id=self.organization_id,
            project_id=project_id,
            data={
                "event_id": event_id,
                "display_code": f"BKG-{seq}",
                "sequence_number": seq,
                "contact_id": contact_id,
                "unit_id": unit_id,
                "adult_tickets": body.adult_tickets,
                "child_tickets": body.child_tickets,
                "total_tickets": total_requested,
                "subtotal_minor": subtotal,
                "tax_minor": tax_minor,
                "total_amount_minor": total_minor,
                "booking_status": booking_status,
                "payment_status": payment_status,
                "gate_qr_token": gate_token,
            },
        )

        if booking_status == CommunityEventBookingStatus.CONFIRMED.value:
            await self.repo.adjust_event_aggregates_on_booking(
                organization_id=self.organization_id,
                event_id=event_id,
                tickets_delta=total_requested,
                bookings_delta=1,
            )
        else:
            await self.repo.adjust_event_aggregates_on_booking(
                organization_id=self.organization_id,
                event_id=event_id,
                tickets_delta=0,
                bookings_delta=1,
            )

        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=str(booking["id"]),
            action=CommunityEventAuditAction.BOOKING_CREATED.value,
            actor_contact_id=contact_id,
            payload={"booking_status": booking_status, "total_tickets": total_requested},
        )

        if booking_status == CommunityEventBookingStatus.CONFIRMED.value:
            await self.notifications.notify_booking_confirmed(
                organization_id=self.organization_id,
                contact_id=contact_id,
                event=event,
                booking=booking,
            )
        else:
            await self.notifications.notify_booking_waitlisted(
                organization_id=self.organization_id,
                contact_id=contact_id,
                event=event,
                booking=booking,
            )

        instruction = None
        if payment_status == CommunityEventPaymentStatus.PENDING.value:
            instruction = "Pay at the clubhouse. Your booking is confirmed pending payment."

        return BookEventResponse(
            booking_id=str(booking["id"]),
            display_code=str(booking["display_code"]),
            adult_tickets=body.adult_tickets,
            child_tickets=body.child_tickets,
            total_tickets=total_requested,
            subtotal_minor=subtotal,
            tax_minor=tax_minor,
            total_amount_minor=total_minor,
            currency=str(booking.get("currency") or "INR"),
            payment_status=payment_status,
            booking_status=booking_status,
            gate_qr_token=gate_token,
            payment_instruction=instruction,
        )

    async def cancel_booking(
        self,
        *,
        contact_id: str | None,
        unit_id: str | None,
        booking_id: str,
        admin_user_id: str | None = None,
    ) -> None:
        """Cancel booking and optionally promote waitlist."""
        booking = await self.repo.fetch_booking_by_id(
            organization_id=self.organization_id,
            booking_id=booking_id,
        )
        if not booking:
            raise NotFoundException(
                message_key="community_events.errors.booking_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(booking.get("booking_status")) == CommunityEventBookingStatus.CANCELLED.value:
            raise ConflictException(
                message_key="community_events.errors.booking_already_cancelled",
                custom_code=CustomStatusCode.CONFLICT,
            )

        if contact_id:
            if str(booking.get("contact_id")) != contact_id:
                raise ValidationException(
                    message_key="community_events.errors.invalid_unit_context",
                    custom_code=CustomStatusCode.FORBIDDEN,
                )
            if unit_id and str(booking.get("unit_id")) != unit_id:
                raise ValidationException(
                    message_key="community_events.errors.invalid_unit_context",
                    custom_code=CustomStatusCode.FORBIDDEN,
                )

        was_confirmed = (
            str(booking.get("booking_status")) == CommunityEventBookingStatus.CONFIRMED.value
        )
        tickets = int(booking.get("total_tickets") or 0)
        event_id = str(booking["event_id"])
        project_id = str(booking["project_id"])

        await self.repo.update_booking_fields(
            organization_id=self.organization_id,
            booking_id=booking_id,
            fields={
                "booking_status": CommunityEventBookingStatus.CANCELLED.value,
                "cancelled_at": datetime.now(timezone.utc),
                "cancelled_by_contact_id": contact_id,
                "cancelled_by_user_id": admin_user_id,
            },
        )
        await self.repo.adjust_event_aggregates_on_booking(
            organization_id=self.organization_id,
            event_id=event_id,
            tickets_delta=-tickets if was_confirmed else 0,
            bookings_delta=-1,
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=booking_id,
            action=CommunityEventAuditAction.BOOKING_CANCELLED.value,
            actor_user_id=admin_user_id,
            actor_contact_id=contact_id,
        )

        if was_confirmed:
            await self._promote_waitlist(event_id=event_id)

    async def _promote_waitlist(self, *, event_id: str) -> None:
        """Promote oldest waitlisted booking when capacity frees."""
        project_row = await self.db_connection.fetchrow(
            """
            SELECT project_id::text AS project_id
            FROM community_events
            WHERE organization_id = $1::uuid AND id = $2::uuid
            """,
            self.organization_id,
            event_id,
        )
        if not project_row:
            return
        project_id = str(project_row["project_id"])
        event = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        if not event:
            return

        capacity = event.get("total_capacity")
        if capacity is None:
            return
        tickets_booked = int(event.get("tickets_booked") or 0)
        remaining = int(capacity) - tickets_booked
        if remaining <= 0:
            return

        waitlisted = await self.repo.fetch_oldest_waitlisted_booking(
            organization_id=self.organization_id,
            event_id=event_id,
        )
        if not waitlisted:
            return

        promote_tickets = int(waitlisted.get("total_tickets") or 0)
        if promote_tickets > remaining:
            return

        gate_token = uuid.uuid4().hex
        await self.repo.update_booking_fields(
            organization_id=self.organization_id,
            booking_id=str(waitlisted["id"]),
            fields={
                "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
                "gate_qr_token": gate_token,
            },
        )
        await self.repo.adjust_event_aggregates_on_booking(
            organization_id=self.organization_id,
            event_id=event_id,
            tickets_delta=promote_tickets,
            bookings_delta=0,
        )
        await self.notifications.notify_waitlist_promoted(
            organization_id=self.organization_id,
            contact_id=str(waitlisted["contact_id"]),
            event=event,
            booking=waitlisted,
        )

    async def mark_paid(
        self,
        *,
        project_id: str,
        event_id: str,
        booking_id: str,
        body: MarkBookingPaidRequest,
    ) -> dict[str, Any]:
        """Admin mark booking paid."""
        booking = await self.repo.fetch_booking_by_id(
            organization_id=self.organization_id,
            booking_id=booking_id,
            event_id=event_id,
        )
        if not booking or str(booking.get("project_id")) != project_id:
            raise NotFoundException(
                message_key="community_events.errors.booking_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(booking.get("payment_status")) != CommunityEventPaymentStatus.PENDING.value:
            raise ConflictException(
                message_key="community_events.errors.booking_not_pending",
                custom_code=CustomStatusCode.CONFLICT,
            )

        updated = await self.repo.update_booking_fields(
            organization_id=self.organization_id,
            booking_id=booking_id,
            fields={
                "payment_status": CommunityEventPaymentStatus.PAID.value,
                "paid_at": datetime.now(timezone.utc),
                "paid_by_user_id": self.user_context.user_id,
                "payment_notes": body.payment_notes,
            },
        )
        await self.repo.increment_paid_revenue(
            organization_id=self.organization_id,
            event_id=event_id,
            amount_minor=int(booking.get("total_amount_minor") or 0),
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=booking_id,
            action=CommunityEventAuditAction.MARKED_PAID.value,
            actor_user_id=self.user_context.user_id,
            payload={"payment_notes": body.payment_notes},
        )
        event = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        if event:
            await self.notifications.notify_payment_received(
                organization_id=self.organization_id,
                contact_id=str(booking["contact_id"]),
                event=event,
                booking=updated or booking,
            )
        return updated or booking

    async def mark_waived(
        self,
        *,
        project_id: str,
        event_id: str,
        booking_id: str,
        body: MarkBookingWaivedRequest,
    ) -> dict[str, Any]:
        """Admin waive booking payment."""
        booking = await self.repo.fetch_booking_by_id(
            organization_id=self.organization_id,
            booking_id=booking_id,
            event_id=event_id,
        )
        if not booking or str(booking.get("project_id")) != project_id:
            raise NotFoundException(
                message_key="community_events.errors.booking_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(booking.get("payment_status")) != CommunityEventPaymentStatus.PENDING.value:
            raise ConflictException(
                message_key="community_events.errors.booking_not_pending",
                custom_code=CustomStatusCode.CONFLICT,
            )
        updated = await self.repo.update_booking_fields(
            organization_id=self.organization_id,
            booking_id=booking_id,
            fields={
                "payment_status": CommunityEventPaymentStatus.WAIVED.value,
                "paid_at": datetime.now(timezone.utc),
                "paid_by_user_id": self.user_context.user_id,
                "payment_notes": body.payment_notes,
            },
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=booking_id,
            action=CommunityEventAuditAction.MARKED_WAIVED.value,
            actor_user_id=self.user_context.user_id,
        )
        return updated or booking

    async def verify_booking_at_gate(
        self,
        *,
        gate_qr_token: str,
    ) -> VerifyBookingResponse:
        """Security gate QR verification."""
        booking = await self.repo.fetch_booking_by_gate_token(
            organization_id=self.organization_id,
            gate_qr_token=gate_qr_token,
        )
        if not booking:
            raise NotFoundException(
                message_key="community_events.errors.booking_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(booking.get("booking_status")) != CommunityEventBookingStatus.CONFIRMED.value:
            raise ValidationException(
                message_key="community_events.errors.booking_not_valid_for_gate",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=str(booking["project_id"]),
            event_id=str(booking["event_id"]),
            booking_id=str(booking["id"]),
            action=CommunityEventAuditAction.GATE_VERIFIED.value,
            actor_user_id=self.user_context.user_id,
            payload={"gate_qr_token": gate_qr_token},
        )

        return VerifyBookingResponse(
            booking_id=str(booking["id"]),
            display_code=str(booking["display_code"]),
            event_title=str(booking.get("event_title") or ""),
            event_start_date=booking["event_start_date"],
            contact_name=booking.get("contact_name"),
            unit_code=booking.get("unit_code"),
            adult_tickets=int(booking.get("adult_tickets") or 0),
            child_tickets=int(booking.get("child_tickets") or 0),
            total_tickets=int(booking.get("total_tickets") or 0),
            payment_status=str(booking.get("payment_status") or ""),
            booking_status=str(booking.get("booking_status") or ""),
        )

    @staticmethod
    def validate_facility_for_event(facility: dict[str, Any] | None) -> None:
        """Ensure facility is eligible event venue."""
        if not facility:
            raise ValidationException(
                message_key="community_events.errors.facility_not_eligible",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        ftype = str(facility.get("facility_type") or "").strip().lower()
        if ftype not in ALLOWED_EVENT_FACILITY_TYPES:
            raise ValidationException(
                message_key="community_events.errors.facility_not_eligible",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not facility.get("active") or str(facility.get("status")) != "active":
            raise ValidationException(
                message_key="community_events.errors.facility_not_eligible",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
