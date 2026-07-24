"""Unit tests for KafkaEventService lifecycle and produce helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.config.app_settings import KafkaSettings
from apps.user_service.app.services import kafka_event_service
from apps.user_service.app.services.kafka_event_service import (
    KafkaEventService,
    get_kafka_event_service,
)


@pytest.fixture(autouse=True)
def reset_kafka_state():
    """Reset module-level producer state between tests."""
    kafka_event_service._state.producer = None
    kafka_event_service._state.closing = False
    yield
    kafka_event_service._state.producer = None
    kafka_event_service._state.closing = False


def _enabled_settings(**overrides) -> KafkaSettings:
    """Build enabled Kafka settings for tests."""
    data = {
        "enabled": True,
        "bootstrap_servers": "localhost:9092",
        "producer_name": "test-producer",
        "security_protocol": "PLAINTEXT",
        "request_timeout_ms": 1000,
        "max_batch_size": 1024,
        "linger_ms": 0,
    }
    data.update(overrides)
    return KafkaSettings(**data)


def test_build_producer_kwargs_includes_optional_security():
    """Producer kwargs include optional SASL and compression settings."""
    settings = _enabled_settings(
        compression_type="gzip",
        sasl_mechanism="PLAIN",
        sasl_username="user",
        sasl_password="secret",
    )
    kwargs = KafkaEventService(settings)._build_producer_kwargs()

    assert kwargs["bootstrap_servers"] == "localhost:9092"
    assert kwargs["compression_type"] == "gzip"
    assert kwargs["sasl_mechanism"] == "PLAIN"
    assert kwargs["sasl_plain_username"] == "user"
    assert kwargs["sasl_plain_password"] == "secret"


@pytest.mark.asyncio
async def test_start_noop_when_disabled():
    """Disabled Kafka settings skip producer startup."""
    service = KafkaEventService(KafkaSettings(enabled=False))

    await service.start()

    assert kafka_event_service._state.producer is None


@pytest.mark.asyncio
async def test_start_raises_when_closing():
    """Start is rejected while shutdown is in progress."""
    kafka_event_service._state.closing = True
    service = KafkaEventService(_enabled_settings())

    with pytest.raises(RuntimeError, match="shutdown is in progress"):
        await service.start()


@pytest.mark.asyncio
async def test_start_and_stop_manage_shared_producer():
    """Start creates producer and stop clears shared state."""
    fake_producer = AsyncMock()
    fake_producer.start = AsyncMock()
    fake_producer.stop = AsyncMock()

    service = KafkaEventService(_enabled_settings())

    with patch(
        "apps.user_service.app.services.kafka_event_service.AIOKafkaProducer",
        return_value=fake_producer,
    ):
        await service.start()
        assert kafka_event_service._state.producer is fake_producer
        await service.stop()

    fake_producer.start.assert_awaited_once()
    fake_producer.stop.assert_awaited_once()
    assert kafka_event_service._state.producer is None
    assert kafka_event_service._state.closing is True


@pytest.mark.asyncio
async def test_produce_event_disabled_returns_none():
    """Disabled settings skip produce_event."""
    service = KafkaEventService(KafkaSettings(enabled=False))

    result = await service.produce_event(event={"type": "noop"}, topics=["crm.events"])

    assert result is None


@pytest.mark.asyncio
async def test_produce_event_requires_topics():
    """Empty topic list raises ValueError."""
    service = KafkaEventService(_enabled_settings())

    with pytest.raises(ValueError, match="non-empty list"):
        await service.produce_event(event={"type": "noop"}, topics=[])


@pytest.mark.asyncio
async def test_produce_event_serializes_json_and_returns_metadata():
    """produce_event serializes payload and returns broker metadata."""
    metadata = MagicMock(topic="crm.events", partition=0, offset=12)
    send_future = asyncio.get_running_loop().create_future()
    send_future.set_result(metadata)

    fake_producer = AsyncMock()
    fake_producer.start = AsyncMock()
    fake_producer.send = AsyncMock(return_value=send_future)
    kafka_event_service._state.producer = fake_producer

    service = KafkaEventService(_enabled_settings())
    result = await service.produce_event(
        event={"type": "lead.created", "id": "lead-1"},
        key="lead-1",
        topics=["crm.events"],
    )

    assert result == [metadata]
    fake_producer.send.assert_awaited_once()
    sent_kwargs = fake_producer.send.await_args.kwargs
    assert sent_kwargs["key"] == b"lead-1"
    assert b"lead.created" in sent_kwargs["value"]


@pytest.mark.asyncio
async def test_produce_event_rejects_non_json_without_serializer():
    """Non-JSON-serializable payloads raise TypeError without serializer hook."""
    fake_producer = AsyncMock()
    fake_producer.send = AsyncMock()
    kafka_event_service._state.producer = fake_producer

    service = KafkaEventService(_enabled_settings())

    with pytest.raises(TypeError):
        await service.produce_event(
            event={"ts": object()},
            topics=["crm.events"],
        )


@pytest.mark.asyncio
async def test_produce_event_uses_custom_serializer():
    """Custom serializer hook can encode non-standard values."""
    metadata = MagicMock(topic="crm.events", partition=0, offset=1)
    send_future = asyncio.get_running_loop().create_future()
    send_future.set_result(metadata)

    fake_producer = AsyncMock()
    fake_producer.send = AsyncMock(return_value=send_future)
    kafka_event_service._state.producer = fake_producer

    service = KafkaEventService(_enabled_settings())
    result = await service.produce_event(
        event={"value": object()},
        topics=["crm.events"],
        serializer=lambda payload: '{"value":"custom"}',
    )

    assert result == [metadata]
    assert fake_producer.send.await_args.kwargs["value"] == b'{"value":"custom"}'


def test_get_kafka_event_service_returns_instance():
    """Dependency helper returns KafkaEventService wrapper."""
    assert isinstance(get_kafka_event_service(), KafkaEventService)
