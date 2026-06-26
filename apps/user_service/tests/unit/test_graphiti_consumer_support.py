"""Unit tests for Graphiti consumer retry/DLQ helpers."""

from __future__ import annotations

import json

import pytest

from libs.shared_utils.graphiti_consumer_support import (
    build_graphiti_dlq_envelope,
    decode_crm_event_payload,
    is_retryable_sync_error,
    run_with_retries,
)


def test_is_retryable_sync_error_connection_errors() -> None:
    """Connection and timeout errors are retryable."""
    assert is_retryable_sync_error(ConnectionError("refused")) is True
    assert is_retryable_sync_error(TimeoutError()) is True
    assert is_retryable_sync_error(ValueError("bad payload")) is False


@pytest.mark.asyncio
async def test_retries_succeed_after_transient_err() -> None:
    """Transient failures should be retried until success."""
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ConnectionError("temporary")

    await run_with_retries(operation, max_attempts=3, base_delay_seconds=0)
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_raise_non_retryable_fast() -> None:
    """Non-retryable errors should fail immediately."""
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        await run_with_retries(operation, max_attempts=3, base_delay_seconds=0)
    assert calls == 1


def test_decode_crm_event_payload_requires_object() -> None:
    """Decoded payloads must be JSON objects."""
    payload = decode_crm_event_payload(
        json.dumps({"event_id": "e1", "organization_id": "org-1"}).encode()
    )
    assert payload["event_id"] == "e1"

    with pytest.raises(ValueError, match="JSON object"):
        decode_crm_event_payload(json.dumps(["bad"]).encode())


def test_dlq_envelope_includes_source_meta() -> None:
    """DLQ envelopes should preserve Kafka source metadata."""
    envelope = build_graphiti_dlq_envelope(
        source_topic="crm.events.dev",
        source_partition=1,
        source_offset=42,
        consumer_group_id="crm-graphiti-sync",
        error=RuntimeError("boom"),
        attempts=3,
        retryable=True,
        original_event={"event_id": "e1"},
    )
    assert envelope["source_offset"] == 42
    assert envelope["error_type"] == "RuntimeError"
    assert envelope["original_event"]["event_id"] == "e1"
