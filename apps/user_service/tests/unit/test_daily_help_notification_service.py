"""Unit tests for DailyHelpNotificationService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.daily_help_notification_service import (
    DailyHelpNotificationService,
)
from apps.user_service.app.services.push_notification_service import (
    PushNotificationSendResult,
    PushNotificationSendStatus,
)


@pytest.mark.asyncio
async def test_notify_skips_when_no_household_links():
    """No active links means zero notifications."""
    svc = DailyHelpNotificationService(db_connection=MagicMock())
    svc.daily_help_repo = MagicMock()
    svc.daily_help_repo.list_active_links_for_profile = AsyncMock(return_value=[])

    sent = await svc.notify_linked_unit_holders(
        organization_id="org-1",
        profile_id="profile-1",
        pass_id="pass-1",
        event_id="evt-1",
        message_key="notifications.push.daily_help.checked_in",
        idempotency_suffix="checked_in",
        helper_name="Lakshmi",
        project_id="project-1",
    )
    assert sent == 0


@pytest.mark.asyncio
async def test_notify_owner_and_tenant_dedupes_by_user():
    """Owner and tenant with same user_id receive one push."""
    svc = DailyHelpNotificationService(db_connection=MagicMock())
    svc.daily_help_repo = MagicMock()
    svc.daily_help_repo.list_active_links_for_profile = AsyncMock(
        return_value=[{"unit_id": "unit-1"}]
    )
    svc.units_repo = MagicMock()
    svc.units_repo.get_unit_role_occupants_batch = AsyncMock(
        return_value={
            "unit-1": {
                "owner": {"contact_id": "contact-1"},
                "tenant": {"contact_id": "contact-1"},
            }
        }
    )
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_for_update = AsyncMock(
        return_value={
            "user_id": "user-1",
            "additional_data": {"preferred_language": "en"},
        }
    )
    mock_push = MagicMock()
    mock_push.send_to_user = AsyncMock(
        return_value=PushNotificationSendResult(status=PushNotificationSendStatus.SENT)
    )
    svc._push_dispatcher = mock_push

    sent = await svc.notify_linked_unit_holders(
        organization_id="org-1",
        profile_id="profile-1",
        pass_id="pass-1",
        event_id="evt-1",
        message_key="notifications.push.daily_help.checked_in",
        idempotency_suffix="checked_in",
        helper_name="Lakshmi",
    )

    assert sent == 1
    mock_push.send_to_user.assert_awaited_once()
