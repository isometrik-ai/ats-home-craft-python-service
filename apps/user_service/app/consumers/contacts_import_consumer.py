"""Kafka consumer for contacts import jobs.

This module is intentionally lean and focuses on:
- Subscribing to the ``contacts.import.requested`` topic
- Decoding the metadata-only payload
- Handing batches off to the service layer for DB work

The actual per-row CSV/XLSX parsing and mapping is expected to be handled
in a separate utility or pipeline that yields normalized contact payloads.

Worker entrypoint (bulk upload + CRM→Supermemory sync in one process)::

    python -m apps.user_service.app.consumers.contacts_import_consumer
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
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

# ---------------------------------------------------------------------------
# Tuning constants — adjust to your workload
# ---------------------------------------------------------------------------

# How many messages can be in-flight (created as tasks) at once.
# Matches the semaphore so we never queue tasks we can't run immediately.
_MAX_CONCURRENCY: int = 8

# How long a single import job is allowed to run before Kafka considers
# the consumer dead and triggers a rebalance.
# Set to 20 minutes — increase if you have very large files.
_MAX_POLL_INTERVAL_MS: int = 20 * 60 * 1000  # 20 minutes

# Kafka session / heartbeat — standard safe values.
_SESSION_TIMEOUT_MS: int = 30_000  # 30 s
_HEARTBEAT_INTERVAL_MS: int = 10_000  # 10 s  (must be < session_timeout / 3)

# How many records Kafka delivers per poll() call.
# Keeping this low means the poll loop stays responsive.
_MAX_POLL_RECORDS: int = 5


# ---------------------------------------------------------------------------
# Internal runtime context
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ConsumerRunContext:
    """Mutable runtime state for a single consumer run."""

    pool: Any
    batch_size: int
    semaphore: asyncio.Semaphore
    commit_lock: asyncio.Lock
    commit_event: asyncio.Event
    in_flight: set[asyncio.Task[None]] = field(default_factory=set)
    next_commit: dict[TopicPartition, int] = field(default_factory=dict)
    processed: dict[TopicPartition, set[int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------


class ContactsImportConsumer:
    """High-level Kafka consumer for contacts import jobs.

    Responsibilities
    ----------------
    - Consume metadata events from ``contacts.import.requested``
    - Claim the corresponding job (transition QUEUED → RUNNING)
    - Stream job rows in batches and delegate DB writes to the service layer

    Design notes
    ------------
    - Concurrency is bounded by a single semaphore (_MAX_CONCURRENCY).
      ``in_flight`` is kept at the same size so we never queue tasks that
      cannot run immediately — this prevents unbounded memory growth.
    - Offsets are committed manually and only after a message has been fully
      processed, so no work is silently lost on crash/restart.
    - The poll loop is kept tight (no blocking awaits between polls) so Kafka
      never mistakes the consumer for dead and triggers a spurious rebalance.
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
            "enable_auto_commit": False,  # we commit manually after processing
            "auto_offset_reset": "latest",
            "security_protocol": self._settings.security_protocol,
            "sasl_mechanism": self._settings.sasl_mechanism,
            "sasl_plain_username": self._settings.sasl_username,
            "sasl_plain_password": self._settings.sasl_password,
            # ---- Rebalance / timeout tuning ----
            # max_poll_interval_ms is the most important setting here.
            # If the poll loop is blocked longer than this (e.g. because
            # _ensure_in_flight_capacity is waiting for slow tasks), Kafka
            # will kick the consumer out and trigger a rebalance.
            "max_poll_interval_ms": _MAX_POLL_INTERVAL_MS,
            "session_timeout_ms": _SESSION_TIMEOUT_MS,
            "heartbeat_interval_ms": _HEARTBEAT_INTERVAL_MS,
            # Limit messages per poll so the loop stays responsive.
            "max_poll_records": _MAX_POLL_RECORDS,
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
    # Backpressure
    # ------------------------------------------------------------------

    @staticmethod
    async def _ensure_capacity(*, in_flight: set[asyncio.Task[None]]) -> None:
        """Block until at least one in-flight slot is free.

        Because max_in_flight == semaphore size, this is rarely hit — the
        semaphore itself provides the primary backpressure. This is a safety
        net against the in_flight set growing unexpectedly.
        """
        while len(in_flight) >= _MAX_CONCURRENCY:
            logger.info(
                "contacts_import_consumer_ensure_capacity_waiting",
                extra={"in_flight": len(in_flight)},
            )
            done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                # Surface unexpected task failures early.
                with contextlib.suppress(asyncio.CancelledError):
                    task.result()
            in_flight.difference_update(done)

    @staticmethod
    def _track_task(*, in_flight: set[asyncio.Task[None]], task: asyncio.Task[None]) -> None:
        """Track a task in the in-flight set."""
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    # ------------------------------------------------------------------
    # Offset tracking
    # ------------------------------------------------------------------

    def _mark_done(
        self,
        *,
        ctx: _ConsumerRunContext,
        topic_partition: TopicPartition,
        offset: int,
    ) -> None:
        """Record a processed offset and advance the contiguous commit cursor.

        Only a contiguous run of completed offsets is safe to commit —
        committing offset N means "everything up to N has been processed".
        If offset 5 finishes before offset 4, we hold off committing 5 until
        4 is also done.
        """
        ctx.processed.setdefault(topic_partition, set()).add(offset)
        ctx.next_commit.setdefault(topic_partition, offset)

        done_offsets = ctx.processed[topic_partition]
        while ctx.next_commit[topic_partition] in done_offsets:
            done_offsets.discard(ctx.next_commit[topic_partition])
            ctx.next_commit[topic_partition] += 1

    async def _commit_ready_offsets(self, *, ctx: _ConsumerRunContext) -> None:
        """Commit the latest safe (contiguous) offsets for each partition."""
        assert self._consumer is not None
        async with ctx.commit_lock:
            offsets: dict[TopicPartition, OffsetAndMetadata] = {
                tp: OffsetAndMetadata(offset, "") for tp, offset in ctx.next_commit.items()
            }
            if offsets:
                await self._consumer.commit(offsets=offsets)

    async def _committer_loop(self, *, ctx: _ConsumerRunContext) -> None:
        """Background task: commit offsets whenever signaled by a worker."""
        try:
            while True:
                await ctx.commit_event.wait()
                ctx.commit_event.clear()
                try:
                    await self._commit_ready_offsets(ctx=ctx)
                except Exception as exc:
                    # Commit errors are retried on the next signal.
                    logger.exception("contacts_import_consumer_commit_error", exc_info=exc)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_message(self, *, ctx: _ConsumerRunContext, message: Any) -> None:
        """Deserialize, process, and mark a single message as done.

        Errors in processing are caught and logged so the consumer always
        advances — the service layer is responsible for idempotency and
        job-state safety (e.g. moving jobs to FAILED).
        """
        topic_partition = TopicPartition(message.topic, message.partition)
        started_ts = asyncio.get_running_loop().time()
        job_key: str | None = None
        try:
            payload_dict = json.loads(message.value.decode("utf-8"))
            event = ContactsImportEventPayload.model_validate(payload_dict)
            job_key = str(event.job_key)
            logger.info(
                "contacts_import_consumer_processing_started job_key=%s organization_id=%s",
                job_key,
                str(event.organization_id),
            )
            async with ctx.semaphore:
                await self._process_event(
                    pool=ctx.pool,
                    event=event,
                    batch_size=ctx.batch_size,
                )
            elapsed_ms = int((asyncio.get_running_loop().time() - started_ts) * 1000)
            logger.info(
                "contacts_import_consumer_processing_finished elapsed_ms=%s",
                elapsed_ms,
            )
        except Exception as exc:
            logger.exception(
                "contacts_import_consumer_message_error",
                exc_info=exc,
                extra={
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                    "job_key": job_key,
                },
            )
        finally:
            # Always mark done so we never block on a poison-pill message.
            async with ctx.commit_lock:
                self._mark_done(
                    ctx=ctx,
                    topic_partition=topic_partition,
                    offset=message.offset,
                )
            ctx.commit_event.set()

    async def _consume_messages(self, *, ctx: _ConsumerRunContext) -> None:
        """Poll Kafka and dispatch each message to a bounded task pool.

        Key design decisions
        --------------------
        - ``_ensure_capacity`` blocks BEFORE creating a new task, so we never
          have more tasks than _MAX_CONCURRENCY waiting on the semaphore.
        - The semaphore lives INSIDE ``_handle_message``, not here, so the poll
          loop itself is never blocked by slow processing.  Kafka heartbeats
          keep ticking even when all workers are busy.
        """
        assert self._consumer is not None
        async for message in self._consumer:
            logger.info(
                "contacts_import_consumer_received",
                extra={
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                },
            )
            # Wait for a free slot before creating a new task.
            # This keeps memory bounded and prevents unbounded task queuing.
            await self._ensure_capacity(in_flight=ctx.in_flight)

            task = asyncio.create_task(
                self._handle_message(ctx=ctx, message=message),
                name=f"import-{message.topic}-{message.partition}-{message.offset}",
            )
            self._track_task(in_flight=ctx.in_flight, task=task)

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def consume_forever(self, *, batch_size: int = 1000) -> None:
        """Main loop: consume messages and process import jobs.

        Usage::

            consumer = ContactsImportConsumer()
            asyncio.run(consumer.consume_forever())
        """
        if not self._settings.enabled:
            logger.info("contacts_import_consumer_disabled_noop")
            return

        await self.start()
        assert self._consumer is not None

        pool = await get_pool()

        ctx = _ConsumerRunContext(
            pool=pool,
            batch_size=batch_size,
            semaphore=asyncio.Semaphore(_MAX_CONCURRENCY),
            commit_lock=asyncio.Lock(),
            commit_event=asyncio.Event(),
        )

        committer_task = asyncio.create_task(
            self._committer_loop(ctx=ctx),
            name="contacts-import-committer",
        )

        try:
            await self._consume_messages(ctx=ctx)
        finally:
            # Drain in-flight work, do a final commit, then shut down cleanly.
            try:
                if ctx.in_flight:
                    logger.info(
                        "contacts_import_consumer_draining",
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

    # ------------------------------------------------------------------
    # Service delegation
    # ------------------------------------------------------------------

    async def _process_event(
        self,
        *,
        pool: Any,
        event: ContactsImportEventPayload,
        batch_size: int,
    ) -> None:
        """Delegate a single contacts import event to the service layer."""
        async with AcquireConnection(pool) as conn:
            service = ContactsImportService(db_connection=conn)
            await service.process_job_event(event=event, batch_size=batch_size)


# ---------------------------------------------------------------------------
# Convenience entrypoints
# ---------------------------------------------------------------------------


async def run_contacts_import_consumer(batch_size: int = 1000) -> None:
    """Convenience entrypoint for running the contacts import consumer."""
    consumer = ContactsImportConsumer()
    await consumer.consume_forever(batch_size=batch_size)


def main() -> None:
    """CLI entrypoint: contacts import + CRM Supermemory consumers."""
    logging.basicConfig(level=logging.INFO)
    try:
        from apps.user_service.app.consumers.kafka_workers import run_kafka_workers

        asyncio.run(run_kafka_workers())
    except KeyboardInterrupt:
        logger.info("contacts_import_consumer_interrupted")


if __name__ == "__main__":
    main()
