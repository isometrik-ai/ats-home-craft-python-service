"""Background jobs for notice board."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.services.notices_service import dispatch_notice_publish_push


async def publish_scheduled_notices(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str | None = None,
    project_id: str | None = None,
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

    for notice_id in published_ids:
        rows = await repo.list_notices_published_since(notice_ids=[notice_id])
        if not rows:
            continue
        notice = rows[0]
        org_id = str(notice["organization_id"])
        proj_id = str(notice["project_id"])
        send = sends_by_id.get(notice_id) or {}
        occurrence_at = send.get("fired_at") if send.get("repeating") else None
        await dispatch_notice_publish_push(
            db_connection,
            organization_id=org_id,
            project_id=proj_id,
            notice_id=notice_id,
            title=str(notice["title"]),
            occurrence_at=occurrence_at,
            notice_row=notice,
        )
    return published_ids


async def expire_notice_pins(db_connection: asyncpg.Connection) -> int:
    """Deactivate banner pins past their expiry time."""
    repo = NoticesRepository(db_connection=db_connection)
    return await repo.expire_due_pins()
