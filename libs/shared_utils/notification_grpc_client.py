"""Async gRPC client for notification-service SendNotification."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import grpc

from libs.grpc_stubs.notification import (
    notification_service_pb2,
    notification_service_pb2_grpc,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("notification_grpc_client")


@dataclass(frozen=True)
class _GrpcBinding:
    """Cached gRPC channel and stub callables for a target."""

    channel: grpc.aio.Channel
    send_notification: Callable[..., Awaitable[Any]]
    notification_request: type


class NotificationGrpcClient:
    """Send push notification payloads to notification-service over gRPC."""

    def __init__(self) -> None:
        self._bindings: dict[str, _GrpcBinding] = {}

    async def _get_binding(self) -> _GrpcBinding:
        """Return cached gRPC binding for the configured notification target."""
        target = shared_settings.notification.grpc_target.strip()
        if not target:
            raise ValueError("NOTIFICATION_GRPC_TARGET is not configured")

        cached = self._bindings.get(target)
        if cached is not None:
            return cached

        channel = grpc.aio.insecure_channel(target)
        stub = notification_service_pb2_grpc.GreeterStub(channel)
        binding = _GrpcBinding(
            channel=channel,
            send_notification=stub.SendNotification,
            notification_request=notification_service_pb2.NotificationRequest,
        )
        self._bindings[target] = binding
        return binding

    async def close(self) -> None:
        """Close all cached gRPC channels."""
        for binding in self._bindings.values():
            await binding.channel.close()
        self._bindings.clear()

    async def send_notification(self, payload: dict[str, Any]) -> bool:
        """Send a notification payload; return True when accepted by notification-service."""
        if not shared_settings.notification.enabled:
            return True

        body_data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        timeout_s = max(0.001, shared_settings.notification.grpc_timeout_ms / 1000.0)

        try:
            binding = await self._get_binding()
            request = binding.notification_request(body_data=body_data)
            response = await binding.send_notification(request, timeout=timeout_s)
            return bool(response.message)
        except Exception as exc:
            logger.error("notification_grpc_send_failed error=%s", exc)
            if shared_settings.notification.raise_on_failure:
                raise
            return False


notification_grpc_client = NotificationGrpcClient()
