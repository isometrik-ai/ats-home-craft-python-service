"""Unit tests for inbound email webhook processing."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.user_service.app.services.email_notification_service import (
    EmailNotificationService,
    build_inbound_email_record,
    extract_sender_email,
    normalize_email_address,
)
from apps.user_service.app.services.supermemory_sync_service import custom_id_for_entity

_EMAIL_SVC = "apps.user_service.app.services.email_notification_service"

SAMPLE_WEBHOOK = {
    "event_id": "3f70417c-8098-4274-927e-2e8fcc171d19",
    "event_type": "message.received",
    "message": {
        "inbox_id": "david.white@agentmail.to",
        "thread_id": "255f5006-ca48-41f5-b1a1-c83c41046e44",
        "message_id": "<19d8fa831b4.611115d42469770@appscrip.co>",
        "subject": "Re:Your auto shipping expert",
        "from_": "Sai Sandeep <saisandeep@appscrip.co>",
        "to": ["David <david.white@agentmail.to>"],
        "extracted_text": "test",
        "timestamp": "2026-04-15T05:40:57.000Z",
        "attachments": [
            {
                "attachment_id": "att_123",
                "filename": "notes.txt",
                "content_type": "text/plain",
                "size": 12,
            }
        ],
    },
}


def test_normalize_email_address_from_display_name() -> None:
    """Display-name headers resolve to the bare email."""
    assert (
        normalize_email_address("Sai Sandeep <saisandeep@appscrip.co>") == "saisandeep@appscrip.co"
    )


def test_extract_sender_email_from_list() -> None:
    """AgentMail may send from_ as a list of addresses."""
    body = {"message": {"from_": ["saisandeep@appscrip.co"]}}
    assert extract_sender_email(body) == "saisandeep@appscrip.co"


def test_record_includes_attachments() -> None:
    """Attachment metadata is included only when explicitly requested."""
    record = build_inbound_email_record(
        webhook_body=SAMPLE_WEBHOOK,
        sender_email="saisandeep@appscrip.co",
        contact_id="contact-1",
        include_attachments=True,
    )
    assert record is not None
    assert record.body == "test"
    assert len(record.attachments) == 1


def test_record_omits_attachments_default() -> None:
    """Without include_attachments, attachment metadata is not parsed."""
    record = build_inbound_email_record(
        webhook_body=SAMPLE_WEBHOOK,
        sender_email="saisandeep@appscrip.co",
        contact_id="contact-1",
    )
    assert record is not None
    assert not record.attachments


@pytest.mark.asyncio
async def test_process_appends_to_contact_custom_id() -> None:
    """Inbound email is stored on crm:contact:{id}, not a separate document."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value="contact-1")

    supermemory = MagicMock()
    supermemory.get_document_content = AsyncMock(return_value=None)
    supermemory.add_or_replace_document = AsyncMock(return_value={"id": "doc"})

    sync_service = MagicMock()
    sync_service.load_contact_snapshot = AsyncMock(
        return_value=("# Contact: Sai\n", {"entity_type": "contact"})
    )

    agentmail = MagicMock()
    agentmail.is_configured = False

    service = EmailNotificationService(
        db_connection=MagicMock(),
        supermemory=supermemory,
        agentmail=agentmail,
        sync_service=sync_service,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_supermemory_configured", return_value=True),
        patch(
            f"{_EMAIL_SVC}.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await service.process_message_received(
            organization_id="org-1",
            webhook_body=SAMPLE_WEBHOOK,
        )

    assert result.stored is True
    assert result.supermemory_document_ids == (custom_id_for_entity("contact", "contact-1"),)
    supermemory.add_or_replace_document.assert_awaited_once()
    custom_id = supermemory.add_or_replace_document.await_args.kwargs["custom_id"]
    assert custom_id == "crm:contact:contact-1"
    content = supermemory.add_or_replace_document.await_args.kwargs["content"]
    assert "## Inbound emails" in content
    assert "test" in content


@pytest.mark.asyncio
async def test_process_skips_unknown_contact() -> None:
    """No contact match skips Supermemory writes."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value=None)

    supermemory = MagicMock()
    supermemory.add_or_replace_document = AsyncMock()

    service = EmailNotificationService(
        db_connection=MagicMock(),
        supermemory=supermemory,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_supermemory_configured", return_value=True),
        patch(
            f"{_EMAIL_SVC}.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await service.process_message_received(
            organization_id="org-1",
            webhook_body=SAMPLE_WEBHOOK,
        )

    assert result.contact_id is None
    assert result.stored is False
    supermemory.add_or_replace_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_continues_when_supermemory_write_fails() -> None:
    """Supermemory errors are isolated; contact match and skip reason are still returned."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value="contact-1")

    supermemory = MagicMock()
    supermemory.get_document_content = AsyncMock(return_value=None)
    supermemory.add_or_replace_document = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "402 Payment Required",
            request=MagicMock(),
            response=MagicMock(status_code=402),
        )
    )

    sync_service = MagicMock()
    sync_service.load_contact_snapshot = AsyncMock(
        return_value=("# Contact: Sai\n", {"entity_type": "contact"})
    )

    service = EmailNotificationService(
        db_connection=MagicMock(),
        supermemory=supermemory,
        sync_service=sync_service,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_supermemory_configured", return_value=True),
        patch(
            f"{_EMAIL_SVC}.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await service.process_message_received(
            organization_id="org-1",
            webhook_body=SAMPLE_WEBHOOK,
        )

    assert result.contact_id == "contact-1"
    assert result.stored is False
    assert result.skipped_reason == "supermemory_write_failed"
