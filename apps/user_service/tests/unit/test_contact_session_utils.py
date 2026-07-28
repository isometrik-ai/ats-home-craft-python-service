"""Unit tests for contact portal session helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.utils.contact_session_utils import (
    revoke_contact_portal_sessions,
)


@pytest.mark.asyncio
async def test_revoke_contact_portal_sessions_skips_without_user_id():
    """No-op when the contact has no linked auth user."""
    with patch(
        "apps.user_service.app.utils.contact_session_utils.revoke_org_member_sessions_everywhere",
        new=AsyncMock(),
    ) as revoke:
        await revoke_contact_portal_sessions(
            db_connection=MagicMock(),
            organization_id="org-1",
            user_id=None,
        )

    revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_contact_portal_sessions_revokes_org_sessions():
    """Revokes org-scoped sessions for the contact portal user."""
    conn = MagicMock()
    with patch(
        "apps.user_service.app.utils.contact_session_utils.revoke_org_member_sessions_everywhere",
        new=AsyncMock(),
    ) as revoke:
        await revoke_contact_portal_sessions(
            db_connection=conn,
            organization_id="org-1",
            user_id="user-1",
        )

    revoke.assert_awaited_once_with(
        db_connection=conn,
        user_id="user-1",
        organization_id="org-1",
    )
