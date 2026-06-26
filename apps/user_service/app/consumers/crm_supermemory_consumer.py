"""Kafka consumer: sync CRM lifecycle events to Graphiti.

Subscribes to ``KafkaTopics.CRM_EVENTS`` with a dedicated consumer group
(``GRAPHITI_CONSUMER_GROUP_ID``) so other services consuming the same topic
are unaffected.

Failed messages are retried for transient Graphiti/FalkorDB errors. After
exhausting retries, the original event is published to the Graphiti DLQ topic
and only then is the Kafka offset committed.

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
from apps.user_service.app.services.graphiti_sync_service import (
    GraphitiSyncService,
)
from apps.user_service.app.services.kafka_event_service import KafkaEventService
from libs.shared_config.app_settings import shared_settings
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.graphiti_consumer_support import (
    build_graphiti_dlq_envelope,
    decode_crm_event_payload,
    is_retryable_sync_error,
    publish_graphiti_dlq,
    run_with_retries,
)
from libs.shared_utils.graphiti_event_replay import replay_pending_crm_events_on_startup
from libs.shared_utils.graphiti_service import (
    init_graphiti_client,
    is_graphiti_configured,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("crm_graphiti_consumer")

_MAX_POLL_INTERVAL_MS: int = 10 * 60 * 1000
_SESSION_TIMEOUT_MS: int = 30_000
_HEARTBEAT_INTERVAL_MS: int = 10_000
_MAX_POLL_RECORDS: int = 10


@dataclass(slots=True)
class _ConsumerRunContext:
    """Mutable runtime state for one consumer process."""

    pool: Any
    sync_service: GraphitiSyncService
    kafka_service: KafkaEventService
    semaphore: asyncio.Semaphore
    commit_lock: asyncio.Lock
    commit_event: asyncio.Event
    in_flight: set[asyncio.Task[None]] = field(default_factory=set)
    next_commit: dict[TopicPartition, int] = field(default_factory=dict)
    processed: dict[TopicPartition, set[int]] = field(default_factory=dict)


class CrmSupermemoryConsumer:
    """Consume CRM events and upsert entity snapshots into Graphiti.

    Requires ``KAFKA_ENABLED``, ``GRAPHITI_ENABLED``, FalkorDB host, and OpenAI key.
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
        self._group_id: str = consumer_group_id or shared_settings.graphiti.consumer_group_id

    @staticmethod
    def _is_enabled(kafka_settings: KafkaSettings) -> bool:
        """True when Kafka and Graphiti are both configured for this worker."""
        return bool(kafka_settings.enabled and is_graphiti_configured())

    def _graphiti_settings(self):
        return shared_settings.graphiti

    def _build_consumer_kwargs(self) -> dict[str, Any]:
        """Assemble constructor kwargs for ``AIOKafkaConsumer``."""
        kwargs: dict[str, Any] = {
            "bootstrap_servers": self._kafka_settings.bootstrap_servers,
            "client_id": f"{self._kafka_settings.producer_name}-crm-graphiti-consumer",
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
            logger.info("crm_graphiti_consumer_disabled")
            return
        if self._consumer is not None:
            return
        consumer = AIOKafkaConsumer(self._topic, **self._build_consumer_kwargs())
        await consumer.start()
        self._consumer = consumer
        logger.info(
            "crm_graphiti_consumer_started",
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
            logger.info("crm_graphiti_consumer_stopped")

    @staticmethod
    async def _ensure_capacity(*, in_flight: set[asyncio.Task[None]]) -> None:
        """Block until at least one in-flight processing slot is free."""
        max_concurrency = shared_settings.graphiti.consumer_max_concurrency
        while len(in_flight) >= max_concurrency:
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
                    logger.exception("crm_graphiti_consumer_commit_error")
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    async def _publish_dlq(
        self,
        *,
        ctx: _ConsumerRunContext,
        message: Any,
        error: BaseException,
        attempts: int,
        retryable: bool,
        original_event: dict[str, Any] | None = None,
        raw_payload: str | None = None,
    ) -> bool:
        """Publish a failed message to the Graphiti DLQ topic."""
        settings = self._graphiti_settings()
        if not self._kafka_settings.enabled:
            logger.error(
                "graphiti_consumer_dlq_skipped_kafka_disabled topic=%s offset=%s",
                message.topic,
                message.offset,
            )
            return False

        envelope = build_graphiti_dlq_envelope(
            source_topic=message.topic,
            source_partition=message.partition,
            source_offset=message.offset,
            consumer_group_id=self._group_id,
            error=error,
            attempts=attempts,
            retryable=retryable,
            original_event=original_event,
            raw_payload=raw_payload,
        )
        partition_key = None
        if original_event is not None:
            partition_key = str(original_event.get("aggregate_id") or original_event.get("event_id") or "")
        try:
            await publish_graphiti_dlq(
                ctx.kafka_service,
                topic=settings.dlq_topic,
                envelope=envelope,
                partition_key=partition_key or None,
            )
            return True
        except Exception:
            logger.exception(
                "graphiti_consumer_dlq_publish_failed topic=%s partition=%s offset=%s",
                message.topic,
                message.partition,
                message.offset,
            )
            return False

    async def _sync_crm_event(
        self,
        *,
        ctx: _ConsumerRunContext,
        payload_dict: dict[str, Any],
    ) -> None:
        settings = self._graphiti_settings()

        async def _run_sync() -> None:
            async with ctx.semaphore:
                async with AcquireConnection(ctx.pool) as conn:
                    await ctx.sync_service.process_crm_event(conn, payload_dict)

        await asyncio.wait_for(
            run_with_retries(
                _run_sync,
                max_attempts=settings.sync_max_retries,
                base_delay_seconds=settings.sync_retry_base_delay_seconds,
            ),
            timeout=settings.sync_timeout_seconds,
        )

    async def _process_message(self, *, ctx: _ConsumerRunContext, message: Any) -> bool:
        """Process one CRM event. Returns True when the Kafka offset may be committed."""
        event_type: str | None = None
        organization_id: str | None = None
        payload_dict: dict[str, Any] | None = None
        raw_payload: str | None = None

        try:
            raw_payload = message.value.decode("utf-8")
            payload_dict = decode_crm_event_payload(message.value)
            event_type = str(payload_dict.get("event_type") or "")
            organization_id = str(payload_dict.get("organization_id") or "")
            aggregate_id = str(payload_dict.get("aggregate_id") or "")
            event_id = str(payload_dict.get("event_id") or "")
            logger.info(
                "crm_graphiti_consumer_event_received topic=%s partition=%s offset=%s "
                "event_id=%s event_type=%s aggregate_id=%s organization_id=%s",
                message.topic,
                message.partition,
                message.offset,
                event_id,
                event_type,
                aggregate_id,
                organization_id,
            )
            await self._sync_crm_event(ctx=ctx, payload_dict=payload_dict)
            return True
        except json.JSONDecodeError as exc:
            logger.exception(
                "crm_graphiti_consumer_invalid_json topic=%s partition=%s offset=%s",
                message.topic,
                message.partition,
                message.offset,
            )
            return await self._publish_dlq(
                ctx=ctx,
                message=message,
                error=exc,
                attempts=0,
                retryable=False,
                raw_payload=raw_payload,
            )
        except Exception as exc:
            logger.exception(
                "crm_graphiti_consumer_message_error",
                extra={
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                    "event_type": event_type,
                    "organization_id": organization_id,
                },
            )
            return await self._publish_dlq(
                ctx=ctx,
                message=message,
                error=exc,
                attempts=self._graphiti_settings().sync_max_retries,
                retryable=is_retryable_sync_error(exc),
                original_event=payload_dict,
                raw_payload=raw_payload,
            )

    async def _handle_message(self, *, ctx: _ConsumerRunContext, message: Any) -> None:
        """Deserialize one CRM event, sync to Graphiti, and commit only on success/DLQ."""
        topic_partition = TopicPartition(message.topic, message.partition)
        should_commit = False
        try:
            should_commit = await self._process_message(ctx=ctx, message=message)
        finally:
            if should_commit:
                async with ctx.commit_lock:
                    self._mark_done(ctx=ctx, topic_partition=topic_partition, offset=message.offset)
                ctx.commit_event.set()
            else:
                logger.error(
                    "crm_graphiti_consumer_offset_not_committed topic=%s partition=%s offset=%s",
                    message.topic,
                    message.partition,
                    message.offset,
                )

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
            logger.info("crm_graphiti_consumer_disabled_noop")
            return

        await init_graphiti_client()
        kafka_service = KafkaEventService(self._kafka_settings)
        await kafka_service.start()
        await self.start()
        assert self._consumer is not None

        pool = await get_pool()
        sync_service = GraphitiSyncService()
        async with AcquireConnection(pool) as conn:
            replay_stats = await replay_pending_crm_events_on_startup(conn, sync_service)
            if replay_stats.selected:
                logger.info(
                    "crm_graphiti_consumer_startup_replay selected=%s processed=%s failed=%s",
                    replay_stats.selected,
                    replay_stats.processed,
                    replay_stats.failed,
                )

        ctx = _ConsumerRunContext(
            pool=pool,
            sync_service=sync_service,
            kafka_service=kafka_service,
            semaphore=asyncio.Semaphore(shared_settings.graphiti.consumer_max_concurrency),
            commit_lock=asyncio.Lock(),
            commit_event=asyncio.Event(),
        )

        committer_task = asyncio.create_task(
            self._committer_loop(ctx=ctx),
            name="crm-graphiti-committer",
        )

        try:
            await self._consume_messages(ctx=ctx)
        finally:
            try:
                if ctx.in_flight:
                    logger.info(
                        "crm_graphiti_consumer_draining",
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
                await kafka_service.stop()


async def run_crm_supermemory_consumer() -> None:
    """Convenience entrypoint for the CRM Graphiti consumer worker."""
    consumer = CrmSupermemoryConsumer()
    await consumer.consume_forever()


def main() -> None:
    """CLI entrypoint."""
    try:
        asyncio.run(run_crm_supermemory_consumer())
    except KeyboardInterrupt:
        logger.info("crm_graphiti_consumer_interrupted")


if __name__ == "__main__":
    main()
