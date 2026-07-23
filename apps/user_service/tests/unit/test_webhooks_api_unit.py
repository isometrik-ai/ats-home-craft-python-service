"""Unit tests for webhooks API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from apps.user_service.app.api.webhooks import (
    email_notifications_webhook,
    enrichment_webhook,
    whatsapp_notifications_webhook,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/webhooks", "headers": []})


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_enrichment_webhook_invalid_payload_no_request_id() -> None:
    """Missing request_id returns 422."""
    response = await enrichment_webhook(
        request=_request(),
        background_tasks=BackgroundTasks(),
        db_connection=MagicMock(),
        body={},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_enrichment_webhook_invalid_payload_no_enrichment_data() -> None:
    """Payload without company/person enrichment returns 422."""
    response = await enrichment_webhook(
        request=_request(),
        background_tasks=BackgroundTasks(),
        db_connection=MagicMock(),
        body={"request_id": "req-1"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_enrichment_webhook_company_success() -> None:
    """Company enrichment schedules indexing and events."""
    db = MagicMock()
    db.transaction.return_value = _Tx()
    bg = BackgroundTasks()
    enrichment = MagicMock()
    enrichment.process_company_enrichment_webhook = AsyncMock(return_value=("company-1", ORG_ID))

    with patch(
        "apps.user_service.app.api.webhooks.ClientEnrichmentService.from_settings",
        return_value=enrichment,
    ):
        response = await enrichment_webhook(
            request=_request(),
            background_tasks=bg,
            db_connection=db,
            body={
                "request_id": "req-1",
                "enriched_company": {"name": "Acme"},
            },
        )

    assert response.status_code == 200
    assert len(bg.tasks) >= 2


@pytest.mark.asyncio
async def test_enrichment_webhook_person_success() -> None:
    """Person enrichment schedules contact indexing."""
    db = MagicMock()
    db.transaction.return_value = _Tx()
    bg = BackgroundTasks()
    enrichment = MagicMock()
    enrichment.process_person_enrichment_webhook = AsyncMock(return_value=("contact-1", ORG_ID))

    with patch(
        "apps.user_service.app.api.webhooks.ClientEnrichmentService.from_settings",
        return_value=enrichment,
    ):
        response = await enrichment_webhook(
            request=_request(),
            background_tasks=bg,
            db_connection=db,
            body={
                "request_id": "req-1",
                "enriched_profile": {"first_name": "Jane"},
            },
        )

    assert response.status_code == 200
    assert len(bg.tasks) >= 1


@pytest.mark.asyncio
async def test_email_notifications_webhook_persists_event() -> None:
    """Email webhook processes message and publishes lifecycle event."""
    db = MagicMock()
    db.transaction.return_value = _Tx()
    bg = BackgroundTasks()
    process_result = MagicMock(
        contact_id="contact-1",
        stored=True,
        skipped_reason=None,
        supermemory_document_ids=["doc-1"],
    )

    with (
        patch(
            "apps.user_service.app.api.webhooks.EmailNotificationService",
        ) as email_cls,
        patch(
            "apps.user_service.app.api.webhooks.EventService",
        ) as event_cls,
    ):
        email_cls.return_value.process_message_received = AsyncMock(return_value=process_result)
        event_cls.return_value.create_lifecycle_event = AsyncMock(return_value={"id": "evt-1"})

        response = await email_notifications_webhook(
            request=_request(),
            background_tasks=bg,
            db_connection=db,
            organization_id=ORG_ID,
            body={"event_type": "message.received", "event_id": "evt-in"},
        )

    assert response.status_code == 200
    assert len(bg.tasks) == 1


@pytest.mark.asyncio
async def test_whatsapp_notifications_webhook() -> None:
    """WhatsApp webhook delegates to WebhookService."""
    with patch(
        "apps.user_service.app.api.webhooks.WebhookService",
    ) as webhook_cls:
        webhook_cls.return_value.execute_isometrik_whatsapp_workflow = AsyncMock(
            return_value={"ok": True}
        )
        response = await whatsapp_notifications_webhook(
            request=_request(),
            body={"message": "hello"},
        )

    assert response.status_code == 200
