"""Background jobs for notice board."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)


async def publish_scheduled_notices(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str | None = None,
    project_id: str | None = None,
) -> list[str]:
    """Promote due scheduled notices to live and dispatch push notifications."""
    repo = NoticesRepository(db_connection=db_connection)
    published_ids = await repo.publish_due_scheduled_notices(
        organization_id=organization_id,
        project_id=project_id,
    )
    if not published_ids:
        return []

    recipient_service = NoticeRecipientResolutionService(db_connection=db_connection)
    push_dispatcher = PushNotificationDispatcher(db_connection=db_connection)

    for notice_id in published_ids:
        rows = await repo.list_notices_published_since(notice_ids=[notice_id])
        if not rows:
            continue
        notice = rows[0]
        org_id = str(notice["organization_id"])
        proj_id = str(notice["project_id"])
        recipient_groups = await repo.list_recipient_groups(
            organization_id=org_id,
            notice_id=notice_id,
        )
        towers = await repo.list_towers_for_notice(
            organization_id=org_id,
            notice_id=notice_id,
        )
        notice_row = await repo.fetch_notice_by_id(
            organization_id=org_id,
            project_id=proj_id,
            notice_id=notice_id,
        )
        if notice_row is None:
            continue
        user_ids = await recipient_service.resolve_recipient_user_ids(
            organization_id=org_id,
            project_id=proj_id,
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            scope_type=str(notice_row["scope_type"]),
            tower_ids=[str(t["tower_id"]) for t in towers],
        )
        for user_id in user_ids:
            await push_dispatcher.send_to_user(
                organization_id=org_id,
                recipient_user_id=user_id,
                message_key="notifications.push.notices.published",
                notification_type="notice_published",
                feed_type="notices",
                template_params={"title": str(notice["title"])},
            )
    return published_ids


async def expire_notice_pins(db_connection: asyncpg.Connection) -> int:
    """Deactivate banner pins past their expiry time."""
    repo = NoticesRepository(db_connection=db_connection)
    return await repo.expire_due_pins()
