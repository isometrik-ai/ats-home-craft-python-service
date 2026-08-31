"""Unit tests for pass event actor label enrichment."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import PassActorType, PassEventType
from apps.user_service.app.utils.pass_event_actor_labels import enrich_pass_event_actor_labels


@pytest.mark.asyncio
async def test_enrich_pass_event_actor_labels_resolves_staff_resident_and_system(monkeypatch):
    """Actor labels resolve from org members, contacts, or system default."""
    members_repo = MagicMock()
    members_repo.fetch_display_names_by_user_ids = AsyncMock(
        return_value={"guard-user": "Mr Ajay Guard"}
    )
    contacts_repo = MagicMock()
    contacts_repo.fetch_display_names_by_user_ids = AsyncMock(
        return_value={"resident-user": "Ms Radhi Sharma"}
    )
    monkeypatch.setattr(
        "apps.user_service.app.utils.pass_event_actor_labels.OrganizationMemberRepository",
        lambda _conn: members_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.utils.pass_event_actor_labels.ContactsRepository",
        lambda _conn: contacts_repo,
    )

    enriched = await enrich_pass_event_actor_labels(
        db_connection=MagicMock(),
        organization_id="org-1",
        events=[
            {
                "event_type": PassEventType.CREATED.value,
                "actor_type": PassActorType.RESIDENT.value,
                "actor_user_id": "resident-user",
            },
            {
                "event_type": PassEventType.CHECKED_IN.value,
                "actor_type": PassActorType.STAFF.value,
                "actor_user_id": "guard-user",
            },
            {
                "event_type": PassEventType.CANCELLED.value,
                "actor_type": PassActorType.SYSTEM.value,
            },
        ],
        created_by="Fallback Creator",
    )

    assert enriched[0]["actor_label"] == "Ms Radhi Sharma"
    assert enriched[1]["actor_label"] == "Mr Ajay Guard"
    assert enriched[2]["actor_label"] == "System"
