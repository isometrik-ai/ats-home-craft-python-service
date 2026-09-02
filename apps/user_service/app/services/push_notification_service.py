"""Push notification delivery via notification-service gRPC."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.user_push_tokens_repository import (
    UserPushTokensRepository,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger
from libs.shared_utils.notification_grpc_client import NotificationGrpcClient
from libs.shared_utils.translations import translator

logger = get_logger("push_notification_service")


class PushNotificationSkipReason(str, Enum):
    """Why a push send was not attempted or did not reach notification-service."""

    DISABLED = "disabled"
    PREFERENCE_OFF = "preference_off"
    NO_TOKENS = "no_tokens"
    GRPC_FAILED = "grpc_failed"


class PushNotificationSendStatus(str, Enum):
    """Outcome of a push notification send attempt."""

    SENT = "sent"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class PushNotificationSendResult:
    """Result of attempting to deliver a push notification."""

    status: PushNotificationSendStatus
    skip_reason: PushNotificationSkipReason | None = None
    token_count: int = 0


def _contact_push_enabled(raw_preferences: Any) -> bool | None:
    """Return push preference when contact preferences are present; else None."""
    if raw_preferences is None:
        return None
    preferences = raw_preferences
    if isinstance(preferences, str):
        try:
            preferences = json.loads(preferences)
        except json.JSONDecodeError:
            return None
    if not isinstance(preferences, dict):
        return None
    push_value = preferences.get("push")
    if isinstance(push_value, bool):
        return push_value
    return None


def resolve_push_copy(
    message_key: str,
    *,
    language: str = "en",
    title: str | None = None,
    body: str | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Resolve localized push title/body from locale keys or explicit overrides."""
    format_params = params or {}
    resolved_title = (title or "").strip() or translator.get(
        f"{message_key}.title",
        language,
        **format_params,
    )
    resolved_body = (body or "").strip() or translator.get(
        f"{message_key}.body",
        language,
        **format_params,
    )
    return resolved_title, resolved_body


class PushNotificationService:
    """Build notification-service payloads and send via gRPC."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        grpc_client: NotificationGrpcClient | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.push_tokens_repo = UserPushTokensRepository(db_connection=db_connection)
        self.contacts_repo = ContactsRepository(db_connection=db_connection)
        self.grpc_client = grpc_client or NotificationGrpcClient()

    async def send(
        self,
        *,
        organization_id: str,
        recipient_user_id: str,
        message_key: str,
        notification_type: str,
        feed_type: str | None = None,
        language: str = "en",
        params: dict[str, Any] | None = None,
        title: str | None = None,
        body: str | None = None,
        data: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        check_push_preference: bool = True,
    ) -> PushNotificationSendResult:
        """Resolve copy, load tokens, and send push notification payload."""
        # pylint: disable=too-complex
        org_id = (organization_id or "").strip()
        user_id = (recipient_user_id or "").strip()
        if not org_id or not user_id:
            logger.info(
                "push_skipped reason=invalid_scope organization_id=%s user_id=%s",
                organization_id,
                recipient_user_id,
            )
            return PushNotificationSendResult(
                status=PushNotificationSendStatus.SKIPPED,
                skip_reason=PushNotificationSkipReason.NO_TOKENS,
            )

        if not shared_settings.notification.enabled:
            logger.info(
                "push_skipped reason=disabled organization_id=%s user_id=%s", org_id, user_id
            )
            return PushNotificationSendResult(
                status=PushNotificationSendStatus.SKIPPED,
                skip_reason=PushNotificationSkipReason.DISABLED,
            )

        if check_push_preference:
            contact = await self.contacts_repo.get_active_contact_by_user_id(
                user_id=user_id,
                organization_id=org_id,
            )
            push_enabled = _contact_push_enabled(
                contact.get("communication_preferences") if contact else None
            )
            if push_enabled is False:
                logger.info(
                    "push_skipped reason=preference_off organization_id=%s user_id=%s",
                    org_id,
                    user_id,
                )
                return PushNotificationSendResult(
                    status=PushNotificationSendStatus.SKIPPED,
                    skip_reason=PushNotificationSkipReason.PREFERENCE_OFF,
                )

        tokens = await self.push_tokens_repo.list_push_tokens_for_user(
            organization_id=org_id,
            user_id=user_id,
        )
        if not tokens:
            logger.info(
                "push_skipped reason=no_tokens organization_id=%s user_id=%s",
                org_id,
                user_id,
            )
            return PushNotificationSendResult(
                status=PushNotificationSendStatus.SKIPPED,
                skip_reason=PushNotificationSkipReason.NO_TOKENS,
            )

        resolved_title, resolved_body = resolve_push_copy(
            message_key,
            language=language,
            title=title,
            body=body,
            params=params,
        )

        payload: dict[str, Any] = {
            "tenant_id": shared_settings.isometrik.client_name,
            "project_id": org_id,
            "user_id": user_id,
            "title": resolved_title,
            "body": resolved_body,
            "type": notification_type,
            "feed_type": feed_type or notification_type,
            "tokens": tokens,
        }
        if data:
            payload["data"] = data
        if actor:
            payload["actor"] = actor
        if entity:
            payload["entity"] = entity

        merged_options: dict[str, Any] = {
            "save_to_db": True,
            "push_enabled": True,
        }
        if options:
            merged_options.update(options)
        payload["options"] = merged_options

        accepted = await self.grpc_client.send_notification(payload)
        if not accepted:
            logger.warning(
                "push_skipped reason=grpc_failed organization_id=%s user_id=%s token_count=%s",
                org_id,
                user_id,
                len(tokens),
            )
            return PushNotificationSendResult(
                status=PushNotificationSendStatus.SKIPPED,
                skip_reason=PushNotificationSkipReason.GRPC_FAILED,
                token_count=len(tokens),
            )

        logger.info(
            "push_sent organization_id=%s user_id=%s token_count=%s type=%s",
            org_id,
            user_id,
            len(tokens),
            notification_type,
        )
        return PushNotificationSendResult(
            status=PushNotificationSendStatus.SENT,
            token_count=len(tokens),
        )
