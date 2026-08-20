"""Push notifications for community events."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("community_event_notifications")


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
        data = {
            "event_id": str(event["id"]),
            "title": str(event.get("title") or ""),
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
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.booking_confirmed",
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": str(event.get("title") or ""),
            },
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
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.booking_waitlisted",
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": str(event.get("title") or ""),
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
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.waitlist_promoted",
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": str(event.get("title") or ""),
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
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.payment_received",
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": str(event.get("title") or ""),
            },
        )

    async def notify_event_reminder(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
    ) -> None:
        """Remind confirmed bookers before event."""
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.reminder",
            data={
                "event_id": str(event["id"]),
                "title": str(event.get("title") or ""),
            },
        )
