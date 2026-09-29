"""Unit tests for Graphiti consumer retry and DLQ helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.shared_utils.graphiti_consumer_support import (
    build_graphiti_dlq_envelope,
    publish_graphiti_dlq,
    run_with_retries,
)


@pytest.mark.asyncio
async def test_run_with_retries_rejects_invalid_max_attempts():
    """max_attempts must be at least one."""
    with pytest.raises(ValueError, match="max_attempts"):
        await run_with_retries(AsyncMock(), max_attempts=0, base_delay_seconds=0.01)


@pytest.mark.asyncio
async def test_run_with_retries_exhausts_retryable_errors():
    """Retryable errors should be retried until attempts are exhausted."""
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("timeout")

    with pytest.raises(ConnectionError, match="timeout"):
        await run_with_retries(operation, max_attempts=2, base_delay_seconds=0)

    assert calls == 2


def test_build_graphiti_dlq_envelope_includes_raw_payload():
    """Raw payload should be truncated into the DLQ envelope."""
    envelope = build_graphiti_dlq_envelope(
        source_topic="crm.events.dev",
        source_partition=0,
        source_offset=1,
        consumer_group_id="crm-graphiti-sync",
        error=RuntimeError("boom"),
        attempts=2,
        retryable=True,
        raw_payload="x" * 60_000,
    )
    assert len(envelope["raw_payload"]) == 50_000


@pytest.mark.asyncio
async def test_publish_graphiti_dlq_produces_event():
    """DLQ publisher should forward envelope to Kafka producer."""
    producer = MagicMock()
    producer.produce_event = AsyncMock()
    envelope = build_graphiti_dlq_envelope(
        source_topic="crm.events.dev",
        source_partition=3,
        source_offset=9,
        consumer_group_id="crm-graphiti-sync",
        error=ValueError("bad"),
        attempts=1,
        retryable=False,
    )

    await publish_graphiti_dlq(
        producer,
        topic="crm.graphiti.dlq",
        envelope=envelope,
        partition_key="org-1",
    )

    producer.produce_event.assert_awaited_once_with(
        event=envelope,
        key="org-1",
        topics=["crm.graphiti.dlq"],
    )
