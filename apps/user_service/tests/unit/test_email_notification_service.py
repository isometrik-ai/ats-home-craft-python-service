"""Unit tests for inbound email webhook processing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.email_notification_service import (
    EmailNotificationService,
    build_inbound_email_record,
    extract_sender_email,
    normalize_email_address,
)
from libs.shared_utils.graphiti_crm_models import (
    ContactSnapshot,
    CrmMetadata,
    custom_id_for_entity,
)

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


def _contact_snapshot() -> ContactSnapshot:
    """Build a minimal contact snapshot for email notification tests."""
    return ContactSnapshot(
        crm_id="contact-1",
        display_name="Sai",
        metadata=CrmMetadata(
            entity_type="contact",
            entity_id="contact-1",
            organization_id="org-1",
            status="active",
            display_name="Sai",
            updated_at=1,
        ),
    )


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
async def test_process_ingests_graphiti_email_episode() -> None:
    """Inbound email is stored as a Graphiti text episode linked to the contact."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value="contact-1")

    graphiti = MagicMock()
    graphiti.episode_exists = AsyncMock(return_value=False)
    graphiti.add_text_episode = AsyncMock()

    sync_service = MagicMock()
    sync_service.load_contact_snapshot = AsyncMock(return_value=_contact_snapshot())
    sync_service.sync_entity = AsyncMock()

    agentmail = MagicMock()
    agentmail.is_configured = False

    service = EmailNotificationService(
        db_connection=MagicMock(),
        graphiti=graphiti,
        agentmail=agentmail,
        sync_service=sync_service,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_graphiti_configured", return_value=True),
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
    graphiti.add_text_episode.assert_awaited_once()
    episode_kwargs = graphiti.add_text_episode.await_args.kwargs
    episode_name = episode_kwargs["name"]
    assert episode_name.startswith("email_")
    assert episode_kwargs["contact_crm_id"] == "contact-1"
    assert "test" in episode_kwargs["body"]


@pytest.mark.asyncio
async def test_process_skips_unknown_contact() -> None:
    """No contact match skips Graphiti writes."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value=None)

    graphiti = MagicMock()
    graphiti.add_text_episode = AsyncMock()

    service = EmailNotificationService(
        db_connection=MagicMock(),
        graphiti=graphiti,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_graphiti_configured", return_value=True),
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
    graphiti.add_text_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_continues_when_graphiti_write_fails() -> None:
    """Graphiti errors are isolated; contact match and skip reason are still returned."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value="contact-1")

    graphiti = MagicMock()
    graphiti.episode_exists = AsyncMock(return_value=False)
    graphiti.add_text_episode = AsyncMock(side_effect=RuntimeError("graph down"))

    sync_service = MagicMock()
    sync_service.load_contact_snapshot = AsyncMock(return_value=_contact_snapshot())
    sync_service.sync_entity = AsyncMock()

    service = EmailNotificationService(
        db_connection=MagicMock(),
        graphiti=graphiti,
        sync_service=sync_service,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_graphiti_configured", return_value=True),
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
    assert result.skipped_reason == "graphiti_write_failed"


def test_normalize_email_address_blank_and_invalid():
    """normalize_email_address handles blank and non-email strings."""
    assert normalize_email_address(None) is None
    assert normalize_email_address("   ") is None
    assert normalize_email_address("not-an-email") is None


def test_extract_sender_email_missing_message():
    """extract_sender_email returns None when message object absent."""
    assert extract_sender_email({"event_type": "message.received"}) is None


def test_build_inbound_email_record_requires_content_and_message_id():
    """Record builder rejects empty content and missing message id."""
    body = {
        "message": {
            "from_": "user@example.com",
            "subject": "",
            "extracted_text": "",
        }
    }
    assert (
        build_inbound_email_record(
            webhook_body=body,
            sender_email="user@example.com",
            contact_id="contact-1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_process_skips_unsupported_event_type() -> None:
    """Unsupported webhook event types skip ingestion."""
    service = EmailNotificationService(db_connection=MagicMock(), graphiti=MagicMock())
    with patch(f"{_EMAIL_SVC}.is_graphiti_configured", return_value=True):
        result = await service.process_message_received(
            organization_id="org-1",
            webhook_body={"event_type": "message.sent", "message": {"from_": "a@b.com"}},
        )
    assert result.skipped_reason == "unsupported_event_type:message.sent"


@pytest.mark.asyncio
async def test_process_skips_when_graphiti_not_configured() -> None:
    """Graphiti disabled skips ingestion early."""
    service = EmailNotificationService(db_connection=MagicMock(), graphiti=MagicMock())
    with patch(f"{_EMAIL_SVC}.is_graphiti_configured", return_value=False):
        result = await service.process_message_received(
            organization_id="org-1",
            webhook_body=SAMPLE_WEBHOOK,
        )
    assert result.skipped_reason == "graphiti_not_configured"


@pytest.mark.asyncio
async def test_process_skips_duplicate_message_id() -> None:
    """Duplicate Graphiti episodes are skipped."""
    repo = MagicMock()
    repo.get_contact_id_by_email = AsyncMock(return_value="contact-1")
    graphiti = MagicMock()
    graphiti.episode_exists = AsyncMock(return_value=True)
    sync_service = MagicMock()
    sync_service.load_contact_snapshot = AsyncMock(return_value=_contact_snapshot())

    service = EmailNotificationService(
        db_connection=MagicMock(),
        graphiti=graphiti,
        sync_service=sync_service,
    )
    service._contacts_repo = repo

    with (
        patch(f"{_EMAIL_SVC}.is_graphiti_configured", return_value=True),
        patch(
            f"{_EMAIL_SVC}.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await service.process_message_received(
            organization_id="org-1",
            webhook_body=SAMPLE_WEBHOOK,
        )

    assert result.skipped_reason == "duplicate_message_id"


@pytest.mark.asyncio
async def test_fetch_attachment_blocks_when_inbox_missing() -> None:
    """Attachment fetch is skipped when inbox_id is absent."""
    from apps.user_service.app.services.email_notification_service import (
        InboundEmailRecord,
    )

    agentmail = MagicMock()
    agentmail.is_configured = True
    agentmail.fetch_message_attachment = AsyncMock()

    service = EmailNotificationService(
        db_connection=MagicMock(),
        agentmail=agentmail,
    )
    record = InboundEmailRecord(
        message_id="m1",
        contact_id="contact-1",
        from_email="user@example.com",
        body="hello",
        subject="Hi",
        from_header=None,
        to=(),
        thread_id=None,
        inbox_id=None,
        received_at=None,
        attachments=({"attachment_id": "att-1"},),
    )

    blocks = await service._fetch_attachment_blocks(record)
    assert blocks == []
    agentmail.fetch_message_attachment.assert_not_awaited()


def test_extract_sender_email_uses_from_key() -> None:
    """extract_sender_email reads legacy 'from' field when present."""
    body = {"message": {"from": "User <user@example.com>"}}
    assert extract_sender_email(body) == "user@example.com"


def test_email_helper_parsers() -> None:
    """Cover recipient, thread, reference-time, and attachment helpers."""
    from apps.user_service.app.services.email_notification_service import (
        _extract_body,
        _parse_attachments,
        _parse_recipients,
        _parse_reference_time,
        _resolve_thread_id,
    )

    message = {
        "extracted_text": " hello ",
        "attachments": [{"filename": "a.txt", "attachment_id": "1"}],
    }
    assert _extract_body(message) == "hello"
    assert _parse_recipients(["a@b.com"]) == ("a@b.com",)
    assert _parse_recipients("") == ()
    assert _parse_attachments(message)
    assert _resolve_thread_id({"thread": {"thread_id": "t1"}}, message) == "t1"
    assert _parse_reference_time("2026-01-01T00:00:00Z").year == 2026
    assert _parse_reference_time("bad-date").tzinfo is not None
