"""Unit tests for PushNotificationService."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.user_service.app.services.push_notification_service import (
    PushNotificationSendResult,
    PushNotificationSendStatus,
    PushNotificationService,
    PushNotificationSkipReason,
    _contact_push_enabled,
    resolve_push_copy,
)


class _FakePushTokensRepo:
    def __init__(self, tokens: list[str] | None = None):
        self.tokens = tokens or []
        self.list_calls = []

    async def list_push_tokens_for_user(self, *, organization_id, user_id):
        self.list_calls.append((organization_id, user_id))
        return list(self.tokens)


class _FakeContactsRepo:
    def __init__(self, contact: dict | None = None):
        self.contact = contact
        self.get_calls = []

    async def get_active_contact_by_user_id(self, *, user_id, organization_id):
        self.get_calls.append((user_id, organization_id))
        return self.contact


class _FakeGrpcClient:
    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.payloads: list[dict] = []

    async def send_notification(self, payload):
        self.payloads.append(payload)
        return self.accepted


def _service(
    *,
    tokens: list[str] | None = None,
    contact: dict | None = None,
    grpc_accepted: bool = True,
) -> PushNotificationService:
    service = PushNotificationService(db_connection=None)
    service.push_tokens_repo = _FakePushTokensRepo(tokens=tokens)
    service.contacts_repo = _FakeContactsRepo(contact=contact)
    service.grpc_client = _FakeGrpcClient(accepted=grpc_accepted)
    return service


def test_contact_push_enabled_parses_json_string():
    assert _contact_push_enabled('{"push": true}') is True
    assert _contact_push_enabled('{"push": false}') is False
    assert _contact_push_enabled('{"email": true}') is None


def test_resolve_push_copy_uses_translator():
    with patch(
        "apps.user_service.app.services.push_notification_service.translator.get",
        side_effect=lambda key, lang, **params: f"{key}:{params.get('unit_label', '')}",
    ):
        title, body = resolve_push_copy(
            "notifications.push.walk_in.awaiting",
            language="en",
            params={"unit_label": "A-2102"},
        )

    assert title == "notifications.push.walk_in.awaiting.title:A-2102"
    assert body == "notifications.push.walk_in.awaiting.body:A-2102"


def test_resolve_push_copy_allows_overrides():
    title, body = resolve_push_copy(
        "notifications.push.walk_in.awaiting",
        title="Custom title",
        body="Custom body",
    )
    assert title == "Custom title"
    assert body == "Custom body"


@pytest.mark.asyncio
async def test_send_skips_when_disabled():
    service = _service(tokens=["token-1"])
    with patch(
        "apps.user_service.app.services.push_notification_service.shared_settings"
    ) as mock_settings:
        mock_settings.notification.enabled = False
        result = await service.send(
            organization_id="org-1",
            recipient_user_id="user-1",
            message_key="notifications.push.walk_in.awaiting",
            notification_type="NOTIFICATION_TYPE_WALK_IN",
        )

    assert result == PushNotificationSendResult(
        status=PushNotificationSendStatus.SKIPPED,
        skip_reason=PushNotificationSkipReason.DISABLED,
    )
    assert service.grpc_client.payloads == []


@pytest.mark.asyncio
async def test_send_skips_when_push_preference_off():
    service = _service(
        tokens=["token-1"],
        contact={"communication_preferences": {"push": False, "email": True}},
    )
    with patch(
        "apps.user_service.app.services.push_notification_service.shared_settings"
    ) as mock_settings:
        mock_settings.notification.enabled = True
        result = await service.send(
            organization_id="org-1",
            recipient_user_id="user-1",
            message_key="notifications.push.walk_in.awaiting",
            notification_type="NOTIFICATION_TYPE_WALK_IN",
        )

    assert result.skip_reason == PushNotificationSkipReason.PREFERENCE_OFF
    assert service.grpc_client.payloads == []


@pytest.mark.asyncio
async def test_send_allows_when_no_contact():
    service = _service(tokens=["token-1"], contact=None)
    with patch(
        "apps.user_service.app.services.push_notification_service.shared_settings"
    ) as mock_settings:
        mock_settings.notification.enabled = True
        mock_settings.isometrik.client_name = "tenant-abc"
        result = await service.send(
            organization_id="org-1",
            recipient_user_id="user-1",
            message_key="notifications.push.walk_in.awaiting",
            notification_type="NOTIFICATION_TYPE_WALK_IN",
            params={"unit_label": "A-2102"},
            data={"screen": "walk_in_detail"},
            entity={"kind": "walk_in", "id": "entry-1"},
        )

    assert result.status == PushNotificationSendStatus.SENT
    assert result.token_count == 1
    payload = service.grpc_client.payloads[0]
    assert payload["tenant_id"] == "tenant-abc"
    assert payload["project_id"] == "org-1"
    assert payload["user_id"] == "user-1"
    assert payload["tokens"] == ["token-1"]
    assert payload["type"] == "NOTIFICATION_TYPE_WALK_IN"
    assert payload["options"]["save_to_db"] is True


@pytest.mark.asyncio
async def test_send_skips_when_no_tokens():
    service = _service(tokens=[], contact={"communication_preferences": {"push": True}})
    with patch(
        "apps.user_service.app.services.push_notification_service.shared_settings"
    ) as mock_settings:
        mock_settings.notification.enabled = True
        result = await service.send(
            organization_id="org-1",
            recipient_user_id="user-1",
            message_key="notifications.push.walk_in.awaiting",
            notification_type="NOTIFICATION_TYPE_WALK_IN",
        )

    assert result.skip_reason == PushNotificationSkipReason.NO_TOKENS


@pytest.mark.asyncio
async def test_send_reports_grpc_failure():
    service = _service(
        tokens=["token-1"],
        contact={"communication_preferences": {"push": True}},
        grpc_accepted=False,
    )
    with patch(
        "apps.user_service.app.services.push_notification_service.shared_settings"
    ) as mock_settings:
        mock_settings.notification.enabled = True
        mock_settings.isometrik.client_name = "tenant-abc"
        with patch(
            "apps.user_service.app.services.push_notification_service.resolve_push_copy",
            return_value=("Title", "Body"),
        ):
            result = await service.send(
                organization_id="org-1",
                recipient_user_id="user-1",
                message_key="notifications.push.walk_in.awaiting",
                notification_type="NOTIFICATION_TYPE_WALK_IN",
            )

    assert result.skip_reason == PushNotificationSkipReason.GRPC_FAILED
    assert result.token_count == 1
