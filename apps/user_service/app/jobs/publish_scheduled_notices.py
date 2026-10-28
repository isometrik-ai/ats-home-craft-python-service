"""Background jobs for notice board."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import BackgroundTasks

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.schemas.enums import NoticeCategory
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("publish_scheduled_notices")


async def publish_scheduled_notices(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str | None = None,
    project_id: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> list[str]:
    """Promote due scheduled notices to live, re-send due repeats, and push."""
    repo = NoticesRepository(db_connection=db_connection)
    sends = await repo.claim_due_notice_sends(
        organization_id=organization_id,
        project_id=project_id,
    )
    if not sends:
        return []
    sends_by_id = {str(send["id"]): send for send in sends}
    published_ids = list(sends_by_id)
    if background_tasks is not None:
        background_tasks.add_task(
            dispatch_due_notice_pushes,
            [_send_payload(send) for send in sends],
            db_connection,
        )
        return published_ids

    await _dispatch_claimed_pushes(db_connection, sends_by_id)
    return published_ids


def _send_payload(send: Any) -> dict[str, Any]:
    """Copy one claimed send so the push task does not keep the request connection."""
    return {
        "id": str(send["id"]),
        "fired_at": send.get("fired_at"),
        "repeating": bool(send.get("repeating")),
    }


async def dispatch_due_notice_pushes(
    sends: list[dict[str, Any]],
    db_connection: asyncpg.Connection,
) -> None:
    """Send publish pushes after the HTTP response, on the request connection.

    FastAPI runs this before the request transaction commits. A new connection
    cannot see the claimed notices yet.
    """
    if not sends:
        return
    try:
        sends_by_id = {str(send["id"]): send for send in sends}
        await _dispatch_claimed_pushes(db_connection, sends_by_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("due notice publish push failed")


async def _dispatch_claimed_pushes(
    db_connection: asyncpg.Connection,
    sends_by_id: dict[str, Any],
) -> None:
    """Resolve recipients and send one push per user for each claimed notice."""
    published_ids = list(sends_by_id)
    repo = NoticesRepository(db_connection=db_connection)
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
        unit_ids = await repo.list_unit_ids(
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
        unpaid_only = str(notice_row.get("category") or "") == NoticeCategory.INVOICE_UNPAID.value
        tower_ids = [str(tower["tower_id"]) for tower in towers]
        user_ids: set[str] = set(
            await recipient_service.resolve_recipient_user_ids(
                organization_id=org_id,
                project_id=proj_id,
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                scope_type=str(notice_row["scope_type"]),
                tower_ids=tower_ids,
                unit_ids=unit_ids,
                require_unpaid_invoice=unpaid_only,
            )
        )
        send = sends_by_id.get(notice_id) or {}
        occurrence_at = send.get("fired_at") if send.get("repeating") else None
        for user_id in user_ids:
            push_fields = NoticesService._notice_publish_push_fields(
                project_id=proj_id,
                notice_id=notice_id,
                recipient_user_id=user_id,
                occurrence_at=occurrence_at,
                is_flash=bool(notice_row.get("is_flash")),
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


async def expire_notice_pins(db_connection: asyncpg.Connection) -> int:
    """Deactivate banner pins past their expiry time."""
    repo = NoticesRepository(db_connection=db_connection)
    return await repo.expire_due_pins()
