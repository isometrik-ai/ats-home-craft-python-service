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
    return published_ids


async def expire_notice_pins(db_connection: asyncpg.Connection) -> int:
    """Deactivate banner pins past their expiry time."""
    repo = NoticesRepository(db_connection=db_connection)
    return await repo.expire_due_pins()


async def process_recurring_notices(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str | None = None,
    project_id: str | None = None,
) -> list[str]:
    """Deliver due recurring notices (same row each send) and dispatch push."""
    repo = NoticesRepository(db_connection=db_connection)
    notice_ids = await repo.list_due_recurring_notice_ids(
        organization_id=organization_id,
        project_id=project_id,
    )
    if not notice_ids:
        return []

    push_dispatcher = PushNotificationDispatcher(db_connection=db_connection)
    recipient_service = NoticeRecipientResolutionService(db_connection=db_connection)
    delivered: list[str] = []

    for notice_id in notice_ids:
        if organization_id:
            row = await repo.fetch_notice_by_id_only(
                organization_id=organization_id,
                notice_id=notice_id,
            )
        else:
            row = await _fetch_recurring_notice_any_org(repo, notice_id=notice_id)
        if row is None or not row.get("series_frequency"):
            continue
        org_id = str(row["organization_id"])
        if row.get("series_repeat_ended_at") is not None:
            continue
        ok = await repo.deliver_due_recurring_notice(
            organization_id=org_id,
            notice_id=notice_id,
            project_id=str(row["project_id"]),
        )
        if not ok:
            continue
        delivered.append(notice_id)

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
        user_ids: set[str] = set(
            await recipient_service.resolve_recipient_user_ids(
                organization_id=org_id,
                project_id=str(row["project_id"]),
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                scope_type=str(row["scope_type"]),
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
                project_id=str(row["project_id"]),
                notice_id=notice_id,
                recipient_user_id=user_id,
            )
            await push_dispatcher.send_to_user(
                organization_id=org_id,
                recipient_user_id=user_id,
                message_key="notifications.push.notices.published",
                notification_type="notice_published",
                feed_type="notices",
                params={"title": str(row["title"])},
                data=push_fields["data"],
                entity=push_fields["entity"],
                options=push_fields["options"],
            )
    return delivered


async def _fetch_recurring_notice_any_org(
    repo: NoticesRepository,
    *,
    notice_id: str,
) -> dict | None:
    """Load notice + series when cron runs org-wide."""
    row = await repo.db_connection.fetchrow(
        """
        SELECT
          n.id::text AS id,
          n.organization_id::text AS organization_id,
          n.project_id::text AS project_id,
          n.title,
          n.scope_type::text AS scope_type,
          ns.frequency::text AS series_frequency,
          ns.monthly_day AS series_monthly_day,
          ns.starts_on AS series_starts_on,
          ns.time_of_day AS series_time_of_day,
          ns.end_type::text AS series_end_type,
          ns.end_on AS series_end_on,
          ns.end_after_sends AS series_end_after_sends,
          ns.sends_completed AS series_sends_completed,
          ns.next_send_at AS series_next_send_at,
          ns.repeat_ended_at AS series_repeat_ended_at
        FROM notices n
        JOIN notice_series ns ON ns.notice_id = n.id
        WHERE n.id = $1::uuid
        """,
        notice_id,
    )
    return dict(row) if row else None
