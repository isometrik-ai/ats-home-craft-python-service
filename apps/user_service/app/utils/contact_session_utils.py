"""Portal session helpers for property-management contacts."""

from __future__ import annotations

import asyncpg

from libs.shared_utils.session_context_cache import (
    revoke_org_member_sessions_everywhere,
)


async def revoke_contact_portal_sessions(
    *,
    db_connection: asyncpg.Connection,
    organization_id: str,
    user_id: str | None,
) -> None:
    """Revoke org-scoped login sessions for a contact portal user."""
    if not user_id or not organization_id:
        return
    await revoke_org_member_sessions_everywhere(
        db_connection=db_connection,
        user_id=str(user_id),
        organization_id=str(organization_id),
    )
