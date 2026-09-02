"""Daily help check-in/out push notifications to linked unit holders."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.daily_help_repository import (
    DailyHelpRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
    recipient_language_from_contact,
)
from apps.user_service.app.services.push_notification_service import (
    PushNotificationSendStatus,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("daily_help_notification_service")


class DailyHelpNotificationService:
    """Notify Owner/Tenant contacts on units linked to a daily help profile."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        push_dispatcher: PushNotificationDispatcher | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.daily_help_repo = DailyHelpRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self._push_dispatcher = push_dispatcher

    def _push(self) -> PushNotificationDispatcher:
        """Return the push dispatcher, creating it on first use."""
        if self._push_dispatcher is None:
            self._push_dispatcher = PushNotificationDispatcher(db_connection=self.db_connection)
        return self._push_dispatcher

    async def notify_linked_unit_holders(
        self,
        *,
        organization_id: str,
        profile_id: str,
        pass_id: str,
        event_id: str,
        message_key: str,
        idempotency_suffix: str,
        helper_name: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Push to Owner/Tenant on each active household link; dedupe by user_id."""
        # pylint: disable=too-complex
        links = await self.daily_help_repo.list_active_links_for_profile(
            organization_id=organization_id,
            profile_id=profile_id,
        )
        if not links:
            return 0

        unit_ids = [str(link["unit_id"]) for link in links if link.get("unit_id")]
        if not unit_ids:
            return 0

        occupants_by_unit = await self.units_repo.get_unit_role_occupants_batch(
            organization_id=organization_id,
            unit_ids=unit_ids,
        )

        contact_ids: list[str] = []
        for occupants in occupants_by_unit.values():
            for role_key in ("owner", "tenant"):
                occupant = occupants.get(role_key)
                if occupant and occupant.get("contact_id"):
                    contact_ids.append(str(occupant["contact_id"]))

        if not contact_ids:
            return 0

        user_ids_seen: set[str] = set()
        sent = 0
        display_name = (helper_name or "Daily help").strip() or "Daily help"
        params = {"helper_name": display_name}
        data: dict[str, Any] = {
            "pass_id": pass_id,
            "daily_help_profile_id": profile_id,
            "screen": "daily_help_detail",
        }
        if project_id:
            data["project_id"] = project_id

        for contact_id in contact_ids:
            contact = await self.contacts_repo.get_contact_for_update(
                contact_id=contact_id,
                organization_id=organization_id,
            )
            if not contact:
                continue
            recipient_user_id = str(contact.get("user_id") or "").strip()
            if not recipient_user_id or recipient_user_id in user_ids_seen:
                continue
            user_ids_seen.add(recipient_user_id)

            result = await self._push().send_to_user(
                organization_id=organization_id,
                recipient_user_id=recipient_user_id,
                message_key=message_key,
                notification_type="NOTIFICATION_TYPE_PASS",
                feed_type="daily_help",
                language=recipient_language_from_contact(contact.get("additional_data")),
                params=params,
                data=data,
                entity={"kind": "daily_help", "id": profile_id},
                options={
                    "click_action": "OPEN_DAILY_HELP",
                    "idempotency_key": (f"daily_help:{profile_id}:{event_id}:{idempotency_suffix}"),
                },
            )
            if result.status == PushNotificationSendStatus.SENT:
                sent += 1

        return sent
