"""Unit tests for PushNotificationDispatcher helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
    contact_display_name,
    recipient_language_from_contact,
    unit_label_from_row,
)
from apps.user_service.app.services.push_notification_service import (
    PushNotificationSendResult,
    PushNotificationSendStatus,
)


def test_recipient_language_from_contact_defaults_and_json():
    assert recipient_language_from_contact(None) == "en"
    assert recipient_language_from_contact('{"preferred_language": "hi"}') == "hi"
    assert recipient_language_from_contact("not-json") == "en"
    assert recipient_language_from_contact(["bad"]) == "en"
    assert recipient_language_from_contact({"preferred_language": "  "}) == "en"


def test_contact_display_name_and_unit_label():
    assert contact_display_name({"prefix": "Mr.", "first_name": "Amit", "last_name": "Shah"})
    assert unit_label_from_row({"unit_label": "A-2102"}) == "A-2102"
    assert unit_label_from_row({"unit_code": "B-101"}) == "B-101"
    assert unit_label_from_row({"unit_id": "unit-1"}) == "unit-1"
    assert unit_label_from_row({}) == "your flat"


@pytest.mark.asyncio
async def test_send_to_user_delegates_to_push_service():
    push_service = MagicMock()
    push_service.send = AsyncMock(
        return_value=PushNotificationSendResult(status=PushNotificationSendStatus.SENT)
    )
    dispatcher = PushNotificationDispatcher(
        db_connection=MagicMock(),
        push_service=push_service,
    )

    result = await dispatcher.send_to_user(
        organization_id="org-1",
        recipient_user_id="user-1",
        message_key="notifications.push.test",
        notification_type="test",
    )
    assert result.status == PushNotificationSendStatus.SENT
    push_service.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_contact_missing_user_returns_none():
    dispatcher = PushNotificationDispatcher(db_connection=MagicMock())
    dispatcher.contacts_repo.get_contact_for_update = AsyncMock(return_value=None)

    assert (
        await dispatcher.send_to_contact(
            organization_id="org-1",
            contact_id="contact-1",
            message_key="k",
            notification_type="t",
        )
        is None
    )

    dispatcher.contacts_repo.get_contact_for_update = AsyncMock(
        return_value={"user_id": None, "additional_data": None}
    )
    assert (
        await dispatcher.send_to_contact(
            organization_id="org-1",
            contact_id="contact-1",
            message_key="k",
            notification_type="t",
        )
        is None
    )


@pytest.mark.asyncio
async def test_send_to_contact_success():
    dispatcher = PushNotificationDispatcher(db_connection=MagicMock())
    dispatcher.contacts_repo.get_contact_for_update = AsyncMock(
        return_value={
            "user_id": "user-1",
            "additional_data": {"preferred_language": "hi"},
        }
    )
    dispatcher.push_service = MagicMock()
    dispatcher.push_service.send = AsyncMock(
        return_value=PushNotificationSendResult(status=PushNotificationSendStatus.SENT)
    )

    result = await dispatcher.send_to_contact(
        organization_id="org-1",
        contact_id="contact-1",
        message_key="k",
        notification_type="t",
    )
    assert result.status == PushNotificationSendStatus.SENT
    _, kwargs = dispatcher.push_service.send.await_args
    assert kwargs["language"] == "hi"


@pytest.mark.asyncio
async def test_send_to_unit_residents_counts_sent_and_skips_excluded():
    dispatcher = PushNotificationDispatcher(db_connection=MagicMock())
    dispatcher.contacts_repo.list_unit_resident_recipients = AsyncMock(
        return_value=[
            {"user_id": "user-1", "contact_id": "c1", "additional_data": None},
            {"user_id": "user-2", "contact_id": "c2", "additional_data": None},
            {"user_id": "", "contact_id": "c3", "additional_data": None},
        ]
    )
    dispatcher.push_service = MagicMock()
    dispatcher.push_service.send = AsyncMock(
        side_effect=[
            PushNotificationSendResult(status=PushNotificationSendStatus.SENT),
            PushNotificationSendResult(status=PushNotificationSendStatus.SKIPPED),
        ]
    )

    sent = await dispatcher.send_to_unit_residents(
        organization_id="org-1",
        unit_id="unit-1",
        message_key="k",
        notification_type="t",
        exclude_user_ids={"user-2"},
        exclude_contact_ids={"c3"},
    )
    assert sent == 1


@pytest.mark.asyncio
async def test_send_to_org_members():
    dispatcher = PushNotificationDispatcher(db_connection=MagicMock())
    dispatcher.org_members_repo.list_active_member_user_ids = AsyncMock(
        return_value=["user-1", "user-2"]
    )
    dispatcher.push_service = MagicMock()
    dispatcher.push_service.send = AsyncMock(
        return_value=PushNotificationSendResult(status=PushNotificationSendStatus.SENT)
    )

    sent = await dispatcher.send_to_org_members(
        organization_id="org-1",
        message_key="k",
        notification_type="t",
    )
    assert sent == 2


@pytest.mark.asyncio
async def test_send_to_contact_unit_primary():
    dispatcher = PushNotificationDispatcher(db_connection=MagicMock())
    dispatcher.contacts_repo.get_push_recipient_for_contact_unit = AsyncMock(return_value=None)
    assert (
        await dispatcher.send_to_contact_unit_primary(
            organization_id="org-1",
            contact_unit_id="cu-1",
            message_key="k",
            notification_type="t",
        )
        is None
    )

    dispatcher.contacts_repo.get_push_recipient_for_contact_unit = AsyncMock(
        return_value={"user_id": "user-1", "additional_data": None}
    )
    dispatcher.push_service = MagicMock()
    dispatcher.push_service.send = AsyncMock(
        return_value=PushNotificationSendResult(status=PushNotificationSendStatus.SENT)
    )
    result = await dispatcher.send_to_contact_unit_primary(
        organization_id="org-1",
        contact_unit_id="cu-1",
        message_key="k",
        notification_type="t",
    )
    assert result.status == PushNotificationSendStatus.SENT
