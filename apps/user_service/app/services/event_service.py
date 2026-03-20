"""Generic event service for event persistence and Kafka publishing."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.events_repository import EventsRepository
from apps.user_service.app.schemas.enums import ClientEventType, KafkaTopics
from apps.user_service.app.services.kafka_event_service import get_kafka_event_service
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.logger import get_logger

logger = get_logger("event_service")

KafkaTopic = str | KafkaTopics


class EventService:
    """Application-level service for generic event lifecycle."""

    def __init__(self, db_connection: asyncpg.Connection | None = None) -> None:
        self.db_connection = db_connection

    @staticmethod
    def _resolve_topics(topics: list[KafkaTopic]) -> list[str]:
        """Normalize topics so internal code always deals with Kafka strings."""
        if not topics:
            raise ValueError("topics must be a non-empty list")

        resolved: list[str] = []
        for topic in topics:
            if isinstance(topic, KafkaTopics):
                resolved.append(topic.value)
            else:
                resolved.append(str(topic))
        return resolved

    def build_event(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        organization_id: str,
        actor_user_id: str | None,
        payload: Mapping[str, Any] | None = None,
        source: str = "user-service",
    ) -> dict[str, Any]:
        """Build a generic event envelope."""
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "aggregate_id": str(aggregate_id),
            "organization_id": str(organization_id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "source": source,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "payload": dict(payload or {}),
        }

    async def store_event(
        self,
        *,
        event: Mapping[str, Any],
        topics: list[KafkaTopic],
        status: str = "pending",
    ) -> None:
        """Persist event to events table using current transaction connection."""
        if self.db_connection is None:
            raise ValueError("db_connection is required to store events")
        resolved_topics = self._resolve_topics(topics)

        event_repo = EventsRepository(db_connection=self.db_connection)
        # Persist the event against the first provided topic.
        # Publishing can target multiple topics, but the DB row stores one.
        resolved_topic = resolved_topics[0]
        await event_repo.create_event(
            event_id=str(event["event_id"]),
            event_type=str(event["event_type"]),
            aggregate_id=str(event["aggregate_id"]),
            organization_id=str(event["organization_id"]),
            topic=resolved_topic,
            payload=event,
            status=status,
        )

    async def create_lifecycle_event(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        organization_id: str,
        actor_user_id: str | None,
        payload: Mapping[str, Any] | None = None,
        topics: list[KafkaTopic],
    ) -> dict[str, Any]:
        """Build and persist lifecycle event in events table."""
        event = self.build_event(
            event_type=event_type,
            aggregate_id=aggregate_id,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            payload=payload,
        )
        await self.store_event(event=event, topics=topics, status="pending")
        return event

    async def create_lifecycle_events(
        self,
        *,
        items: list[dict[str, Any]],
        topics: list[KafkaTopic],
    ) -> list[dict[str, Any]]:
        """Build and persist multiple lifecycle events in one DB batch."""
        if self.db_connection is None:
            raise ValueError("db_connection is required to store events")
        if not items:
            return []
        resolved_topics = self._resolve_topics(topics)

        resolved_topic = resolved_topics[0]
        events: list[dict[str, Any]] = []
        db_rows: list[dict[str, Any]] = []

        for item in items:
            event = self.build_event(
                event_type=str(item["event_type"]),
                aggregate_id=str(item["aggregate_id"]),
                organization_id=str(item["organization_id"]),
                actor_user_id=item.get("actor_user_id"),
                payload=item.get("payload"),
            )
            events.append(event)
            db_rows.append(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "aggregate_id": event["aggregate_id"],
                    "organization_id": event["organization_id"],
                    "topic": resolved_topic,
                    "payload": event,
                    "status": "pending",
                }
            )

        event_repo = EventsRepository(db_connection=self.db_connection)
        await event_repo.bulk_create_events(events=db_rows)
        return events

    async def create_client_created_events(
        self,
        *,
        records: list[Mapping[str, Any]],
        actor_user_id: str | None,
        topics: list[KafkaTopic],
    ) -> list[dict[str, Any]]:
        """Build and persist `clients.created` lifecycle events for client records."""
        items = [
            {
                "event_type": ClientEventType.CREATED.value,
                "aggregate_id": str(record["id"]),
                "organization_id": str(record["organization_id"]),
                "actor_user_id": actor_user_id,
                "payload": {"module": "clients", "action": "create"},
            }
            for record in records
        ]
        return await self.create_lifecycle_events(items=items, topics=topics)

    @staticmethod
    async def publish_event_background(
        *,
        event: Mapping[str, Any],
        key: str | None = None,
        topics: list[KafkaTopic],
    ) -> None:
        """Best-effort Kafka publish intended for background execution."""
        try:
            kafka_service = get_kafka_event_service()
            resolved_topics = EventService._resolve_topics(topics)
            metadata = await kafka_service.produce_event(
                event=event, key=key, topics=resolved_topics
            )
        except Exception:
            logger.exception(
                "kafka_event_publish_failed",
                extra={
                    "event_type": event.get("event_type"),
                    "aggregate_id": event.get("aggregate_id"),
                    "organization_id": event.get("organization_id"),
                },
            )
            return

        # Confirmation comes from Kafka ack metadata. If Kafka is disabled,
        # produce_event returns None and we must not mark DB row as published.
        if metadata is None:
            logger.warning(
                "event_publish_status_update_skipped_no_kafka_ack",
                extra={
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "aggregate_id": event.get("aggregate_id"),
                    "organization_id": event.get("organization_id"),
                },
            )
            return

        event_id = event.get("event_id")
        if not event_id:
            logger.error(
                "event_publish_status_update_skipped_missing_event_id",
                extra={
                    "event_type": event.get("event_type"),
                    "aggregate_id": event.get("aggregate_id"),
                    "organization_id": event.get("organization_id"),
                },
            )
            return

        try:
            pool = await get_pool()
            async with AcquireConnection(pool) as conn:
                event_repo = EventsRepository(db_connection=conn)
                await event_repo.update_event_status(
                    event_id=str(event_id),
                    status="published",
                    mark_published_at=True,
                )
        except Exception:
            logger.exception(
                "event_publish_status_update_failed",
                extra={
                    "event_id": event_id,
                    "event_type": event.get("event_type"),
                    "aggregate_id": event.get("aggregate_id"),
                    "organization_id": event.get("organization_id"),
                },
            )
