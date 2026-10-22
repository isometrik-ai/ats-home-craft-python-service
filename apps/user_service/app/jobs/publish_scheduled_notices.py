"""Background jobs for notice board."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from apps.user_service.app.utils.common_utils import UserContext


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

    if published_ids:
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
        contact_ids = await repo.list_contact_recipient_ids(
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
        user_ids: set[str] = set(
            await recipient_service.resolve_recipient_user_ids(
                organization_id=org_id,
                project_id=proj_id,
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                scope_type=str(notice_row["scope_type"]),
                tower_ids=[str(t["tower_id"]) for t in towers],
            )
        )
        if contact_ids:
            user_ids.update(
                await recipient_service.resolve_user_ids_for_contact_ids(
                    organization_id=org_id,
                    contact_ids=contact_ids,
                )
            )
        for user_id in user_ids:
            push_fields = NoticesService._notice_publish_push_fields(
                project_id=proj_id,
                notice_id=notice_id,
                recipient_user_id=user_id,
            )
            await push_dispatcher.send_to_user(
                organization_id=org_id,
                recipient_user_id=user_id,
                message_key="notifications.push.notices.published",
                notification_type="notice_published",
                feed_type="notices",
                params={"title": str(notice["title"])},
                data=push_fields["data"],
                entity=push_fields["entity"],
                options=push_fields["options"],
            )
    recurring_ids = await materialize_due_recurring_notices(
        db_connection,
        organization_id=organization_id,
        project_id=project_id,
    )
    return published_ids + recurring_ids


async def materialize_due_recurring_notices(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str | None = None,
    project_id: str | None = None,
) -> list[str]:
    """Create live notices from due recurring templates (one per template per run)."""
    repo = NoticesRepository(db_connection=db_connection)
    templates = await repo.lock_due_recurring_templates(
        organization_id=organization_id,
        project_id=project_id,
        limit=50,
    )
    instance_ids: list[str] = []
    for template in templates:
        org_id = str(template["organization_id"])
        service = NoticesService(
            db_connection=db_connection,
            user_context=UserContext(
                user_id="system",
                email="",
                organization_id=org_id,
            ),
        )
        instance_id = await service._materialize_recurring_template(template)
        if instance_id:
            instance_ids.append(instance_id)
    return instance_ids


async def expire_notice_pins(db_connection: asyncpg.Connection) -> int:
    """Deactivate banner pins past their expiry time."""
    repo = NoticesRepository(db_connection=db_connection)
    return await repo.expire_due_pins()
