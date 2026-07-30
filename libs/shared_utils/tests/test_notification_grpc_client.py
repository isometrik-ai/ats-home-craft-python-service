"""Unit tests for NotificationGrpcClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_utils.notification_grpc_client import NotificationGrpcClient


@pytest.mark.asyncio
async def test_send_notification_disabled_returns_true():
    client = NotificationGrpcClient()
    with patch("libs.shared_utils.notification_grpc_client.shared_settings") as mock_settings:
        mock_settings.notification.enabled = False
        assert await client.send_notification({"x": 1}) is True


@pytest.mark.asyncio
async def test_send_notification_calls_stub():
    client = NotificationGrpcClient()

    fake_send = AsyncMock(return_value=type("Resp", (), {"message": "ok"})())
    fake_binding = MagicMock(
        send_notification=fake_send,
        notification_request=MagicMock(side_effect=lambda **kwargs: type("Req", (), kwargs)()),
    )

    with (
        patch("libs.shared_utils.notification_grpc_client.shared_settings") as mock_settings,
        patch.object(client, "_get_binding", new=AsyncMock(return_value=fake_binding)),
    ):
        mock_settings.notification.enabled = True
        mock_settings.notification.grpc_timeout_ms = 1000

        ok = await client.send_notification({"hello": "world"})

    assert ok is True
    args, kwargs = fake_send.await_args
    assert kwargs["timeout"] == 1.0
    body_data = args[0].body_data
    assert json.loads(body_data) == {"hello": "world"}


@pytest.mark.asyncio
async def test_send_notification_handles_exception_when_not_raising():
    client = NotificationGrpcClient()

    fake_send = AsyncMock(side_effect=RuntimeError("down"))
    fake_binding = MagicMock(
        send_notification=fake_send,
        notification_request=MagicMock(side_effect=lambda **kwargs: type("Req", (), kwargs)()),
    )

    with (
        patch("libs.shared_utils.notification_grpc_client.shared_settings") as mock_settings,
        patch.object(client, "_get_binding", new=AsyncMock(return_value=fake_binding)),
    ):
        mock_settings.notification.enabled = True
        mock_settings.notification.grpc_timeout_ms = 10
        mock_settings.notification.raise_on_failure = False

        ok = await client.send_notification({"x": 1})

    assert ok is False


@pytest.mark.asyncio
async def test_send_notification_raises_when_configured():
    client = NotificationGrpcClient()

    fake_send = AsyncMock(side_effect=RuntimeError("down"))
    fake_binding = MagicMock(
        send_notification=fake_send,
        notification_request=MagicMock(side_effect=lambda **kwargs: type("Req", (), kwargs)()),
    )

    with (
        patch("libs.shared_utils.notification_grpc_client.shared_settings") as mock_settings,
        patch.object(client, "_get_binding", new=AsyncMock(return_value=fake_binding)),
    ):
        mock_settings.notification.enabled = True
        mock_settings.notification.grpc_timeout_ms = 10
        mock_settings.notification.raise_on_failure = True

        with pytest.raises(RuntimeError, match="down"):
            await client.send_notification({"x": 1})


@pytest.mark.asyncio
async def test_client_close_closes_channel():
    client = NotificationGrpcClient()

    fake_channel = AsyncMock()
    fake_stub = AsyncMock()
    fake_stub.SendNotification = AsyncMock()

    with (
        patch(
            "libs.shared_utils.notification_grpc_client.grpc.aio.insecure_channel",
            return_value=fake_channel,
        ),
        patch(
            "libs.shared_utils.notification_grpc_client.notification_service_pb2_grpc.GreeterStub",
            return_value=fake_stub,
        ),
        patch("libs.shared_utils.notification_grpc_client.shared_settings") as mock_settings,
    ):
        mock_settings.notification.grpc_target = "localhost:50051"
        await client._get_binding()
        await client.close()

    fake_channel.close.assert_awaited_once()
