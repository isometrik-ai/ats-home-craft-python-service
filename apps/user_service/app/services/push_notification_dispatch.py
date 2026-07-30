"""Shared helpers for dispatching domain push notifications."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.services.push_notification_service import (
    PushNotificationSendResult,
    PushNotificationSendStatus,
    PushNotificationService,
)
from apps.user_service.app.services.units_service import format_contact_display_name
from libs.shared_utils.logger import get_logger

logger = get_logger("push_notification_dispatch")


def recipient_language_from_contact(additional_data: Any) -> str:
    """Resolve recipient locale from contact additional_data."""
    if additional_data is None:
        return "en"
    data = additional_data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return "en"
    if not isinstance(data, dict):
        return "en"
    language = str(data.get("preferred_language") or "").strip()
    return language or "en"


def contact_display_name(contact: dict[str, Any]) -> str | None:
    """Build a display name from a contact row."""
    return format_contact_display_name(
        prefix=contact.get("prefix"),
        first_name=contact.get("first_name"),
        last_name=contact.get("last_name"),
    )


def unit_label_from_row(unit_row: dict[str, Any]) -> str:
    """Prefer unit_label, then unit_code, then unit id."""
    for key in ("unit_label", "unit_code"):
        value = str(unit_row.get(key) or "").strip()
        if value:
            return value
    return str(unit_row.get("unit_id") or "").strip() or "your flat"


class PushNotificationDispatcher:
    """High-level push sends reused across domain services."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        push_service: PushNotificationService | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.push_service = push_service or PushNotificationService(db_connection=db_connection)
        self.contacts_repo = ContactsRepository(db_connection=db_connection)
        self.org_members_repo = OrganizationMemberRepository(db_connection=db_connection)

    async def send_to_user(
        self,
        *,
        organization_id: str,
        recipient_user_id: str,
        message_key: str,
        notification_type: str,
        feed_type: str | None = None,
        language: str = "en",
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        check_push_preference: bool = True,
    ) -> PushNotificationSendResult:
        """Send a push notification to one Supabase user."""
        return await self.push_service.send(
            organization_id=organization_id,
            recipient_user_id=recipient_user_id,
            message_key=message_key,
            notification_type=notification_type,
            feed_type=feed_type,
            language=language,
            params=params,
            data=data,
            actor=actor,
            entity=entity,
            options=options,
            check_push_preference=check_push_preference,
        )

    async def send_to_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
        message_key: str,
        notification_type: str,
        feed_type: str | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> PushNotificationSendResult | None:
        """Send a push to a contact when they have a linked Supabase user."""
        contact = await self.contacts_repo.get_contact_for_update(
            contact_id=contact_id,
            organization_id=organization_id,
        )
        if not contact:
            return None
        recipient_user_id = str(contact.get("user_id") or "").strip()
        if not recipient_user_id:
            return None
        return await self.send_to_user(
            organization_id=organization_id,
            recipient_user_id=recipient_user_id,
            message_key=message_key,
            notification_type=notification_type,
            feed_type=feed_type,
            language=recipient_language_from_contact(contact.get("additional_data")),
            params=params,
            data=data,
            actor=actor,
            entity=entity,
            options=options,
            check_push_preference=True,
        )

    async def send_to_unit_residents(
        self,
        *,
        organization_id: str,
        unit_id: str,
        message_key: str,
        notification_type: str,
        feed_type: str | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        exclude_user_ids: set[str] | None = None,
        exclude_contact_ids: set[str] | None = None,
    ) -> int:
        """Notify all resident contacts on a unit; return count accepted by notification-service."""
        recipients = await self.contacts_repo.list_unit_resident_recipients(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        sent = 0
        excluded_users = exclude_user_ids or set()
        excluded_contacts = exclude_contact_ids or set()
        for recipient in recipients:
            recipient_user_id = str(recipient.get("user_id") or "").strip()
            contact_id = str(recipient.get("contact_id") or "").strip()
            if not recipient_user_id or recipient_user_id in excluded_users:
                continue
            if contact_id and contact_id in excluded_contacts:
                continue
            result = await self.send_to_user(
                organization_id=organization_id,
                recipient_user_id=recipient_user_id,
                message_key=message_key,
                notification_type=notification_type,
                feed_type=feed_type,
                language=recipient_language_from_contact(recipient.get("additional_data")),
                params=params,
                data=data,
                actor=actor,
                entity=entity,
                options=options,
                check_push_preference=True,
            )
            if result.status == PushNotificationSendStatus.SENT:
                sent += 1
        return sent

    async def send_to_org_members(
        self,
        *,
        organization_id: str,
        message_key: str,
        notification_type: str,
        feed_type: str | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> int:
        """Notify active organization members (admin/staff app users)."""
        user_ids = await self.org_members_repo.list_active_member_user_ids(
            organization_id=organization_id,
        )
        sent = 0
        for recipient_user_id in user_ids:
            result = await self.send_to_user(
                organization_id=organization_id,
                recipient_user_id=recipient_user_id,
                message_key=message_key,
                notification_type=notification_type,
                feed_type=feed_type,
                params=params,
                data=data,
                actor=actor,
                entity=entity,
                options=options,
                check_push_preference=False,
            )
            if result.status == PushNotificationSendStatus.SENT:
                sent += 1
        return sent

    async def send_to_contact_unit_primary(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        message_key: str,
        notification_type: str,
        feed_type: str | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
        entity: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> PushNotificationSendResult | None:
        """Notify the primary contact linked to a contact_units row."""
        recipient = await self.contacts_repo.get_push_recipient_for_contact_unit(
            organization_id=organization_id,
            contact_unit_id=contact_unit_id,
        )
        if not recipient:
            return None
        recipient_user_id = str(recipient.get("user_id") or "").strip()
        if not recipient_user_id:
            return None
        return await self.send_to_user(
            organization_id=organization_id,
            recipient_user_id=recipient_user_id,
            message_key=message_key,
            notification_type=notification_type,
            feed_type=feed_type,
            language=recipient_language_from_contact(recipient.get("additional_data")),
            params=params,
            data=data,
            actor=actor,
            entity=entity,
            options=options,
            check_push_preference=True,
        )
