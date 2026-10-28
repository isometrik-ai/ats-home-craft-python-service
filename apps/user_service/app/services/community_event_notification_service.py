"""Push notifications for community events."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.schemas.enums import (
    CommunityEventBookingStatus,
    CommunityEventPaymentStatus,
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
)
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("community_event_notifications")

_ISSUED_PAYMENT_STATUSES = frozenset(
    {
        CommunityEventPaymentStatus.PAID.value,
        CommunityEventPaymentStatus.NOT_APPLICABLE.value,
        CommunityEventPaymentStatus.WAIVED.value,
    }
)

_RESENDABLE_PUBLISH_STATUSES = frozenset(
    {
        CommunityEventPublishStatus.PUBLISHED.value,
        CommunityEventPublishStatus.COMPLETED.value,
    }
)


def _event_title_params(event: dict[str, Any]) -> dict[str, str]:
    """Template params for community event push copy."""
    title = str(event.get("title") or "").strip()
    return {"title": title or "Community event"}


def ticket_items_for_booking(booking: dict[str, Any]) -> list[dict[str, str]]:
    """Build adult/child ticket cards from booking counts. Codes are stable."""
    display_code = str(booking.get("display_code") or "").strip()
    items: list[dict[str, str]] = []
    if not display_code:
        return items
    for index in range(1, int(booking.get("adult_tickets") or 0) + 1):
        items.append({"code": f"{display_code}-A{index}", "ticket_type": "adult"})
    for index in range(1, int(booking.get("child_tickets") or 0) + 1):
        items.append({"code": f"{display_code}-C{index}", "ticket_type": "child"})
    return items


def tickets_are_issued(booking: dict[str, Any]) -> bool:
    """True when the resident already received tickets for this booking."""
    return (
        str(booking.get("booking_status") or "") == CommunityEventBookingStatus.CONFIRMED.value
        and str(booking.get("payment_status") or "") in _ISSUED_PAYMENT_STATUSES
    )


def can_resend_event_tickets(
    booking: dict[str, Any],
    event: dict[str, Any] | None = None,
) -> bool:
    """True when admin may replay the original ticket payload."""
    if not tickets_are_issued(booking):
        return False
    if event is None:
        return True
    if str(event.get("record_status") or "") != CommunityEventRecordStatus.ACTIVE.value:
        return False
    return str(event.get("publish_status") or "") in _RESENDABLE_PUBLISH_STATUSES


def ticket_push_data(*, event: dict[str, Any], booking: dict[str, Any]) -> dict[str, Any]:
    """Push payload reused for first send and resend — never mints new codes or QR."""
    items = ticket_items_for_booking(booking)
    title = str(event.get("title") or "").strip() or "Community event"
    return {
        "event_id": str(event.get("id") or ""),
        "booking_id": str(booking.get("id") or ""),
        "display_code": str(booking.get("display_code") or ""),
        "title": title,
        "ticket_codes": ", ".join(item["code"] for item in items if item.get("code")),
        "adult_tickets": int(booking.get("adult_tickets") or 0),
        "child_tickets": int(booking.get("child_tickets") or 0),
        "total_tickets": int(booking.get("total_tickets") or 0),
        "total_amount_minor": int(booking.get("total_amount_minor") or 0),
        "currency": str(booking.get("currency") or event.get("currency") or "INR"),
        "gate_qr_token": booking.get("gate_qr_token"),
        "project_id": str(event.get("project_id") or booking.get("project_id") or ""),
    }


class CommunityEventNotificationService:
    """Dispatch community event push notifications."""

    def __init__(self, *, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection
        self.push_dispatcher = PushNotificationDispatcher(db_connection=db_connection)
        self.contacts_repo = ContactsRepository(db_connection=db_connection)

    async def _send_to_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
        message_key: str,
        params: dict[str, Any],
        data: dict[str, Any],
    ) -> None:
        """Send push to contact's linked user if present."""
        contact = await self.contacts_repo.get_contact_details(
            organization_id=organization_id,
            contact_id=contact_id,
        )
        if not contact or not contact.get("user_id"):
            return
        try:
            await self.push_dispatcher.send_to_user(
                organization_id=organization_id,
                recipient_user_id=str(contact["user_id"]),
                message_key=message_key,
                notification_type="community_event",
                feed_type="community_events",
                params=params,
                data=data,
                entity={"type": "community_event", "id": data.get("event_id", "")},
                options={},
            )
        except Exception as exc:  # — push must not fail booking flow
            logger.warning("community event push failed: %s", exc)

    async def notify_event_published(
        self,
        *,
        organization_id: str,
        event: dict[str, Any],
        recipient_user_ids: list[str],
    ) -> None:
        """Notify project residents when event is published."""
        params = _event_title_params(event)
        data = {
            "event_id": str(event["id"]),
            "title": params["title"],
            "project_id": str(event.get("project_id") or ""),
        }
        for user_id in recipient_user_ids:
            try:
                await self.push_dispatcher.send_to_user(
                    organization_id=organization_id,
                    recipient_user_id=user_id,
                    message_key="notifications.push.community_events.published",
                    notification_type="community_event",
                    feed_type="community_events",
                    params=params,
                    data=data,
                    entity={"type": "community_event", "id": data["event_id"]},
                    options={},
                )
            except Exception as exc:
                logger.warning("publish push failed for %s: %s", user_id, exc)

    async def notify_booking_confirmed(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> None:
        """Notify resident on confirmed booking."""
        payload = ticket_push_data(event=event, booking=booking)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.booking_confirmed",
            params={"title": payload["title"]},
            data=payload,
        )

    async def notify_booking_waitlisted(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> None:
        """Notify resident when waitlisted."""
        params = _event_title_params(event)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.booking_waitlisted",
            params=params,
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": params["title"],
            },
        )

    async def notify_waitlist_promoted(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> None:
        """Notify resident when promoted from waitlist."""
        params = _event_title_params(event)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.waitlist_promoted",
            params=params,
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": params["title"],
            },
        )

    async def notify_payment_received(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> None:
        """Notify resident when payment recorded."""
        payload = ticket_push_data(event=event, booking=booking)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.payment_received",
            params={"title": payload["title"]},
            data=payload,
        )

    async def notify_tickets_resent(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> bool:
        """Replay the original ticket payload. Returns False when no linked user."""
        contact = await self.contacts_repo.get_contact_details(
            organization_id=organization_id,
            contact_id=contact_id,
        )
        if not contact or not contact.get("user_id"):
            return False
        payload = ticket_push_data(event=event, booking=booking)
        await self.push_dispatcher.send_to_user(
            organization_id=organization_id,
            recipient_user_id=str(contact["user_id"]),
            message_key="notifications.push.community_events.tickets_resent",
            notification_type="community_event",
            feed_type="community_events",
            params={
                "title": payload["title"],
                "ticket_codes": payload["ticket_codes"],
            },
            data=payload,
            entity={"type": "community_event", "id": payload.get("event_id", "")},
            options={},
        )
        return True

    async def notify_event_reminder(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
    ) -> None:
        """Remind confirmed bookers before event."""
        params = _event_title_params(event)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.reminder",
            params=params,
            data={
                "event_id": str(event["id"]),
                "title": params["title"],
            },
        )

    async def notify_event_cancelled(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
    ) -> None:
        """Notify resident when an event they booked is cancelled."""
        params = _event_title_params(event)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.cancelled",
            params=params,
            data={
                "event_id": str(event["id"]),
                "title": params["title"],
            },
        )
