"""In-app + push notifications for facility booking lifecycle events."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.services.facility_booking.money import fmt_money
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("facility_booking_notifications")

_NOTIFICATION_TYPE = "NOTIFICATION_TYPE_SYSTEM"
_FEED_TYPE = "facility_booking"


class FacilityBookingNotificationService:
    """Maps Clubhouse booking notify() events onto Home Craft push + in-app feed."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        organization_id: str,
        dispatcher: PushNotificationDispatcher | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.dispatcher = dispatcher or PushNotificationDispatcher(db_connection=db_connection)

    async def _to_contact(
        self,
        *,
        contact_id: str,
        message_key: str,
        params: dict[str, Any],
        reservation_id: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Send a push/in-app notification to one contact."""
        data = {"reservation_id": reservation_id, **(extra or {})}
        try:
            await self.dispatcher.send_to_contact(
                organization_id=self.organization_id,
                contact_id=contact_id,
                message_key=message_key,
                notification_type=_NOTIFICATION_TYPE,
                feed_type=_FEED_TYPE,
                params=params,
                data=data,
                entity={"id": reservation_id, "type": "facility_reservation"}
                if reservation_id
                else None,
            )
        except Exception:
            logger.exception("facility booking contact notification failed key=%s", message_key)

    async def _to_staff(
        self,
        *,
        message_key: str,
        params: dict[str, Any],
        reservation_id: str | None,
    ) -> None:
        """Send a push/in-app notification to org staff members."""
        try:
            await self.dispatcher.send_to_org_members(
                organization_id=self.organization_id,
                message_key=message_key,
                notification_type=_NOTIFICATION_TYPE,
                feed_type=_FEED_TYPE,
                params=params,
                data={"reservation_id": reservation_id},
                entity={"id": reservation_id, "type": "facility_reservation"}
                if reservation_id
                else None,
            )
        except Exception:
            logger.exception("facility booking staff notification failed key=%s", message_key)

    async def confirmed(
        self,
        *,
        contact_id: str,
        reservation_id: str,
        facility_label: str,
        total: int,
        billed_later: bool,
    ) -> None:
        """Notify the host that a reservation was confirmed."""
        if total > 0:
            charge_text = (
                f" {fmt_money(total)} will be billed on your next invoice."
                if billed_later
                else f" {fmt_money(total)} charged to your account."
            )
        else:
            charge_text = " Included in your membership — no charge."
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.confirmed",
            params={"facility_label": facility_label, "charge_text": charge_text},
            reservation_id=reservation_id,
        )

    async def submitted(self, *, contact_id: str, reservation_id: str, facility_label: str) -> None:
        """Notify the host that a reservation was submitted for approval."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.submitted",
            params={"facility_label": facility_label},
            reservation_id=reservation_id,
        )

    async def approval_requested(
        self,
        *,
        reservation_id: str,
        facility_label: str,
        host_name: str,
    ) -> None:
        """Alert staff that a reservation needs approval."""
        await self._to_staff(
            message_key="notifications.push.facility_booking.approval_requested",
            params={"facility_label": facility_label, "host_name": host_name},
            reservation_id=reservation_id,
        )

    async def approved(
        self,
        *,
        contact_id: str,
        reservation_id: str,
        facility_label: str,
        deposit: int,
    ) -> None:
        """Notify the host that a pending reservation was approved."""
        deposit_text = (
            f" Deposit {fmt_money(deposit)} charged; balance due at check-in."
            if deposit > 0
            else ""
        )
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.approved",
            params={"facility_label": facility_label, "deposit_text": deposit_text},
            reservation_id=reservation_id,
        )

    async def rejected(
        self,
        *,
        contact_id: str,
        reservation_id: str,
        facility_label: str,
        reason: str,
    ) -> None:
        """Notify the host that a reservation was rejected."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.rejected",
            params={"facility_label": facility_label, "reason": reason},
            reservation_id=reservation_id,
        )

    async def checked_in(
        self, *, contact_id: str, reservation_id: str, facility_label: str
    ) -> None:
        """Notify the host that check-in completed."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.checked_in",
            params={"facility_label": facility_label},
            reservation_id=reservation_id,
        )

    async def cancelled(
        self,
        *,
        contact_id: str,
        reservation_id: str,
        facility_label: str,
        by_staff: bool,
        free: bool,
        fee: int,
        refund: int,
        reason: str | None,
    ) -> None:
        """Notify the host about a cancellation (by staff or resident)."""
        if by_staff:
            await self._to_contact(
                contact_id=contact_id,
                message_key="notifications.push.facility_booking.cancelled_by_staff",
                params={
                    "facility_label": facility_label,
                    "reason": reason or "Cancelled by community staff.",
                },
                reservation_id=reservation_id,
            )
            return
        summary = (
            "No charges were applied."
            if free
            else f"Cancellation fee {fmt_money(fee)} · refund {fmt_money(refund)}."
        )
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.cancelled",
            params={"facility_label": facility_label, "summary": summary},
            reservation_id=reservation_id,
        )

    async def rescheduled(
        self,
        *,
        contact_id: str,
        reservation_id: str,
        facility_label: str,
        by_staff: bool,
        pending: bool,
    ) -> None:
        """Notify the host about a reschedule (immediate or pending approval)."""
        if pending:
            key = "notifications.push.facility_booking.reschedule_submitted"
        elif by_staff:
            key = "notifications.push.facility_booking.rescheduled_by_staff"
        else:
            key = "notifications.push.facility_booking.rescheduled"
        await self._to_contact(
            contact_id=contact_id,
            message_key=key,
            params={"facility_label": facility_label},
            reservation_id=reservation_id,
        )
        if pending:
            await self.approval_requested(
                reservation_id=reservation_id,
                facility_label=facility_label,
                host_name="A resident",
            )

    async def no_show(
        self,
        *,
        contact_id: str,
        reservation_id: str,
        facility_label: str,
        forfeit: int,
    ) -> None:
        """Notify the host about a no-show and any forfeit amount."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.no_show",
            params={"facility_label": facility_label, "amount": fmt_money(forfeit)},
            reservation_id=reservation_id,
        )

    async def invoice_generated(
        self,
        *,
        contact_id: str,
        invoice_id: str,
        number: str,
        total: int,
        due_date: str,
    ) -> None:
        """Notify a contact that a new invoice was generated."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.invoice_generated",
            params={
                "number": number,
                "amount": fmt_money(total),
                "due_date": due_date,
            },
            reservation_id=None,
            extra={"invoice_id": invoice_id},
        )

    async def payment_received(
        self, *, contact_id: str, invoice_id: str, number: str, method: str
    ) -> None:
        """Notify a contact that an invoice payment was recorded."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.payment_received",
            params={"number": number, "method": method},
            reservation_id=None,
            extra={"invoice_id": invoice_id},
        )

    async def wallet_updated(self, *, contact_id: str, amount: int, reason: str) -> None:
        """Notify a contact about a wallet credit or debit."""
        direction = "credited" if amount > 0 else "debited"
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.wallet_updated",
            params={
                "direction": direction,
                "amount": fmt_money(abs(amount)),
                "reason": reason,
            },
            reservation_id=None,
        )

    async def charge_posted(
        self,
        *,
        contact_id: str,
        reservation_id: str | None,
        amount: int,
        description: str,
    ) -> None:
        """Notify a contact that a charge was posted to their account."""
        await self._to_contact(
            contact_id=contact_id,
            message_key="notifications.push.facility_booking.charge_posted",
            params={"amount": fmt_money(amount), "description": description},
            reservation_id=reservation_id,
        )
