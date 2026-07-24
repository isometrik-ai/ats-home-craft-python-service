"""Unit tests for EventService lifecycle helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import (
    ClientEventType,
    ClientType,
    KafkaTopics,
    LeadEventType,
)
from apps.user_service.app.services.event_service import EventService


class _FakeEventsRepo:
    """In-memory fake EventsRepository."""

    def __init__(self):
        self.created: list[dict[str, Any]] = []
        self.bulk_created: list[dict[str, Any]] = []
        self.status_updates: list[dict[str, Any]] = []

    async def create_event(self, **kwargs) -> None:
        """Record single event insert."""
        self.created.append(kwargs)

    async def bulk_create_events(self, *, events: list[dict[str, Any]]) -> None:
        """Record bulk event insert."""
        self.bulk_created.extend(events)

    async def update_event_status(self, **kwargs) -> None:
        """Record status update."""
        self.status_updates.append(kwargs)


def test_build_event_envelope():
    """Build event fills required envelope fields."""
    event = EventService().build_event(
        event_type="clients.created",
        aggregate_id="client-1",
        organization_id="org-1",
        actor_user_id="user-1",
        payload={"module": "clients"},
    )
    assert event["event_type"] == "clients.created"
    assert event["aggregate_id"] == "client-1"
    assert event["payload"]["module"] == "clients"
    assert event["event_id"]


def test_normalize_client_type_for_api_path():
    """Client type maps to API resource segment."""
    assert EventService._normalize_client_type_for_api_path(ClientType.COMPANY.value) == "companies"
    assert EventService._normalize_client_type_for_api_path(ClientType.PERSON.value) == "contacts"
    assert EventService._normalize_client_type_for_api_path("unknown") == ""


def test_resolve_topics_accepts_enum_and_string():
    """Topic resolver accepts enum members and raw strings."""
    resolved = EventService._resolve_topics([KafkaTopics.CRM_EVENTS, "custom.topic"])
    assert resolved == [KafkaTopics.CRM_EVENTS.value, "custom.topic"]


def test_resolve_topics_rejects_empty():
    """Topic resolver rejects empty lists."""
    with pytest.raises(ValueError):
        EventService._resolve_topics([])


@pytest.mark.asyncio
async def test_store_event_persists_via_repo():
    """Store event writes through EventsRepository."""
    fake_repo = _FakeEventsRepo()
    service = EventService(db_connection=MagicMock())
    event = service.build_event(
        event_type="leads.created",
        aggregate_id="lead-1",
        organization_id="org-1",
        actor_user_id="user-1",
    )

    with patch(
        "apps.user_service.app.services.event_service.EventsRepository",
        return_value=fake_repo,
    ):
        await service.store_event(event=event, topics=[KafkaTopics.CRM_EVENTS])

    assert fake_repo.created[0]["event_type"] == "leads.created"
    assert fake_repo.created[0]["topic"] == KafkaTopics.CRM_EVENTS.value


@pytest.mark.asyncio
async def test_store_event_requires_connection():
    """Store event rejects missing db connection."""
    service = EventService(db_connection=None)
    event = service.build_event(
        event_type="leads.created",
        aggregate_id="lead-1",
        organization_id="org-1",
        actor_user_id=None,
    )
    with pytest.raises(ValueError):
        await service.store_event(event=event, topics=[KafkaTopics.CRM_EVENTS])


@pytest.mark.asyncio
async def test_create_lifecycle_event():
    """Lifecycle helper builds and stores one event."""
    fake_repo = _FakeEventsRepo()
    service = EventService(db_connection=MagicMock())

    with patch(
        "apps.user_service.app.services.event_service.EventsRepository",
        return_value=fake_repo,
    ):
        event = await service.create_lifecycle_event(
            event_type=LeadEventType.CREATED.value,
            aggregate_id="lead-1",
            organization_id="org-1",
            actor_user_id="user-1",
            payload={"module": "leads"},
            topics=[KafkaTopics.CRM_EVENTS],
        )

    assert event["event_type"] == LeadEventType.CREATED.value
    assert len(fake_repo.created) == 1


@pytest.mark.asyncio
async def test_create_lead_created_lifecycle_event():
    """Lead-created helper uses standard payload."""
    fake_repo = _FakeEventsRepo()
    service = EventService(db_connection=MagicMock())

    with patch(
        "apps.user_service.app.services.event_service.EventsRepository",
        return_value=fake_repo,
    ):
        event = await service.create_lead_created_lifecycle_event(
            lead_id="lead-1",
            organization_id="org-1",
            actor_user_id="user-1",
            topics=[KafkaTopics.CRM_EVENTS],
        )

    assert event["payload"]["action"] == "create"
    assert event["event_type"] == LeadEventType.CREATED.value


@pytest.mark.asyncio
async def test_create_lifecycle_events_bulk():
    """Bulk lifecycle helper inserts all rows."""
    fake_repo = _FakeEventsRepo()
    service = EventService(db_connection=MagicMock())
    items = [
        {
            "event_type": ClientEventType.CREATED.value,
            "aggregate_id": "c-1",
            "organization_id": "org-1",
            "actor_user_id": "user-1",
        },
        {
            "event_type": ClientEventType.CREATED.value,
            "aggregate_id": "c-2",
            "organization_id": "org-1",
            "actor_user_id": "user-1",
        },
    ]

    with patch(
        "apps.user_service.app.services.event_service.EventsRepository",
        return_value=fake_repo,
    ):
        events = await service.create_lifecycle_events(items=items, topics=[KafkaTopics.CRM_EVENTS])

    assert len(events) == 2
    assert len(fake_repo.bulk_created) == 2


@pytest.mark.asyncio
async def test_create_client_created_events():
    """Client-created helper maps client_type to API path."""
    fake_repo = _FakeEventsRepo()
    service = EventService(db_connection=MagicMock())
    records = [
        {"id": "c-1", "organization_id": "org-1", "client_type": ClientType.PERSON.value},
        {"id": "co-1", "organization_id": "org-1", "client_type": ClientType.COMPANY.value},
    ]

    with patch(
        "apps.user_service.app.services.event_service.EventsRepository",
        return_value=fake_repo,
    ):
        events = await service.create_client_created_events(
            records=records,
            actor_user_id="user-1",
            topics=[KafkaTopics.CRM_EVENTS],
        )

    assert events[0]["payload"]["client_type"] == "contacts"
    assert events[1]["payload"]["client_type"] == "companies"


@pytest.mark.asyncio
async def test_publish_event_background_updates_status():
    """Background publish marks event published after Kafka ack."""
    fake_repo = _FakeEventsRepo()
    kafka_service = MagicMock()
    kafka_service.produce_event = AsyncMock(return_value={"partition": 0})
    pool = MagicMock()
    conn = MagicMock()

    class _Acquire:
        """Async context manager stub."""

        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_args):
            return None

    with (
        patch(
            "apps.user_service.app.services.event_service.get_kafka_event_service",
            return_value=kafka_service,
        ),
        patch(
            "apps.user_service.app.services.event_service.get_pool",
            AsyncMock(return_value=pool),
        ),
        patch(
            "apps.user_service.app.services.event_service.AcquireConnection",
            return_value=_Acquire(),
        ),
        patch(
            "apps.user_service.app.services.event_service.EventsRepository",
            return_value=fake_repo,
        ),
    ):
        await EventService.publish_event_background(
            event={"event_id": "evt-1", "event_type": "leads.created"},
            key="lead-1",
            topics=[KafkaTopics.CRM_EVENTS],
        )

    assert fake_repo.status_updates[0]["status"] == "published"


@pytest.mark.asyncio
async def test_publish_event_skips_without_kafka_ack():
    """Background publish skips DB update when Kafka returns no ack."""
    fake_repo = _FakeEventsRepo()
    kafka_service = MagicMock()
    kafka_service.produce_event = AsyncMock(return_value=None)

    with (
        patch(
            "apps.user_service.app.services.event_service.get_kafka_event_service",
            return_value=kafka_service,
        ),
        patch(
            "apps.user_service.app.services.event_service.EventsRepository",
            return_value=fake_repo,
        ),
    ):
        await EventService.publish_event_background(
            event={"event_id": "evt-1", "event_type": "leads.created"},
            topics=[KafkaTopics.CRM_EVENTS],
        )

    assert fake_repo.status_updates == []
