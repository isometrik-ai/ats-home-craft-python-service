"""Replay stored CRM lifecycle events into Graphiti."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from apps.user_service.app.db.repositories.events_repository import EventsRepository
from apps.user_service.app.services.graphiti_sync_service import GraphitiSyncService
from apps.user_service.app.services.organization_memory_service import (
    is_organization_memory_enabled,
)
from apps.user_service.app.services.supermemory_sync_service import resolve_sync_targets
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_event_replay")

ReplayMode = Literal["chronological", "latest_per_aggregate"]


@dataclass(slots=True)
class GraphitiEventReplayStats:
    """Counters from one replay run."""

    selected: int = 0
    processed: int = 0
    skipped_no_targets: int = 0
    skipped_memory_disabled: int = 0
    failed: int = 0


def _event_envelope(row: dict[str, Any]) -> dict[str, Any]:
    """Return the Kafka-style envelope stored in ``events.payload``."""
    payload = row.get("payload")
    if isinstance(payload, dict):
        return payload
    return {
        "event_id": row.get("event_id"),
        "event_type": row.get("event_type"),
        "aggregate_id": row.get("aggregate_id"),
        "organization_id": row.get("organization_id"),
        "payload": {},
    }


async def replay_stored_crm_events(
    db_connection: asyncpg.Connection,
    sync_service: GraphitiSyncService,
    *,
    topic: str | None = None,
    organization_id: str | None = None,
    statuses: list[str] | None = None,
    since_occurred_at: str | None = None,
    limit: int | None = None,
    mode: ReplayMode = "chronological",
    mark_synced: bool = True,
) -> GraphitiEventReplayStats:
    """Replay CRM rows from the ``events`` table through ``GraphitiSyncService``.

    Each replay calls ``process_crm_event``, which reloads canonical entity state
    from Postgres. Replaying the latest event per aggregate is enough for catch-up.
    """
    stats = GraphitiEventReplayStats()
    resolved_topic = topic or shared_settings.graphiti.crm_events_topic
    repo = EventsRepository(db_connection=db_connection)
    rows = await repo.fetch_crm_events_for_graphiti_replay(
        topic=resolved_topic,
        organization_id=organization_id,
        statuses=statuses,
        since_occurred_at=since_occurred_at,
        limit=limit,
        latest_per_aggregate=mode == "latest_per_aggregate",
    )
    stats.selected = len(rows)

    for row in rows:
        envelope = _event_envelope(row)
        org_id = str(envelope.get("organization_id") or row.get("organization_id") or "")
        event_type = str(envelope.get("event_type") or row.get("event_type") or "")
        aggregate_id = str(envelope.get("aggregate_id") or row.get("aggregate_id") or "")
        inner_payload = envelope.get("payload")
        payload_dict = inner_payload if isinstance(inner_payload, dict) else {}

        if not org_id:
            stats.skipped_no_targets += 1
            continue
        if not resolve_sync_targets(
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload_dict,
        ):
            stats.skipped_no_targets += 1
            continue
        if not await is_organization_memory_enabled(db_connection, org_id):
            stats.skipped_memory_disabled += 1
            continue

        try:
            await sync_service.process_crm_event(db_connection, envelope)
            stats.processed += 1
            if mark_synced:
                await repo.mark_graphiti_synced(event_id=str(row["event_id"]))
        except Exception:
            stats.failed += 1
            logger.exception(
                "graphiti_event_replay_failed event_id=%s event_type=%s aggregate_id=%s org=%s",
                row.get("event_id"),
                event_type,
                aggregate_id,
                org_id,
            )

    logger.info(
        "graphiti_event_replay_complete topic=%s mode=%s selected=%s processed=%s "
        "skipped_no_targets=%s skipped_memory_disabled=%s failed=%s",
        resolved_topic,
        mode,
        stats.selected,
        stats.processed,
        stats.skipped_no_targets,
        stats.skipped_memory_disabled,
        stats.failed,
    )
    return stats


async def replay_pending_crm_events_on_startup(
    db_connection: asyncpg.Connection,
    sync_service: GraphitiSyncService,
) -> GraphitiEventReplayStats:
    """Optionally replay stored CRM events when the Graphiti consumer starts.

    Controlled by ``GRAPHITI_STARTUP_BACKFILL_ENABLED`` (default: false).
    """
    settings = shared_settings.graphiti
    if not settings.startup_backfill_enabled:
        logger.info("graphiti_startup_backfill_skipped disabled")
        return GraphitiEventReplayStats()

    mode: ReplayMode = (
        "latest_per_aggregate"
        if settings.startup_backfill_mode.strip().lower() == "latest_per_aggregate"
        else "chronological"
    )
    statuses = ["pending"] if mode == "chronological" else None

    logger.info(
        "graphiti_startup_backfill_starting mode=%s limit=%s",
        mode,
        settings.startup_backfill_limit,
    )
    return await replay_stored_crm_events(
        db_connection,
        sync_service,
        topic=settings.crm_events_topic,
        statuses=statuses,
        limit=settings.startup_backfill_limit,
        mode=mode,
        mark_synced=True,
    )
