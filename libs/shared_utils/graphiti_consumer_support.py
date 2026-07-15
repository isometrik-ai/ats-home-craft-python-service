"""Retry and dead-letter helpers for the CRM Graphiti Kafka consumer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_consumer_support")

_RETRYABLE_ERROR_MARKERS = (
    "connection",
    "timeout",
    "temporarily unavailable",
    "broken pipe",
    "connection reset",
    "service unavailable",
    "too many connections",
)


def is_retryable_sync_error(exc: BaseException) -> bool:
    """Return True when a Graphiti sync failure may succeed on retry."""
    if isinstance(exc, (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_ERROR_MARKERS)


async def run_with_retries(
    operation: Callable[[], Awaitable[None]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
) -> None:
    """Run *operation* with exponential backoff on retryable errors."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            await operation()
            return
        except Exception as exc:
            last_error = exc
            is_last_attempt = attempt >= max_attempts - 1
            if not is_retryable_sync_error(exc) or is_last_attempt:
                raise
            delay = base_delay_seconds * (2**attempt)
            logger.warning(
                "graphiti_sync_retry attempt=%s max_attempts=%s delay_seconds=%s error=%s",
                attempt + 1,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error


def build_graphiti_dlq_envelope(
    *,
    source_topic: str,
    source_partition: int,
    source_offset: int,
    consumer_group_id: str,
    error: BaseException,
    attempts: int,
    retryable: bool,
    original_event: dict[str, Any] | None = None,
    raw_payload: str | None = None,
) -> dict[str, Any]:
    """Build a dead-letter payload for a failed CRM Graphiti sync message."""
    envelope: dict[str, Any] = {
        "dlq_id": str(uuid4()),
        "failed_at": datetime.now(UTC).isoformat(),
        "source_topic": source_topic,
        "source_partition": source_partition,
        "source_offset": source_offset,
        "consumer_group_id": consumer_group_id,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "attempts": attempts,
        "retryable": retryable,
    }
    if original_event is not None:
        envelope["original_event"] = original_event
    if raw_payload is not None:
        envelope["raw_payload"] = raw_payload[:50_000]
    return envelope


async def publish_graphiti_dlq(
    kafka_producer: Any,
    *,
    topic: str,
    envelope: dict[str, Any],
    partition_key: str | None,
) -> None:
    """Publish a dead-letter envelope to Kafka."""
    await kafka_producer.produce_event(
        event=envelope,
        key=partition_key,
        topics=[topic],
    )
    logger.error(
        "graphiti_consumer_dlq_published dlq_id=%s topic=%s partition_key=%s "
        "source_topic=%s source_partition=%s source_offset=%s error_type=%s",
        envelope.get("dlq_id"),
        topic,
        partition_key or "-",
        envelope.get("source_topic"),
        envelope.get("source_partition"),
        envelope.get("source_offset"),
        envelope.get("error_type"),
    )


def decode_crm_event_payload(raw_bytes: bytes) -> dict[str, Any]:
    """Decode and validate a CRM Kafka message body."""
    text = raw_bytes.decode("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("CRM event payload must be a JSON object")
    return payload
