"""Kafka consumer for contacts import jobs.

This module is intentionally lean and focuses on:
- Subscribing to the ``contacts.import.requested`` topic
- Decoding the metadata-only payload
- Handing batches off to the service layer for DB work

The actual per-row CSV/XLSX parsing and mapping is expected to be handled
in a separate utility or pipeline that yields normalized contact payloads.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from apps.user_service.app.config.app_settings import KafkaSettings, app_settings
from apps.user_service.app.schemas.contacts_imports import ContactsImportEventPayload
from apps.user_service.app.schemas.enums import ContactsImportKafkaStream
from apps.user_service.app.services.contacts_imports_service import (
    ContactsImportService,
)
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ConsumerRunContext:
    """Mutable runtime state for a single consumer run."""

    pool: Any
    batch_size: int
    semaphore: asyncio.Semaphore
    max_in_flight: int
    commit_lock: asyncio.Lock
    commit_event: asyncio.Event
    in_flight: set[asyncio.Task[None]]
    next_commit: dict[TopicPartition, int]
    processed: dict[TopicPartition, set[int]]


class ContactsImportConsumer:
    """High-level Kafka consumer for contacts import jobs.

    Responsibilities:
    - Consume metadata events from ``contacts.import.requested``
    - Claim the corresponding job (transition QUEUED → RUNNING)
    - Stream job rows in batches and delegate DB writes to the service layer

    NOTE: This class is transport-focused; it intentionally delegates
    business rules and DB access to ``ContactsImportService`` and
    repository classes so that the API and consumer share the same
    invariants.
    """

    __slots__ = ("_settings", "_consumer", "_topic")

    def __init__(self, settings: KafkaSettings | None = None) -> None:
        self._settings: KafkaSettings = settings or app_settings.kafka
        self._consumer: AIOKafkaConsumer | None = None
        self._topic: str = ContactsImportKafkaStream.CONTACTS_IMPORT_REQUESTED.value

    # ------------------------------------------------------------------
    # Kafka wiring
    # ------------------------------------------------------------------
    def _build_consumer_kwargs(self) -> dict[str, Any]:
        """Assemble constructor kwargs for ``AIOKafkaConsumer``."""
        return {
            "bootstrap_servers": self._settings.bootstrap_servers,
            "client_id": f"{self._settings.producer_name}-contacts-import-consumer",
            "group_id": "contacts-import-consumers",
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
            "security_protocol": self._settings.security_protocol,
            "sasl_mechanism": self._settings.sasl_mechanism,
            "sasl_plain_username": self._settings.sasl_username,
            "sasl_plain_password": self._settings.sasl_password,
        }

    async def start(self) -> None:
        """Connect the consumer and subscribe to the topic."""
        if not self._settings.enabled:
            logger.info("contacts_import_consumer_disabled")
            return

        if self._consumer is not None:
            return

        consumer = AIOKafkaConsumer(self._topic, **self._build_consumer_kwargs())
        await consumer.start()
        self._consumer = consumer
        logger.info("contacts_import_consumer_started", extra={"topic": self._topic})

    async def stop(self) -> None:
        """Stop the consumer and flush offsets."""
        consumer, self._consumer = self._consumer, None
        if consumer is None:
            return
        try:
            await consumer.stop()
        finally:
            logger.info("contacts_import_consumer_stopped")

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------
    @staticmethod
    async def _ensure_in_flight_capacity(
        *, in_flight: set[asyncio.Task[None]], max_in_flight: int
    ) -> None:
        """Apply backpressure until in_flight is below max_in_flight."""
        while len(in_flight) >= max_in_flight:
            done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                # Propagate unexpected task failures (should be rare due to safety wrapper).
                task.result()
            in_flight.difference_update(done)

    @staticmethod
    def _track_task(*, in_flight: set[asyncio.Task[None]], task: asyncio.Task[None]) -> None:
        """Track a task in the in_flight set and discard on completion."""
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    def _mark_done(
        self,
        *,
        ctx: _ConsumerRunContext,
        topic_partition: TopicPartition,
        offset: int,
    ) -> None:
        """Record a processed offset and advance commit cursor."""
        ctx.processed.setdefault(topic_partition, set()).add(offset)
        if topic_partition not in ctx.next_commit:
            ctx.next_commit[topic_partition] = offset

        done_offsets = ctx.processed[topic_partition]
        while ctx.next_commit[topic_partition] in done_offsets:
            done_offsets.remove(ctx.next_commit[topic_partition])
            ctx.next_commit[topic_partition] += 1

    async def _commit_ready_offsets(self, *, ctx: _ConsumerRunContext) -> None:
        """Commit the latest safe offsets for each partition."""
        assert self._consumer is not None
        async with ctx.commit_lock:
            offsets: dict[TopicPartition, OffsetAndMetadata] = {}
            for topic_partition, commit_offset in ctx.next_commit.items():
                offsets[topic_partition] = OffsetAndMetadata(commit_offset, "")
            if offsets:
                await self._consumer.commit(offsets=offsets)

    async def _committer_loop(self, *, ctx: _ConsumerRunContext) -> None:
        """Background loop that commits offsets when signaled."""
        try:
            while True:
                await ctx.commit_event.wait()
                ctx.commit_event.clear()
                try:
                    await self._commit_ready_offsets(ctx=ctx)
                except Exception as exc:
                    # Commit errors are retried on the next event.
                    logger.exception("contacts_import_consumer_commit_error", exc_info=exc)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    async def _run_safely(
        self,
        *,
        ctx: _ConsumerRunContext,
        fn: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a unit of work within the bounded semaphore."""
        async with ctx.semaphore:
            await fn()

    async def _handle_message(self, *, ctx: _ConsumerRunContext, message: Any) -> None:
        """Handle a single Kafka message and update commit state."""
        topic_partition = TopicPartition(message.topic, message.partition)
        try:
            payload_dict = json.loads(message.value.decode("utf-8"))
            event = ContactsImportEventPayload.model_validate(payload_dict)
            await self._process_event(pool=ctx.pool, event=event, batch_size=ctx.batch_size)
        except Exception as exc:
            # We still mark the message as "done" so the consumer can move on.
            # The service layer is responsible for idempotency / job-state safety.
            logger.exception("contacts_import_consumer_message_error", exc_info=exc)
        finally:
            async with ctx.commit_lock:
                self._mark_done(ctx=ctx, topic_partition=topic_partition, offset=message.offset)
            ctx.commit_event.set()

    async def _consume_messages(self, *, ctx: _ConsumerRunContext) -> None:
        """Consume messages forever and schedule work with backpressure."""
        assert self._consumer is not None
        async for message in self._consumer:
            await self._ensure_in_flight_capacity(
                in_flight=ctx.in_flight,
                max_in_flight=ctx.max_in_flight,
            )

            async def _bound_handle_message(msg: Any = message) -> None:
                await self._handle_message(ctx=ctx, message=msg)

            task = asyncio.create_task(self._run_safely(ctx=ctx, fn=_bound_handle_message))
            self._track_task(in_flight=ctx.in_flight, task=task)

    async def consume_forever(self, *, batch_size: int = 1000) -> None:
        """Main loop: consume messages and process jobs.

        This function is designed to be run in a long-lived background
        process, e.g.::

            consumer = ContactsImportConsumer()
            asyncio.run(consumer.consume_forever())
        """
        if not self._settings.enabled:
            logger.info("contacts_import_consumer_disabled_noop")
            return

        await self.start()
        assert self._consumer is not None  # for type checkers

        pool = await get_pool()

        ctx = _ConsumerRunContext(
            pool=pool,
            batch_size=batch_size,
            semaphore=asyncio.Semaphore(8),
            max_in_flight=128,
            commit_lock=asyncio.Lock(),
            commit_event=asyncio.Event(),
            in_flight=set(),
            next_commit={},
            processed={},
        )

        committer_task = asyncio.create_task(self._committer_loop(ctx=ctx))
        try:
            await self._consume_messages(ctx=ctx)
        finally:
            # Finish any in-flight work before stopping the consumer, then commit
            # any remaining ready offsets.
            try:
                if ctx.in_flight:
                    await asyncio.gather(*ctx.in_flight, return_exceptions=True)
                ctx.commit_event.set()
                await self._commit_ready_offsets(ctx=ctx)
            finally:
                committer_task.cancel()
                with contextlib.suppress(Exception):
                    await committer_task
            await self.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _process_event(
        self,
        *,
        pool: Any,
        event: ContactsImportEventPayload,
        batch_size: int,
    ) -> None:
        """Process a single contacts import event using the service layer."""
        async with AcquireConnection(pool) as conn:
            service = ContactsImportService(db_connection=conn)
            await service.process_job_event(event=event, batch_size=batch_size)


async def run_contacts_import_consumer(batch_size: int = 1000) -> None:
    """Convenience entrypoint for running the contacts import consumer."""
    consumer = ContactsImportConsumer()
    await consumer.consume_forever(batch_size=batch_size)
