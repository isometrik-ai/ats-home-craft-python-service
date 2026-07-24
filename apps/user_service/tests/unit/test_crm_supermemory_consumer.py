"""Unit tests for CRM supermemory (Graphiti) consumer."""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka.structs import TopicPartition

from apps.user_service.app.consumers.crm_supermemory_consumer import (
    CrmSupermemoryConsumer,
    _ConsumerRunContext,
)


def _message(*, offset: int = 0, payload: dict | None = None, raw: bytes | None = None):
    """Build a minimal Kafka message stub."""
    body = (
        raw
        if raw is not None
        else json.dumps(
            payload
            or {"event_id": "e1", "organization_id": "org-1", "event_type": "contact.updated"}
        ).encode()
    )
    return SimpleNamespace(topic="crm.events.dev", partition=0, offset=offset, value=body)


def _ctx() -> _ConsumerRunContext:
    """Build consumer run context with async primitives."""
    import asyncio

    return _ConsumerRunContext(
        pool=MagicMock(),
        sync_service=MagicMock(),
        kafka_service=MagicMock(),
        semaphore=asyncio.Semaphore(2),
        commit_lock=asyncio.Lock(),
        commit_event=asyncio.Event(),
    )


@pytest.mark.asyncio
async def test_process_message_success() -> None:
    """Successful sync returns commit=True."""
    consumer = CrmSupermemoryConsumer()
    with patch.object(CrmSupermemoryConsumer, "_sync_crm_event", new=AsyncMock()):
        ok = await consumer._process_message(ctx=_ctx(), message=_message())
    assert ok is True


@pytest.mark.asyncio
async def test_process_message_invalid_json_dlq() -> None:
    """Invalid JSON publishes to DLQ and commits when publish succeeds."""
    consumer = CrmSupermemoryConsumer()
    with patch.object(
        CrmSupermemoryConsumer, "_publish_dlq", new=AsyncMock(return_value=True)
    ) as dlq:
        ok = await consumer._process_message(ctx=_ctx(), message=_message(raw=b"not-json"))
    assert ok is True
    dlq.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_sync_error_dlq() -> None:
    """Sync failure with DLQ success still commits offset."""
    consumer = CrmSupermemoryConsumer()
    with (
        patch.object(
            CrmSupermemoryConsumer,
            "_sync_crm_event",
            new=AsyncMock(side_effect=RuntimeError("graph down")),
        ),
        patch.object(CrmSupermemoryConsumer, "_publish_dlq", new=AsyncMock(return_value=True)),
    ):
        ok = await consumer._process_message(ctx=_ctx(), message=_message())
    assert ok is True


@pytest.mark.asyncio
async def test_process_message_no_commit_on_dlq_fail() -> None:
    """DLQ publish failure prevents offset commit."""
    consumer = CrmSupermemoryConsumer()
    with (
        patch.object(
            CrmSupermemoryConsumer,
            "_sync_crm_event",
            new=AsyncMock(side_effect=RuntimeError("graph down")),
        ),
        patch.object(CrmSupermemoryConsumer, "_publish_dlq", new=AsyncMock(return_value=False)),
    ):
        ok = await consumer._process_message(ctx=_ctx(), message=_message())
    assert ok is False


def test_mark_done_advances_contiguous_offsets() -> None:
    """Processed offsets advance commit cursor in order."""
    consumer = CrmSupermemoryConsumer()
    ctx = _ctx()
    tp = TopicPartition("crm.events.dev", 0)

    consumer._mark_done(ctx=ctx, topic_partition=tp, offset=0)
    consumer._mark_done(ctx=ctx, topic_partition=tp, offset=1)

    assert ctx.next_commit[tp] == 2
    assert ctx.processed[tp] == set()


@pytest.mark.asyncio
async def test_handle_message_sets_commit_event() -> None:
    """Successful handle marks offset and signals committer."""
    consumer = CrmSupermemoryConsumer()
    ctx = _ctx()
    with patch.object(CrmSupermemoryConsumer, "_process_message", new=AsyncMock(return_value=True)):
        await consumer._handle_message(ctx=ctx, message=_message(offset=3))
    assert ctx.commit_event.is_set()
    assert TopicPartition("crm.events.dev", 0) in ctx.next_commit


def test_is_enabled_requires_kafka_and_graphiti() -> None:
    """Consumer enabled only when Kafka and Graphiti configured."""
    kafka = MagicMock(enabled=True)
    with patch(
        "apps.user_service.app.consumers.crm_supermemory_consumer.is_graphiti_configured",
        return_value=True,
    ):
        assert CrmSupermemoryConsumer._is_enabled(kafka) is True
    with patch(
        "apps.user_service.app.consumers.crm_supermemory_consumer.is_graphiti_configured",
        return_value=False,
    ):
        assert CrmSupermemoryConsumer._is_enabled(kafka) is False


def test_build_consumer_kwargs_with_sasl() -> None:
    """SASL settings are forwarded to aiokafka when configured."""
    kafka = MagicMock(
        enabled=True,
        bootstrap_servers="localhost:9092",
        producer_name="test-producer",
        security_protocol="SASL_SSL",
        sasl_mechanism="PLAIN",
        sasl_username="user",
        sasl_password="pass",
    )
    consumer = CrmSupermemoryConsumer(kafka_settings=kafka, consumer_group_id="grp-1")
    kwargs = consumer._build_consumer_kwargs()
    assert kwargs["sasl_mechanism"] == "PLAIN"
    assert kwargs["group_id"] == "grp-1"
    assert kwargs["enable_auto_commit"] is False


def test_build_consumer_kwargs_without_sasl() -> None:
    """Consumer kwargs omit SASL fields when mechanism is unset."""
    kafka = MagicMock(
        enabled=True,
        bootstrap_servers="localhost:9092",
        producer_name="test-producer",
        security_protocol="PLAINTEXT",
        sasl_mechanism=None,
    )
    consumer = CrmSupermemoryConsumer(kafka_settings=kafka)
    kwargs = consumer._build_consumer_kwargs()
    assert "sasl_mechanism" not in kwargs


@pytest.mark.asyncio
async def test_start_disabled_noop() -> None:
    """start() is a no-op when Kafka/Graphiti disabled."""
    kafka = MagicMock(enabled=False)
    consumer = CrmSupermemoryConsumer(kafka_settings=kafka)
    with patch.object(CrmSupermemoryConsumer, "_is_enabled", return_value=False):
        await consumer.start()
    assert consumer._consumer is None


@pytest.mark.asyncio
async def test_start_and_stop() -> None:
    """Consumer connects on start and stops cleanly."""
    kafka = MagicMock(
        enabled=True,
        bootstrap_servers="localhost:9092",
        producer_name="test-producer",
        security_protocol="PLAINTEXT",
        sasl_mechanism=None,
    )
    consumer = CrmSupermemoryConsumer(kafka_settings=kafka)
    mock_consumer = AsyncMock()
    with (
        patch.object(CrmSupermemoryConsumer, "_is_enabled", return_value=True),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.AIOKafkaConsumer",
            return_value=mock_consumer,
        ),
    ):
        await consumer.start()
        assert consumer._consumer is mock_consumer
        mock_consumer.start.assert_awaited_once()
        await consumer.start()
        mock_consumer.start.assert_awaited_once()
        await consumer.stop()
        mock_consumer.stop.assert_awaited_once()
        assert consumer._consumer is None


@pytest.mark.asyncio
async def test_stop_when_not_started() -> None:
    """stop() is safe when consumer was never started."""
    consumer = CrmSupermemoryConsumer()
    await consumer.stop()


@pytest.mark.asyncio
async def test_ensure_capacity_waits_for_slot() -> None:
    """_ensure_capacity blocks until an in-flight task completes."""
    import asyncio

    in_flight: set[asyncio.Task[None]] = set()

    async def _done() -> None:
        return None

    task = asyncio.create_task(_done())
    in_flight.add(task)
    with patch(
        "apps.user_service.app.consumers.crm_supermemory_consumer.shared_settings"
    ) as settings:
        settings.graphiti.consumer_max_concurrency = 1
        await CrmSupermemoryConsumer._ensure_capacity(in_flight=in_flight)
    await task


def test_track_task_removes_on_completion() -> None:
    """Completed tasks are removed from in_flight via callback."""
    import asyncio

    in_flight: set[asyncio.Task[None]] = set()

    async def _noop() -> None:
        return None

    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(_noop())
        CrmSupermemoryConsumer._track_task(in_flight=in_flight, task=task)
        assert task in in_flight
        loop.run_until_complete(task)
        loop.run_until_complete(asyncio.sleep(0))
        assert task not in in_flight
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_commit_ready_offsets() -> None:
    """Ready offsets are committed to Kafka."""
    consumer = CrmSupermemoryConsumer()
    mock_kafka = AsyncMock()
    consumer._consumer = mock_kafka
    ctx = _ctx()
    tp = TopicPartition("crm.events.dev", 0)
    ctx.next_commit[tp] = 5

    await consumer._commit_ready_offsets(ctx=ctx)

    mock_kafka.commit.assert_awaited_once()
    offsets = mock_kafka.commit.await_args.kwargs["offsets"]
    assert offsets[tp].offset == 5


@pytest.mark.asyncio
async def test_committer_loop_commits_on_event() -> None:
    """Committer loop reacts to commit_event."""
    consumer = CrmSupermemoryConsumer()
    consumer._consumer = AsyncMock()
    ctx = _ctx()

    with patch.object(CrmSupermemoryConsumer, "_commit_ready_offsets", new=AsyncMock()) as commit:
        loop_task = asyncio.create_task(consumer._committer_loop(ctx=ctx))
        await asyncio.sleep(0)
        ctx.commit_event.set()
        await asyncio.sleep(0.01)
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    commit.assert_awaited()


@pytest.mark.asyncio
async def test_publish_dlq_kafka_disabled() -> None:
    """DLQ publish is skipped when Kafka is disabled."""
    consumer = CrmSupermemoryConsumer(kafka_settings=MagicMock(enabled=False))
    ok = await consumer._publish_dlq(
        ctx=_ctx(),
        message=_message(),
        error=ValueError("bad"),
        attempts=1,
        retryable=False,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_publish_dlq_success() -> None:
    """DLQ envelope is published with partition key from aggregate_id."""
    consumer = CrmSupermemoryConsumer(kafka_settings=MagicMock(enabled=True))
    ctx = _ctx()
    with patch(
        "apps.user_service.app.consumers.crm_supermemory_consumer.publish_graphiti_dlq",
        new=AsyncMock(return_value=None),
    ) as publish:
        ok = await consumer._publish_dlq(
            ctx=ctx,
            message=_message(),
            error=RuntimeError("sync failed"),
            attempts=3,
            retryable=True,
            original_event={"aggregate_id": "agg-1", "event_id": "e1"},
        )
    assert ok is True
    publish.assert_awaited_once()
    assert publish.await_args.kwargs["partition_key"] == "agg-1"


@pytest.mark.asyncio
async def test_publish_dlq_publish_failure() -> None:
    """DLQ publish failure returns False."""
    consumer = CrmSupermemoryConsumer(kafka_settings=MagicMock(enabled=True))
    with patch(
        "apps.user_service.app.consumers.crm_supermemory_consumer.publish_graphiti_dlq",
        new=AsyncMock(side_effect=RuntimeError("kafka down")),
    ):
        ok = await consumer._publish_dlq(
            ctx=_ctx(),
            message=_message(),
            error=RuntimeError("sync failed"),
            attempts=1,
            retryable=True,
        )
    assert ok is False


@pytest.mark.asyncio
async def test_sync_crm_event() -> None:
    """_sync_crm_event acquires connection and delegates to sync service."""
    consumer = CrmSupermemoryConsumer()
    ctx = _ctx()
    ctx.sync_service.process_crm_event = AsyncMock()
    payload = {"event_id": "e1", "organization_id": "org-1"}

    async def _run_retries(op, **_kwargs):
        await op()

    with (
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.run_with_retries",
            new=AsyncMock(side_effect=_run_retries),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.AcquireConnection"
        ) as acquire,
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.shared_settings"
        ) as settings,
    ):
        settings.graphiti.sync_max_retries = 2
        settings.graphiti.sync_retry_base_delay_seconds = 0.1
        settings.graphiti.sync_timeout_seconds = 5
        conn = MagicMock()
        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=False)
        acquire.return_value = acquire_cm
        await consumer._sync_crm_event(ctx=ctx, payload_dict=payload)

    ctx.sync_service.process_crm_event.assert_awaited_once_with(conn, payload)


@pytest.mark.asyncio
async def test_handle_message_no_commit_on_failure() -> None:
    """Failed processing without DLQ does not signal commit."""
    consumer = CrmSupermemoryConsumer()
    ctx = _ctx()
    with patch.object(
        CrmSupermemoryConsumer, "_process_message", new=AsyncMock(return_value=False)
    ):
        await consumer._handle_message(ctx=ctx, message=_message(offset=7))
    assert not ctx.commit_event.is_set()
    assert TopicPartition("crm.events.dev", 0) not in ctx.next_commit


@pytest.mark.asyncio
async def test_consume_forever_disabled() -> None:
    """consume_forever exits early when disabled."""
    consumer = CrmSupermemoryConsumer(kafka_settings=MagicMock(enabled=False))
    with patch.object(CrmSupermemoryConsumer, "_is_enabled", return_value=False):
        await consumer.consume_forever()


@pytest.mark.asyncio
async def test_consume_messages_dispatches_tasks() -> None:
    """Polling loop creates handle tasks for each message."""
    consumer = CrmSupermemoryConsumer()
    ctx = _ctx()

    class _AsyncIter:
        def __init__(self) -> None:
            self._count = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._count >= 1:
                raise StopAsyncIteration
            self._count += 1
            return _message(offset=9)

    consumer._consumer = _AsyncIter()
    with patch.object(CrmSupermemoryConsumer, "_handle_message", new=AsyncMock()) as handle:
        with patch.object(CrmSupermemoryConsumer, "_ensure_capacity", new=AsyncMock()):
            poll_task = asyncio.create_task(consumer._consume_messages(ctx=ctx))
            await asyncio.sleep(0.05)
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task
    handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_crm_supermemory_consumer() -> None:
    """Entrypoint delegates to consume_forever."""
    with patch.object(CrmSupermemoryConsumer, "consume_forever", new=AsyncMock()) as run:
        from apps.user_service.app.consumers.crm_supermemory_consumer import (
            run_crm_supermemory_consumer,
        )

        await run_crm_supermemory_consumer()
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_forever_lifecycle() -> None:
    """consume_forever initializes deps, replays, and shuts down cleanly."""
    consumer = CrmSupermemoryConsumer(kafka_settings=MagicMock(enabled=True))
    mock_kafka_consumer = AsyncMock()

    async def _async_iter():
        if False:  # pragma: no cover
            yield _message()

    mock_kafka_consumer.__aiter__ = lambda self: _async_iter()

    with (
        patch.object(CrmSupermemoryConsumer, "_is_enabled", return_value=True),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.init_graphiti_client",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.KafkaEventService"
        ) as kafka_cls,
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.get_pool",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.GraphitiSyncService",
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.replay_pending_crm_events_on_startup",
            new=AsyncMock(return_value=SimpleNamespace(selected=2, processed=2, failed=0)),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.AcquireConnection"
        ) as acquire,
        patch.object(CrmSupermemoryConsumer, "start", new=AsyncMock()),
        patch.object(CrmSupermemoryConsumer, "stop", new=AsyncMock()),
        patch.object(CrmSupermemoryConsumer, "_consume_messages", new=AsyncMock()) as consume,
    ):
        kafka_svc = kafka_cls.return_value
        kafka_svc.start = AsyncMock()
        kafka_svc.stop = AsyncMock()
        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        acquire_cm.__aexit__ = AsyncMock(return_value=False)
        acquire.return_value = acquire_cm
        consumer._consumer = mock_kafka_consumer

        await consumer.consume_forever()

    consume.assert_awaited_once()
    kafka_svc.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_forever_drains_in_flight() -> None:
    """consume_forever drains in-flight tasks on shutdown."""
    consumer = CrmSupermemoryConsumer(kafka_settings=MagicMock(enabled=True))

    async def _consume(ctx):
        task = asyncio.create_task(asyncio.sleep(0))
        ctx.in_flight.add(task)
        raise asyncio.CancelledError

    with (
        patch.object(CrmSupermemoryConsumer, "_is_enabled", return_value=True),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.init_graphiti_client",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.KafkaEventService"
        ) as kafka_cls,
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.get_pool",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.GraphitiSyncService",
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.replay_pending_crm_events_on_startup",
            new=AsyncMock(return_value=SimpleNamespace(selected=0, processed=0, failed=0)),
        ),
        patch(
            "apps.user_service.app.consumers.crm_supermemory_consumer.AcquireConnection"
        ) as acquire,
        patch.object(CrmSupermemoryConsumer, "start", new=AsyncMock()),
        patch.object(CrmSupermemoryConsumer, "stop", new=AsyncMock()),
        patch.object(CrmSupermemoryConsumer, "_consume_messages", side_effect=_consume),
    ):
        kafka_svc = kafka_cls.return_value
        kafka_svc.start = AsyncMock()
        kafka_svc.stop = AsyncMock()
        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        acquire_cm.__aexit__ = AsyncMock(return_value=False)
        acquire.return_value = acquire_cm
        consumer._consumer = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await consumer.consume_forever()

    kafka_svc.stop.assert_awaited_once()


def test_main_handles_keyboard_interrupt() -> None:
    """CLI main catches KeyboardInterrupt."""
    with patch(
        "apps.user_service.app.consumers.crm_supermemory_consumer.asyncio.run",
        side_effect=KeyboardInterrupt,
    ):
        from apps.user_service.app.consumers.crm_supermemory_consumer import main

        main()
