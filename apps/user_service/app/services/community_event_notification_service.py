"""Push notifications for community events."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from apps.user_service.app.utils.community_event_email_helpers import (
    build_booking_email_body_context,
    build_ticket_attachments,
    contact_booking_email,
)
from apps.user_service.app.utils.email_utils import send_community_event_booking_email
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("community_event_notifications")


def _event_title_params(event: dict[str, Any]) -> dict[str, str]:
    """Template params for community event push copy."""
    title = str(event.get("title") or "").strip()
    return {"title": title or "Community event"}


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
        """Notify resident on confirmed booking (push + ticket email)."""
        params = _event_title_params(event)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.booking_confirmed",
            params=params,
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": params["title"],
            },
        )
        await self._send_booking_email(
            organization_id=organization_id,
            contact_id=contact_id,
            event=event,
            booking=booking,
            template="community_event_booking_confirmed",
        )

    async def send_ticket_email(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> str:
        """Send one confirmation email with tickets. No push. Raises if it cannot send."""
        email = await self._send_booking_email(
            organization_id=organization_id,
            contact_id=contact_id,
            event=event,
            booking=booking,
            template="community_event_booking_confirmed",
            require_send=True,
        )
        if not email:
            raise ValidationException(
                message_key="community_events.errors.ticket_email_not_sent",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return email

    async def notify_booking_waitlisted(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> None:
        """Notify resident when waitlisted (push + email, no ticket PDF)."""
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
        await self._send_booking_email(
            organization_id=organization_id,
            contact_id=contact_id,
            event=event,
            booking=booking,
            template="community_event_booking_waitlisted",
        )

    async def notify_waitlist_promoted(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
    ) -> None:
        """Notify resident when promoted from waitlist (push + ticket email)."""
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
        await self._send_booking_email(
            organization_id=organization_id,
            contact_id=contact_id,
            event=event,
            booking=booking,
            template="community_event_booking_confirmed",
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
        params = _event_title_params(event)
        await self._send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key="notifications.push.community_events.payment_received",
            params=params,
            data={
                "event_id": str(event["id"]),
                "booking_id": str(booking["id"]),
                "title": params["title"],
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

    async def _send_booking_email(
        self,
        *,
        organization_id: str,
        contact_id: str,
        event: dict[str, Any],
        booking: dict[str, Any],
        template: str,
        require_send: bool = False,
    ) -> str | None:
        """Send booking email. Failures must not block the booking flow unless required."""
        try:
            contact = await self.contacts_repo.get_contact_details(
                organization_id=organization_id,
                contact_id=contact_id,
            )
            email = contact_booking_email(contact)
            if not email or not contact:
                logger.info(
                    "community event booking email skipped (no address) contact_id=%s",
                    contact_id,
                )
                if require_send:
                    raise ValidationException(
                        message_key="community_events.errors.resident_email_missing",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
                return None
            project_name = await self._project_name(
                organization_id=organization_id,
                project_id=str(event.get("project_id") or booking.get("project_id") or ""),
            )
            body_context = build_booking_email_body_context(
                contact=contact,
                event=event,
                booking=booking,
                project_name=project_name,
            )
            attachments: list[dict[str, str]] = []
            if template == "community_event_booking_confirmed":
                attachments = build_ticket_attachments(booking=booking, event=event)
            sent = send_community_event_booking_email(
                email=email,
                template=template,
                body_context=body_context,
                attachments=attachments or None,
            )
            if not sent:
                if require_send:
                    raise ValidationException(
                        message_key="community_events.errors.ticket_email_not_sent",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
                return None
            return email
        except ValidationException:
            raise
        except Exception as exc:
            logger.warning("community event booking email failed: %s", exc)
            if require_send:
                raise ValidationException(
                    message_key="community_events.errors.ticket_email_not_sent",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                ) from exc
            return None

    async def _project_name(self, *, organization_id: str, project_id: str) -> str:
        """Project display name for email copy."""
        if not project_id:
            return ""
        row = await self.db_connection.fetchrow(
            """
            SELECT name
            FROM projects
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return str(row["name"]) if row and row.get("name") else ""
