"""Async Kafka producer service for event publishing.

This module exposes a single process-wide AIOKafkaProducer managed through
``KafkaEventService``.  All application modules share the same underlying
producer; the service wrapper is cheap to instantiate and safe to use as a
FastAPI dependency.

Typical FastAPI wiring::

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = KafkaEventService()
        await svc.start()
        yield
        await svc.stop()

    app = FastAPI(lifespan=lifespan)

    @router.post("/orders")
    async def create_order(kafka: KafkaEventService = Depends(get_kafka_event_service)):
        await kafka.produce_event(event={"type": "order.created", "id": "..."})
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aiokafka import AIOKafkaProducer

from apps.user_service.app.config.app_settings import KafkaSettings, app_settings

logger = logging.getLogger(__name__)


# Module-level producer state
@dataclass(slots=True)
class _ProducerState:
    """Process-global holder for the shared AIOKafkaProducer.

    Only one producer is created per process.  All ``KafkaEventService``
    instances share this state via the module-level ``_state`` singleton.

    Attributes:
        producer: The live AIOKafkaProducer, or ``None`` when stopped.
        lock:     Asyncio lock that serialises producer start/stop.
        closing:  Set to ``True`` by ``stop()`` before teardown begins.
                  Once set, ``start()`` and ``produce_event()`` will raise
                  immediately rather than creating a new producer or sending
                  messages into a shutting-down broker connection.
    """

    producer: Any | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closing: bool = False


_state = _ProducerState()


# Service
class KafkaEventService:
    """Process-wide Kafka producer service.

    Wraps an ``AIOKafkaProducer`` with lifecycle management, structured
    logging, and a clean FastAPI dependency interface.  The underlying
    producer is shared across all instances via module-level state; this
    class is intentionally cheap to construct.

    The service enforces strict JSON serialisation: payloads must be fully
    serialisable without any implicit coercion.  Callers that need custom
    types (``datetime``, ``UUID``, ``Decimal``, etc.) should either
    serialise them before calling ``produce_event`` or supply a
    ``serializer`` callable.

    Example::

        svc = KafkaEventService()
        await svc.start()
        await svc.produce_event(event={"order_id": str(uuid4()), "amount": "9.99"})
        await svc.stop()
    """

    __slots__ = ("_settings",)

    def __init__(self, settings: KafkaSettings | None = None) -> None:
        """Initialise the service with optional settings override.

        Args:
            settings: Kafka configuration.  Defaults to
                      ``app_settings.kafka`` when omitted, which is the
                      correct choice for all production code.  Pass an
                      explicit value in tests to avoid touching global state.
        """
        self._settings: KafkaSettings = settings or app_settings.kafka

    # Lifecycle
    def _build_producer_kwargs(self) -> dict[str, Any]:
        """Assemble the constructor kwargs for ``AIOKafkaProducer``.

        Starts from the mandatory fields common to all deployments, then
        layers in optional security and tuning fields only when they are
        set.  Keeping this separate from ``start()`` reduces its McCabe
        complexity and makes the configuration surface easy to test in
        isolation.

        Returns:
            A kwargs dict ready to be unpacked into ``AIOKafkaProducer()``.
        """
        kwargs: dict[str, Any] = {
            "bootstrap_servers": self._settings.bootstrap_servers,
            "client_id": self._settings.producer_name,
            "acks": "all",
            "enable_idempotence": True,
            "request_timeout_ms": self._settings.request_timeout_ms,
            "max_batch_size": self._settings.max_batch_size,
            "linger_ms": self._settings.linger_ms,
            "security_protocol": self._settings.security_protocol,
        }

        if self._settings.compression_type:
            kwargs["compression_type"] = self._settings.compression_type
        if self._settings.sasl_mechanism:
            kwargs["sasl_mechanism"] = self._settings.sasl_mechanism
        if self._settings.sasl_username:
            kwargs["sasl_plain_username"] = self._settings.sasl_username
        if self._settings.sasl_password:
            kwargs["sasl_plain_password"] = self._settings.sasl_password

        return kwargs

    async def start(self) -> None:
        """Connect the producer to the Kafka cluster.

        Idempotent — safe to call multiple times.  The second and
        subsequent calls return immediately without acquiring the lock.

        Raises:
            RuntimeError: If called after ``stop()`` has begun (i.e. the
                          shutdown flag is set), or if ``aiokafka`` is not
                          installed.
        """
        if not self._settings.enabled:
            logger.info("kafka_producer_disabled")
            return

        # Reject any attempt to (re-)start during or after shutdown.
        if _state.closing:
            raise RuntimeError("Cannot start the Kafka producer: shutdown is in progress.")

        if _state.producer is not None:
            return

        async with _state.lock:
            # Re-check both flags after acquiring the lock — another
            # coroutine may have raced through start() or begun stop().
            if _state.closing:
                raise RuntimeError("Cannot start the Kafka producer: shutdown is in progress.")
            if _state.producer is not None:
                return

            producer = AIOKafkaProducer(**self._build_producer_kwargs())
            await producer.start()
            _state.producer = producer

        logger.info(
            "kafka_producer_started",
            extra={
                "bootstrap_servers": self._settings.bootstrap_servers,
                "default_topic": self._settings.default_topic,
            },
        )

    async def stop(self) -> None:
        """Flush in-flight messages and disconnect from the cluster.

        Sets the ``closing`` flag *before* acquiring the lock so that any
        concurrent ``start()`` or ``produce_event()`` calls fail fast
        rather than racing with teardown.

        The producer reference is cleared under the lock; the actual
        ``producer.stop()`` coroutine runs outside it so other coroutines
        are not blocked during the broker flush.  ``asyncio.shield()``
        ensures that a cancellation from FastAPI's shutdown sequence does
        not interrupt the flush mid-way.

        Safe to call when the producer was never started or is already
        stopped.
        """
        # Raise the flag before taking the lock so concurrent callers see
        # it immediately without waiting.
        _state.closing = True

        async with _state.lock:
            if _state.producer is None:
                return
            producer, _state.producer = _state.producer, None

        # Flush and close outside the lock; shield from external cancellation.
        await asyncio.shield(producer.stop())
        logger.info("kafka_producer_stopped")

    # Producing
    async def produce_event(
        self,
        *,
        event: Mapping[str, Any],
        key: str | None = None,
        topic: str | None = None,
        headers: list[tuple[str, bytes]] | None = None,
        timeout: float | None = None,
        serializer: Callable[[Any], str] | None = None,
    ) -> Any | None:
        """Publish *event* as a JSON message to Kafka.

        Serialisation is strict by default: if *event* contains types that
        the standard ``json`` module cannot handle (``datetime``, ``UUID``,
        ``Decimal``, …) a ``TypeError`` is raised immediately.  Use the
        *serializer* hook to handle custom types rather than relying on
        silent coercion.

        Args:
            event:      Payload to publish.  Must be a JSON-serialisable
                        mapping unless *serializer* is supplied.
            key:        Optional partition key.  Messages with the same key
                        are routed to the same partition, preserving order.
            topic:      Target topic.  Falls back to the default topic from
                        settings when omitted.
            headers:    Optional list of ``(header_name, value_bytes)``
                        tuples attached to the Kafka message.
            timeout:    Seconds to wait for the broker acknowledgement.
                        ``None`` waits indefinitely (recommended for most
                        use-cases; pair with ``request_timeout_ms`` in
                        settings instead).
            serializer: Optional callable that accepts the event mapping and
                        returns a JSON string.  Use this to handle custom
                        types such as ``datetime`` or ``UUID`` without
                        mutating the original payload::

                            import orjson
                            await svc.produce_event(
                                event={"ts": datetime.utcnow()},
                                serializer=lambda e: orjson.dumps(e).decode(),
                            )

        Returns:
            ``RecordMetadata`` on success (contains topic, partition, and
            offset), or ``None`` when Kafka is disabled in settings.

        Raises:
            RuntimeError:  If the producer is unavailable after startup, or
                           if ``stop()`` has already been called.
            TypeError:     If *event* contains non-JSON-serialisable values
                           and no *serializer* is provided.
        """
        if not self._settings.enabled:
            logger.info("kafka_producer_skipped_disabled")
            return None

        if _state.closing:
            raise RuntimeError("Cannot produce event: Kafka producer is shutting down.")

        await self.start()

        producer = _state.producer
        if producer is None:
            raise RuntimeError("Kafka producer is unavailable after startup.")

        resolved_topic = topic or self._settings.default_topic

        # Serialise strictly — no silent coercion via default=str.
        # Callers own their types; use the serializer hook for custom ones.
        if serializer is not None:
            value = serializer(event).encode()
        else:
            value = json.dumps(event, separators=(",", ":")).encode()

        encoded_key = key.encode() if key else None

        # ``producer.send()`` schedules the message and returns an
        # asyncio.Future that resolves when the broker sends an ack.
        # ``asyncio.shield()`` prevents a timeout cancellation from
        # also cancelling the Future — the message may already be in
        # the broker's buffer even if we stop waiting for the ack.
        fut = await producer.send(
            resolved_topic,
            value=value,
            key=encoded_key,
            headers=headers,
        )

        if timeout is None:
            metadata = await fut
        else:
            metadata = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)

        logger.info(
            "kafka_event_produced",
            extra={
                "topic": metadata.topic,
                "partition": metadata.partition,
                "offset": metadata.offset,
                "key_present": key is not None,
            },
        )
        return metadata


# Dependency helper
def get_kafka_event_service() -> KafkaEventService:
    """Return a ``KafkaEventService`` instance for use as a FastAPI dependency.

    Creates a fresh, lightweight wrapper on every call.  The wrapper holds
    only settings; the expensive resource (the AIOKafkaProducer) is
    process-global and managed via the lifespan hook, not here.

    Because this returns a new object each time, it is safe to override in
    tests via ``app.dependency_overrides`` without any global state leak::

        app.dependency_overrides[get_kafka_event_service] = lambda: FakeKafka()

    Returns:
        A ``KafkaEventService`` configured from ``app_settings.kafka``.
    """
    return KafkaEventService()
