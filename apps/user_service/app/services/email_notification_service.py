"""Process inbound email webhooks and append messages to the contact Supermemory document."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.services.organization_memory_service import (
    is_organization_memory_enabled,
)
from apps.user_service.app.services.supermemory_sync_service import (
    SupermemorySyncService,
    custom_id_for_entity,
)
from apps.user_service.app.utils.inbound_email_memory import (
    format_attachment_block,
    format_inbound_email_entry,
    merge_contact_content_with_inbound_email,
)
from libs.shared_utils.agentmail_service import (
    AgentMailService,
    normalize_attachment_meta,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.supermemory_service import (
    SupermemoryService,
    container_tag_for_organization,
    is_supermemory_configured,
)

logger = get_logger("email_notification_service")

MESSAGE_RECEIVED_EVENT = "message.received"
_MAX_BODY_CHARS = 100_000
_ENTITY_CONTEXT = (
    "Legal CRM contact record including profile, notes, deals, and appended inbound emails."
)


@dataclass(frozen=True, slots=True)
class InboundEmailProcessResult:
    """Outcome of processing an inbound email webhook."""

    contact_id: str | None
    stored: bool
    supermemory_document_ids: tuple[str, ...] = ()
    skipped_reason: str | None = None


@dataclass(frozen=True, slots=True)
class InboundEmailRecord:
    """Normalized inbound message ready to append on the contact document."""

    message_id: str
    contact_id: str
    from_email: str
    body: str
    subject: str | None
    from_header: str | None
    to: tuple[str, ...]
    thread_id: str | None
    inbox_id: str | None
    received_at: str | None
    attachments: tuple[dict[str, Any], ...] = ()


def _skipped(
    reason: str,
    *,
    contact_id: str | None = None,
) -> InboundEmailProcessResult:
    """Return a non-stored process result with *reason*."""
    return InboundEmailProcessResult(
        contact_id=contact_id,
        stored=False,
        skipped_reason=reason,
    )


def _webhook_event_context(webhook_body: dict[str, Any]) -> tuple[str, str]:
    """Return ``(event_id, event_type)`` for structured logs (``-`` when missing)."""
    event_id = str(webhook_body.get("event_id") or "").strip()
    event_type = str(webhook_body.get("event_type") or "").strip()
    return event_id or "-", event_type or "-"


def _log_inbound_email_skip(
    *,
    organization_id: str,
    reason: str,
    webhook_body: dict[str, Any] | None = None,
    contact_id: str | None = None,
    sender_email: str | None = None,
    message_id: str | None = None,
    custom_id: str | None = None,
) -> InboundEmailProcessResult:
    """Log why Supermemory was not updated and return a skip result."""
    event_id, event_type = _webhook_event_context(webhook_body or {})
    logger.info(
        "inbound_email_supermemory_skipped organization_id=%s reason=%s "
        "event_id=%s event_type=%s contact_id=%s sender=%s message_id=%s custom_id=%s",
        organization_id,
        reason,
        event_id,
        event_type,
        contact_id or "-",
        sender_email or "-",
        message_id or "-",
        custom_id or "-",
    )
    return _skipped(reason, contact_id=contact_id)


def normalize_email_address(raw: str | None) -> str | None:
    """Return a lowercase email from a plain address or ``Name <email>`` header."""
    text = (raw or "").strip()
    if not text:
        return None
    _, addr = parseaddr(text)
    addr = (addr or text).strip().lower()
    return addr if "@" in addr else None


def _first_normalized_address(raw: Any) -> str | None:
    """AgentMail may send addresses as a string or a list."""
    if isinstance(raw, list):
        for item in raw:
            email = normalize_email_address(str(item))
            if email:
                return email
        return None
    return normalize_email_address(str(raw) if raw is not None else None)


def extract_sender_email(webhook_body: dict[str, Any]) -> str | None:
    """Resolve the sender address from an AgentMail-style webhook payload."""
    message = webhook_body.get("message")
    if not isinstance(message, dict):
        return None
    for key in ("from_", "from"):
        if key in message:
            return _first_normalized_address(message.get(key))
    return None


def _extract_body(message: dict[str, Any]) -> str:
    """Prefer provider-cleaned text; fall back to raw body."""
    for key in ("extracted_text", "text", "preview"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text[:_MAX_BODY_CHARS] if len(text) > _MAX_BODY_CHARS else text
    return ""


def _parse_recipients(raw: Any) -> tuple[str, ...]:
    """Normalize ``to`` / ``cc`` recipient fields from string or list."""
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    if isinstance(raw, str) and raw.strip():
        return (raw.strip(),)
    return ()


def _parse_attachments(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Extract normalized attachment metadata from a webhook message."""
    raw = message.get("attachments")
    if not isinstance(raw, list):
        return ()
    parsed: list[dict[str, Any]] = []
    for item in raw:
        meta = normalize_attachment_meta(item)
        if meta:
            parsed.append(meta)
    return tuple(parsed)


def _resolve_thread_id(webhook_body: dict[str, Any], message: dict[str, Any]) -> str | None:
    """Resolve thread id from the webhook thread object or message."""
    thread = webhook_body.get("thread")
    if isinstance(thread, dict):
        thread_id = str(thread.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
    message_thread = str(message.get("thread_id") or "").strip()
    return message_thread or None


def build_inbound_email_record(
    *,
    webhook_body: dict[str, Any],
    sender_email: str,
    contact_id: str,
    include_attachments: bool = False,
) -> InboundEmailRecord | None:
    """Build a normalized inbound email from the webhook payload."""
    message = webhook_body.get("message")
    if not isinstance(message, dict):
        return None

    body = _extract_body(message)
    subject = str(message.get("subject") or "").strip() or None
    if not body and not subject:
        return None

    message_id = str(message.get("message_id") or message.get("smtp_id") or "").strip()
    if not message_id:
        return None

    received_at = str(message.get("timestamp") or message.get("created_at") or "").strip()
    return InboundEmailRecord(
        message_id=message_id,
        contact_id=contact_id,
        from_email=sender_email,
        body=body,
        subject=subject,
        from_header=str(message.get("from_") or message.get("from") or "").strip() or None,
        to=_parse_recipients(message.get("to")),
        thread_id=_resolve_thread_id(webhook_body, message),
        inbox_id=str(message.get("inbox_id") or "").strip() or None,
        received_at=received_at or None,
        attachments=_parse_attachments(message) if include_attachments else (),
    )


class EmailNotificationService:
    """Match inbound senders to contacts and append email content on the contact memory doc."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        *,
        supermemory: SupermemoryService | None = None,
        agentmail: AgentMailService | None = None,
        sync_service: SupermemorySyncService | None = None,
    ) -> None:
        self._db_connection = db_connection
        self._contacts_repo = ContactsRepository(db_connection=db_connection)
        self._supermemory = supermemory or SupermemoryService.from_settings()
        self._agentmail = agentmail or AgentMailService.from_settings()
        self._sync_service = sync_service or SupermemorySyncService(supermemory=self._supermemory)

    @property
    def _attachments_enabled(self) -> bool:
        """True when AgentMail API is configured to download attachment bytes."""
        return self._agentmail.is_configured

    async def process_message_received(
        self,
        *,
        organization_id: str,
        webhook_body: dict[str, Any],
    ) -> InboundEmailProcessResult:
        """Append inbound email content to the contact's Supermemory document."""
        event_id, event_type = _webhook_event_context(webhook_body)
        logger.info(
            "inbound_email_processing_started organization_id=%s event_id=%s event_type=%s "
            "supermemory_configured=%s",
            organization_id,
            event_id,
            event_type,
            is_supermemory_configured(),
        )

        if event_type not in ("-", MESSAGE_RECEIVED_EVENT):
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason=f"unsupported_event_type:{event_type}",
                webhook_body=webhook_body,
            )

        if not is_supermemory_configured():
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason="supermemory_not_configured",
                webhook_body=webhook_body,
            )

        if not await is_organization_memory_enabled(self._db_connection, organization_id):
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason="organization_memory_disabled",
                webhook_body=webhook_body,
            )

        sender_email = extract_sender_email(webhook_body)
        if not sender_email:
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason="missing_sender_email",
                webhook_body=webhook_body,
            )

        contact_id = await self._contacts_repo.get_contact_id_by_email(
            organization_id=organization_id,
            email=sender_email,
        )
        if not contact_id:
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason="contact_not_found",
                webhook_body=webhook_body,
                sender_email=sender_email,
            )

        record = build_inbound_email_record(
            webhook_body=webhook_body,
            sender_email=sender_email,
            contact_id=contact_id,
            include_attachments=self._attachments_enabled,
        )
        if not record:
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason="empty_message_content",
                webhook_body=webhook_body,
                contact_id=contact_id,
                sender_email=sender_email,
            )

        contact_custom_id = custom_id_for_entity("contact", contact_id)
        logger.info(
            "inbound_email_contact_matched organization_id=%s event_id=%s contact_id=%s "
            "sender=%s message_id=%s custom_id=%s",
            organization_id,
            event_id,
            contact_id,
            sender_email,
            record.message_id,
            contact_custom_id,
        )

        append_failure = await self._append_to_contact_document(
            organization_id=organization_id,
            record=record,
        )
        if append_failure:
            return _log_inbound_email_skip(
                organization_id=organization_id,
                reason=append_failure,
                webhook_body=webhook_body,
                contact_id=contact_id,
                sender_email=sender_email,
                message_id=record.message_id,
                custom_id=contact_custom_id,
            )

        logger.info(
            "inbound_email_supermemory_stored organization_id=%s event_id=%s contact_id=%s "
            "sender=%s message_id=%s custom_id=%s",
            organization_id,
            event_id,
            contact_id,
            sender_email,
            record.message_id,
            contact_custom_id,
        )
        return InboundEmailProcessResult(
            contact_id=contact_id,
            stored=True,
            supermemory_document_ids=(contact_custom_id,),
        )

    async def _append_to_contact_document(
        self,
        *,
        organization_id: str,
        record: InboundEmailRecord,
    ) -> str | None:
        """Rebuild CRM snapshot, append the email block, and replace the contact document.

        Returns:
            ``None`` on success, or a stable skip/failure reason string.
        """
        contact_custom_id = custom_id_for_entity("contact", record.contact_id)
        snapshot = await self._sync_service.load_contact_snapshot(
            self._db_connection,
            organization_id=organization_id,
            contact_id=record.contact_id,
        )
        if snapshot is None:
            return "contact_snapshot_not_found"

        base_content, metadata = snapshot
        existing_content = await self._supermemory.get_document_content(
            custom_id=contact_custom_id,
            organization_id=organization_id,
        )

        attachment_blocks = await self._fetch_attachment_blocks(record)
        new_entry = format_inbound_email_entry(
            subject=record.subject,
            from_header=record.from_header,
            from_email=record.from_email,
            to=record.to,
            thread_id=record.thread_id,
            message_id=record.message_id,
            received_at=record.received_at,
            body=record.body,
            attachment_blocks=attachment_blocks or None,
        )
        merged_content, appended = merge_contact_content_with_inbound_email(
            base_content=base_content,
            existing_document_content=existing_content,
            new_entry=new_entry,
            message_id=record.message_id,
        )
        if not appended:
            return "duplicate_message_id"

        logger.info(
            "inbound_email_supermemory_upsert organization_id=%s contact_id=%s "
            "message_id=%s custom_id=%s",
            organization_id,
            record.contact_id,
            record.message_id,
            contact_custom_id,
        )
        await self._supermemory.add_or_replace_document(
            content=merged_content,
            container_tag=container_tag_for_organization(organization_id),
            custom_id=contact_custom_id,
            metadata=metadata,
            entity_context=_ENTITY_CONTEXT,
        )
        return None

    async def _fetch_attachment_blocks(self, record: InboundEmailRecord) -> list[str]:
        """Download and format attachment text blocks when AgentMail is configured."""
        if not self._attachments_enabled or not record.attachments:
            return []
        if not record.inbox_id:
            logger.warning(
                "inbound_email_attachments_skipped_missing_inbox_id contact_id=%s message_id=%s",
                record.contact_id,
                record.message_id,
            )
            return []

        blocks: list[str] = []
        for attachment in record.attachments:
            att_id = str(attachment.get("attachment_id") or "")
            if not att_id:
                continue
            raw_bytes = await self._agentmail.fetch_message_attachment(
                inbox_id=record.inbox_id,
                message_id=record.message_id,
                attachment_id=att_id,
            )
            blocks.append(format_attachment_block(attachment=attachment, raw_bytes=raw_bytes))
        return blocks
