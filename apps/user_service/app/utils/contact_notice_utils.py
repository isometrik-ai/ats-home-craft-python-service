"""Notice-board helpers for property-management contacts."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository


async def purge_contact_notice_likes(
    *,
    db_connection: asyncpg.Connection,
    organization_id: str,
    contact_id: str,
) -> None:
    """Remove notice likes owned by a contact and reconcile like counts."""
    if not organization_id or not contact_id:
        return
    notices_repo = NoticesRepository(db_connection)
    await notices_repo.delete_all_likes_for_contact(
        organization_id=str(organization_id),
        contact_id=str(contact_id),
    )
