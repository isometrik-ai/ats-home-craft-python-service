"""Kafka consumer: organization business overview enrichment after create.

Run with other workers::

    python -m apps.user_service.app.consumers.kafka_workers

Or standalone for local debugging::

    python -m apps.user_service.app.consumers.org_enrichment_consumer
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from apps.user_service.app.config.app_settings import KafkaSettings, app_settings
from apps.user_service.app.schemas.enums import KafkaTopics, OrganizationEventType
from apps.user_service.app.services.org_business_overview_enrichment_service import (
    OrgBusinessOverviewEnrichmentService,
    strands_enrichment_enabled,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.isometrik_strands_client import init_strands_http_client
from libs.shared_utils.logger import get_logger
from libs.shared_utils.openai_chat_service import init_openai_http_client

logger = get_logger("org_enrichment_consumer")

_SESSION_TIMEOUT_MS = 30_000
_HEARTBEAT_INTERVAL_MS = 10_000
_MAX_POLL_RECORDS = 1
_ENRICHMENT_BUFFER_SECONDS = 120.0


def _max_poll_interval_ms() -> int:
    """Allow long-running Strands + OpenAI work between polls."""
    iso = shared_settings.isometrik
    strands_budget = float(iso.strands_request_timeout_seconds) * 2
    openai_budget = float(iso.org_overview_openai_timeout_seconds)
    total_seconds = strands_budget + openai_budget + _ENRICHMENT_BUFFER_SECONDS
    return int(max(600.0, total_seconds) * 1000)


def _message_log_context(message: Any) -> dict[str, Any]:
    """Build structured log fields from a Kafka consumer message."""
    return {
        "topic": message.topic,
        "partition": message.partition,
        "offset": message.offset,
    }


class OrgEnrichmentConsumer:
    """Consume org enrichment jobs and run the strands + OpenAI pipeline."""

    __slots__ = ("_kafka_settings", "_consumer", "_topic", "_group_id")

    def __init__(
        self,
        *,
        kafka_settings: KafkaSettings | None = None,
        consumer_group_id: str | None = None,
    ) -> None:
        self._kafka_settings = kafka_settings or app_settings.kafka
        self._consumer: AIOKafkaConsumer | None = None
        self._topic = KafkaTopics.ORG_ENRICHMENT.value
        self._group_id = consumer_group_id or self._kafka_settings.org_enrichment_consumer_group_id

    @staticmethod
    def _is_enabled(kafka_settings: KafkaSettings) -> bool:
        """Return True when Kafka and strands enrichment are both configured."""
        return bool(kafka_settings.enabled and strands_enrichment_enabled())

    @staticmethod
    def _disabled_reason(kafka_settings: KafkaSettings) -> str:
        """Human-readable reason when the consumer will not run."""
        if not kafka_settings.enabled:
            return "kafka_disabled"
        if not strands_enrichment_enabled():
            return "strands_enrichment_not_configured"
        return "unknown"

    def _build_consumer_kwargs(self) -> dict[str, Any]:
        """Assemble aiokafka consumer configuration from app settings."""
        kwargs: dict[str, Any] = {
            "bootstrap_servers": self._kafka_settings.bootstrap_servers,
            "client_id": f"{self._kafka_settings.producer_name}-org-enrichment-consumer",
            "group_id": self._group_id,
            "enable_auto_commit": False,
            "auto_offset_reset": "latest",
            "security_protocol": self._kafka_settings.security_protocol,
            "max_poll_interval_ms": _max_poll_interval_ms(),
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
        """Start the Kafka consumer when enrichment is enabled."""
        if not self._is_enabled(self._kafka_settings):
            logger.info(
                "org_enrichment_consumer_disabled",
                extra={"reason": self._disabled_reason(self._kafka_settings)},
            )
            return
        if self._consumer is not None:
            return
        consumer = AIOKafkaConsumer(self._topic, **self._build_consumer_kwargs())
        await consumer.start()
        self._consumer = consumer
        logger.info(
            "org_enrichment_consumer_started",
            extra={
                "topic": self._topic,
                "group_id": self._group_id,
                "bootstrap_servers": self._kafka_settings.bootstrap_servers,
                "auto_offset_reset": "latest",
                "max_poll_interval_ms": _max_poll_interval_ms(),
                "expected_event_type": OrganizationEventType.ENRICHMENT_REQUESTED.value,
            },
        )

    async def stop(self) -> None:
        """Stop the Kafka consumer and clear the client reference."""
        consumer, self._consumer = self._consumer, None
        if consumer is None:
            return
        try:
            await consumer.stop()
        finally:
            logger.info("org_enrichment_consumer_stopped")

    def _parse_message(self, message: Any) -> dict[str, Any] | None:
        """Decode and validate an enrichment-requested event payload."""
        ctx = _message_log_context(message)
        try:
            payload = json.loads(message.value.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "org_enrichment_consumer_invalid_json",
                extra={**ctx, "error": str(exc)},
            )
            return None
        if not isinstance(payload, dict):
            logger.warning(
                "org_enrichment_consumer_invalid_payload_type",
                extra={**ctx, "payload_type": type(payload).__name__},
            )
            return None
        if payload.get("event_type") != OrganizationEventType.ENRICHMENT_REQUESTED.value:
            logger.info(
                "org_enrichment_consumer_skip_event_type",
                extra={
                    **ctx,
                    "event_type": payload.get("event_type"),
                    "expected_event_type": OrganizationEventType.ENRICHMENT_REQUESTED.value,
                    "event_id": payload.get("event_id"),
                },
            )
            return None
        return payload

    async def _handle_message(self, message: Any) -> None:
        """Run enrichment for a single Kafka message."""
        ctx = _message_log_context(message)
        logger.info(
            "org_enrichment_consumer_message_received",
            extra=ctx,
        )

        payload = self._parse_message(message)
        if payload is None:
            logger.info(
                "org_enrichment_consumer_message_skipped",
                extra=ctx,
            )
            return

        organization_id = str(payload.get("organization_id") or "").strip()
        event_payload = payload.get("payload")
        organization_name = ""
        organization_website: str | None = None
        if isinstance(event_payload, dict):
            organization_name = str(event_payload.get("organization_name") or "").strip()
            website_raw = event_payload.get("organization_website")
            if isinstance(website_raw, str) and website_raw.strip():
                organization_website = website_raw.strip()

        event_id = str(payload.get("event_id") or "")
        logger.info(
            "org_enrichment_consumer_processing_started",
            extra={
                **ctx,
                "event_id": event_id,
                "event_type": payload.get("event_type"),
                "organization_id": organization_id,
                "organization_name": organization_name,
                "organization_website": organization_website,
            },
        )

        if not organization_id or not organization_name:
            logger.warning(
                "org_enrichment_consumer_missing_fields",
                extra={
                    **ctx,
                    "event_id": event_id,
                    "organization_id": organization_id,
                    "organization_name": organization_name,
                },
            )
            return

        started_at = time.monotonic()
        await OrgBusinessOverviewEnrichmentService.process_enrichment_event(
            organization_id=organization_id,
            organization_name=organization_name,
            organization_website=organization_website,
        )
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "org_enrichment_consumer_processing_finished",
            extra={
                **ctx,
                "event_id": event_id,
                "organization_id": organization_id,
                "organization_name": organization_name,
                "elapsed_ms": elapsed_ms,
            },
        )

    async def _commit(self, message: Any) -> None:
        """Commit the next offset after processing a message."""
        assert self._consumer is not None
        topic_partition = TopicPartition(message.topic, message.partition)
        await self._consumer.commit(
            {topic_partition: OffsetAndMetadata(message.offset + 1, "")},
        )
        logger.info(
            "org_enrichment_consumer_offset_committed",
            extra={
                **_message_log_context(message),
                "committed_offset": message.offset + 1,
            },
        )

    async def consume_forever(self) -> None:
        """Run the consumer until cancelled."""
        if not self._is_enabled(self._kafka_settings):
            logger.info(
                "org_enrichment_consumer_disabled_noop",
                extra={"reason": self._disabled_reason(self._kafka_settings)},
            )
            return

        await init_strands_http_client()
        await init_openai_http_client()
        await self.start()
        assert self._consumer is not None

        logger.info(
            "org_enrichment_consumer_polling",
            extra={
                "topic": self._topic,
                "group_id": self._group_id,
                "hint": (
                    "create a new organization after this log; "
                    "auto_offset_reset=latest skips older messages"
                ),
            },
        )

        try:
            async for message in self._consumer:
                try:
                    await self._handle_message(message)
                except Exception:
                    logger.exception(
                        "org_enrichment_consumer_message_error",
                        extra=_message_log_context(message),
                    )
                finally:
                    try:
                        await self._commit(message)
                    except Exception:
                        logger.exception(
                            "org_enrichment_consumer_commit_error",
                            extra=_message_log_context(message),
                        )
        finally:
            await self.stop()


async def run_org_enrichment_consumer() -> None:
    """Entrypoint for the org enrichment Kafka worker."""
    consumer = OrgEnrichmentConsumer()
    await consumer.consume_forever()


def main() -> None:
    """CLI entrypoint for standalone consumer debugging."""
    try:
        asyncio.run(run_org_enrichment_consumer())
    except KeyboardInterrupt:
        logger.info("org_enrichment_consumer_interrupted")


if __name__ == "__main__":
    main()
