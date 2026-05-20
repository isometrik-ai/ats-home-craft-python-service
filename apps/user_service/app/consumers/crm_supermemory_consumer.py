"""Kafka consumer: sync CRM lifecycle events to Supermemory.

Subscribes to ``KafkaTopics.CRM_EVENTS`` with a dedicated consumer group
(``SUPERMEMORY_CONSUMER_GROUP_ID``) so other services consuming the same topic
are unaffected.

Run with the bulk-upload worker (recommended)::

    python -m apps.user_service.app.consumers.contacts_import_consumer

This module can still be run alone for local debugging::

    python -m apps.user_service.app.consumers.crm_supermemory_consumer
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from apps.user_service.app.config.app_settings import KafkaSettings, app_settings
from apps.user_service.app.schemas.enums import KafkaTopics
from apps.user_service.app.services.supermemory_sync_service import (
    SupermemorySyncService,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.logger import get_logger
from libs.shared_utils.supermemory_service import (
    init_supermemory_http_client,
    is_supermemory_configured,
)

logger = get_logger("crm_supermemory_consumer")

_MAX_CONCURRENCY: int = 4
_MAX_POLL_INTERVAL_MS: int = 10 * 60 * 1000
_SESSION_TIMEOUT_MS: int = 30_000
_HEARTBEAT_INTERVAL_MS: int = 10_000
_MAX_POLL_RECORDS: int = 10


@dataclass(slots=True)
class _ConsumerRunContext:
    """Mutable runtime state for one consumer process."""

    pool: Any
    sync_service: SupermemorySyncService
    semaphore: asyncio.Semaphore
    commit_lock: asyncio.Lock
    commit_event: asyncio.Event
    in_flight: set[asyncio.Task[None]] = field(default_factory=set)
    next_commit: dict[TopicPartition, int] = field(default_factory=dict)
    processed: dict[TopicPartition, set[int]] = field(default_factory=dict)


class CrmSupermemoryConsumer:
    """Consume CRM events and upsert entity snapshots into Supermemory.

    Requires ``KAFKA_ENABLED``, ``SUPERMEMORY_ENABLED``, and ``SUPERMEMORY_API_KEY``.
    Per-organization sync is gated by ``organizations.settings.organization_memory``.
    """

    __slots__ = ("_kafka_settings", "_consumer", "_topic", "_group_id")

    def __init__(
        self,
        *,
        kafka_settings: KafkaSettings | None = None,
        consumer_group_id: str | None = None,
    ) -> None:
        self._kafka_settings: KafkaSettings = kafka_settings or app_settings.kafka
        self._consumer: AIOKafkaConsumer | None = None
        self._topic: str = KafkaTopics.CRM_EVENTS.value
        self._group_id: str = consumer_group_id or shared_settings.supermemory.consumer_group_id

    @staticmethod
    def _is_enabled(kafka_settings: KafkaSettings) -> bool:
        """True when Kafka and Supermemory are both configured for this worker."""
        return bool(kafka_settings.enabled and is_supermemory_configured())

    def _build_consumer_kwargs(self) -> dict[str, Any]:
        """Assemble constructor kwargs for ``AIOKafkaConsumer``."""
        kwargs: dict[str, Any] = {
            "bootstrap_servers": self._kafka_settings.bootstrap_servers,
            "client_id": f"{self._kafka_settings.producer_name}-crm-supermemory-consumer",
            "group_id": self._group_id,
            "enable_auto_commit": False,
            "auto_offset_reset": "latest",
            "security_protocol": self._kafka_settings.security_protocol,
            "max_poll_interval_ms": _MAX_POLL_INTERVAL_MS,
            "session_timeout_ms": _SESSION_TIMEOUT_MS,
            "heartbeat_interval_ms": _HEARTBEAT_INTERVAL_MS,
            "max_poll_records": _MAX_POLL_RECORDS,
        }
        if self._kafka_settings.sasl_mechanism:
            kwargs["sasl_mechanism"] = self._kafka_settings.sasl_mechanism
            kwargs["sasl_plain_username"] = self._kafka_settings.sasl_username
            kwargs["sasl_plain_password"] = self._kafka_settings.sasl_password
        return kwargs

    async def start(self) -> None:
        """Connect the consumer and subscribe to the CRM events topic."""
        if not self._is_enabled(self._kafka_settings):
            logger.info("crm_supermemory_consumer_disabled")
            return
        if self._consumer is not None:
            return
        consumer = AIOKafkaConsumer(self._topic, **self._build_consumer_kwargs())
        await consumer.start()
        self._consumer = consumer
        logger.info(
            "crm_supermemory_consumer_started",
            extra={"topic": self._topic, "group_id": self._group_id},
        )

    async def stop(self) -> None:
        """Stop the Kafka consumer."""
        consumer, self._consumer = self._consumer, None
        if consumer is None:
            return
        try:
            await consumer.stop()
        finally:
            logger.info("crm_supermemory_consumer_stopped")

    @staticmethod
    async def _ensure_capacity(*, in_flight: set[asyncio.Task[None]]) -> None:
        """Block until at least one in-flight processing slot is free."""
        while len(in_flight) >= _MAX_CONCURRENCY:
            done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                with contextlib.suppress(asyncio.CancelledError):
                    task.result()
            in_flight.difference_update(done)

    @staticmethod
    def _track_task(*, in_flight: set[asyncio.Task[None]], task: asyncio.Task[None]) -> None:
        """Register *task* in *in_flight* and drop it when the task finishes."""
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    def _mark_done(
        self,
        *,
        ctx: _ConsumerRunContext,
        topic_partition: TopicPartition,
        offset: int,
    ) -> None:
        """Record a processed offset and advance the contiguous commit cursor."""
        ctx.processed.setdefault(topic_partition, set()).add(offset)
        ctx.next_commit.setdefault(topic_partition, offset)
        done_offsets = ctx.processed[topic_partition]
        while ctx.next_commit[topic_partition] in done_offsets:
            done_offsets.discard(ctx.next_commit[topic_partition])
            ctx.next_commit[topic_partition] += 1

    async def _commit_ready_offsets(self, *, ctx: _ConsumerRunContext) -> None:
        """Commit contiguous processed offsets when the commit lock is available."""
        assert self._consumer is not None
        async with ctx.commit_lock:
            offsets: dict[TopicPartition, OffsetAndMetadata] = {
                tp: OffsetAndMetadata(offset, "") for tp, offset in ctx.next_commit.items()
            }
            if offsets:
                await self._consumer.commit(offsets=offsets)

    async def _committer_loop(self, *, ctx: _ConsumerRunContext) -> None:
        """Background task: commit offsets when workers signal completion."""
        try:
            while True:
                await ctx.commit_event.wait()
                ctx.commit_event.clear()
                try:
                    await self._commit_ready_offsets(ctx=ctx)
                except Exception:
                    logger.exception("crm_supermemory_consumer_commit_error")
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    async def _handle_message(self, *, ctx: _ConsumerRunContext, message: Any) -> None:
        """Deserialize one CRM event and delegate to the sync service."""
        topic_partition = TopicPartition(message.topic, message.partition)
        event_type: str | None = None
        organization_id: str | None = None
        try:
            payload_dict = json.loads(message.value.decode("utf-8"))
            event_type = str(payload_dict.get("event_type") or "")
            organization_id = str(payload_dict.get("organization_id") or "")
            aggregate_id = str(payload_dict.get("aggregate_id") or "")
            event_id = str(payload_dict.get("event_id") or "")
            logger.info(
                "crm_supermemory_consumer_event_received topic=%s partition=%s offset=%s "
                "event_id=%s event_type=%s aggregate_id=%s organization_id=%s",
                message.topic,
                message.partition,
                message.offset,
                event_id,
                event_type,
                aggregate_id,
                organization_id,
            )
            async with ctx.semaphore:
                async with AcquireConnection(ctx.pool) as conn:
                    await ctx.sync_service.process_crm_event(conn, payload_dict)
        except Exception:
            logger.exception(
                "crm_supermemory_consumer_message_error",
                extra={
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                    "event_type": event_type,
                    "organization_id": organization_id,
                },
            )
        finally:
            async with ctx.commit_lock:
                self._mark_done(ctx=ctx, topic_partition=topic_partition, offset=message.offset)
            ctx.commit_event.set()

    async def _consume_messages(self, *, ctx: _ConsumerRunContext) -> None:
        """Poll Kafka and dispatch messages to a bounded worker pool."""
        assert self._consumer is not None
        async for message in self._consumer:
            await self._ensure_capacity(in_flight=ctx.in_flight)
            task = asyncio.create_task(
                self._handle_message(ctx=ctx, message=message),
                name=f"crm-sm-{message.topic}-{message.partition}-{message.offset}",
            )
            self._track_task(in_flight=ctx.in_flight, task=task)

    async def consume_forever(self) -> None:
        """Run the consumer until cancelled (Ctrl+C or process exit)."""
        if not self._is_enabled(self._kafka_settings):
            logger.info("crm_supermemory_consumer_disabled_noop")
            return

        await init_supermemory_http_client()
        await self.start()
        assert self._consumer is not None

        pool = await get_pool()
        ctx = _ConsumerRunContext(
            pool=pool,
            sync_service=SupermemorySyncService(),
            semaphore=asyncio.Semaphore(_MAX_CONCURRENCY),
            commit_lock=asyncio.Lock(),
            commit_event=asyncio.Event(),
        )

        committer_task = asyncio.create_task(
            self._committer_loop(ctx=ctx),
            name="crm-supermemory-committer",
        )

        try:
            await self._consume_messages(ctx=ctx)
        finally:
            try:
                if ctx.in_flight:
                    logger.info(
                        "crm_supermemory_consumer_draining",
                        extra={"in_flight": len(ctx.in_flight)},
                    )
                    await asyncio.gather(*ctx.in_flight, return_exceptions=True)
                ctx.commit_event.set()
                await self._commit_ready_offsets(ctx=ctx)
            finally:
                committer_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await committer_task
                await self.stop()


async def run_crm_supermemory_consumer() -> None:
    """Convenience entrypoint for the CRM Supermemory consumer worker."""
    consumer = CrmSupermemoryConsumer()
    await consumer.consume_forever()


def main() -> None:
    """CLI entrypoint."""
    try:
        asyncio.run(run_crm_supermemory_consumer())
    except KeyboardInterrupt:
        logger.info("crm_supermemory_consumer_interrupted")


if __name__ == "__main__":
    main()
