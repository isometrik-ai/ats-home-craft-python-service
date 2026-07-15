"""Run all user_service Kafka consumers in one process.

Used by the bulk-upload worker entrypoint so contacts import, CRM→Graphiti sync,
and post-create org enrichment share one container / ``python -m`` command.

Production: run this worker as an always-on process when ``KAFKA_ENABLED`` and
``GRAPHITI_ENABLED`` are true::

    python -m apps.user_service.app.consumers.contacts_import_consumer
"""

from __future__ import annotations

import asyncio

from apps.user_service.app.consumers.contacts_import_consumer import (
    ContactsImportConsumer,
)
from apps.user_service.app.consumers.crm_supermemory_consumer import (
    CrmSupermemoryConsumer,
)
from apps.user_service.app.consumers.org_enrichment_consumer import (
    OrgEnrichmentConsumer,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("kafka_workers")


async def run_kafka_workers(*, import_batch_size: int = 1000) -> None:
    """Run contacts-import, CRM Supermemory, and org-enrichment consumers concurrently.

    Each consumer no-ops when its prerequisites are disabled (Kafka off,
    Supermemory off, Strands off, etc.), so a single command is safe in partial configs.
    """
    logger.info("kafka_workers_starting")
    await asyncio.gather(
        ContactsImportConsumer().consume_forever(batch_size=import_batch_size),
        CrmSupermemoryConsumer().consume_forever(),
        OrgEnrichmentConsumer().consume_forever(),
    )
    logger.info("kafka_workers_stopped")
