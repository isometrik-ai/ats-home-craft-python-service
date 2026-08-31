"""Resolve pass event actor display names at read time."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.schemas.enums import PassActorType, PassEventType


async def enrich_pass_event_actor_labels(
    *,
    db_connection: asyncpg.Connection,
    organization_id: str,
    events: list[dict[str, Any]],
    created_by: str | None = None,
) -> list[dict[str, Any]]:
    """Attach actor_label to pass timeline events from actor ids."""
    staff_user_ids: set[str] = set()
    resident_user_ids: set[str] = set()
    for event in events:
        actor_type = str(event.get("actor_type") or "")
        user_id = str(event.get("actor_user_id") or "").strip()
        if not user_id:
            continue
        if actor_type == PassActorType.RESIDENT.value:
            resident_user_ids.add(user_id)
        elif actor_type in {PassActorType.STAFF.value, PassActorType.SYSTEM.value}:
            staff_user_ids.add(user_id)

    members_repo = OrganizationMemberRepository(db_connection)
    contacts_repo = ContactsRepository(db_connection)
    staff_names = await members_repo.fetch_display_names_by_user_ids(
        organization_id=organization_id,
        user_ids=sorted(staff_user_ids),
    )
    resident_names = await contacts_repo.fetch_display_names_by_user_ids(
        organization_id=organization_id,
        user_ids=sorted(resident_user_ids),
    )

    enriched: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        actor_type = str(item.get("actor_type") or "")
        user_id = str(item.get("actor_user_id") or "").strip()
        label: str | None = None
        if actor_type == PassActorType.RESIDENT.value and user_id:
            label = resident_names.get(user_id)
        elif actor_type in {PassActorType.STAFF.value, PassActorType.SYSTEM.value} and user_id:
            label = staff_names.get(user_id)
        elif actor_type == PassActorType.SYSTEM.value:
            label = "System"
        if (
            not label
            and item.get("event_type") == PassEventType.CREATED.value
            and created_by
        ):
            label = created_by
        item["actor_label"] = label
        enriched.append(item)
    return enriched
