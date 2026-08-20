"""Send 24h-before-event push reminders to confirmed bookers."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.db.repositories.community_events_repository import (
    CommunityEventsRepository,
)
from apps.user_service.app.services.community_event_notification_service import (
    CommunityEventNotificationService,
)


async def send_community_event_reminders(
    db_connection: asyncpg.Connection,
    *,
    hours_ahead: int = 24,
) -> dict[str, int]:
    """Notify confirmed bookers for events starting within the reminder window."""
    repo = CommunityEventsRepository(db_connection)
    notifications = CommunityEventNotificationService(db_connection=db_connection)
    events = await repo.list_events_due_for_reminder(hours_ahead=hours_ahead)
    sent = 0
    for event in events:
        bookers = await repo.list_confirmed_bookers_for_reminder(
            organization_id=str(event["organization_id"]),
            event_id=str(event["id"]),
        )
        for booker in bookers:
            await notifications.notify_event_reminder(
                organization_id=str(event["organization_id"]),
                contact_id=str(booker["contact_id"]),
                event=event,
            )
            sent += 1
    return {"events_processed": len(events), "reminders_sent": sent}
