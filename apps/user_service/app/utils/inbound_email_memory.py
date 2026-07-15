"""Shared helpers for inbound email blocks inside contact Supermemory documents."""

from __future__ import annotations

from typing import Any

from libs.shared_utils.agentmail_service import attachment_text_for_supermemory

INBOUND_EMAILS_HEADING = "## Inbound emails"
_MESSAGE_ID_MARKER = "Message ID: "


def extract_inbound_emails_section(content: str) -> str:
    """Return the body under ``## Inbound emails``, or empty string."""
    if INBOUND_EMAILS_HEADING not in content:
        return ""
    _, body = content.split(INBOUND_EMAILS_HEADING, 1)
    return body.strip()


def strip_inbound_emails_section(content: str) -> str:
    """Remove the inbound emails section from a document body."""
    if INBOUND_EMAILS_HEADING not in content:
        return content.rstrip()
    return content.split(INBOUND_EMAILS_HEADING, 1)[0].rstrip()


def inbound_section_has_message_id(section_body: str, message_id: str) -> bool:
    """Return True when *message_id* already appears in the inbound emails section."""
    if not message_id:
        return False
    return f"{_MESSAGE_ID_MARKER}{message_id}" in section_body


def format_inbound_email_entry(
    *,
    subject: str | None,
    from_header: str | None,
    from_email: str,
    to: tuple[str, ...],
    thread_id: str | None,
    message_id: str,
    received_at: str | None,
    body: str,
    attachment_blocks: list[str] | None = None,
) -> str:
    """Format one inbound email (and optional attachment text) as markdown."""
    title_subject = subject or "(no subject)"
    header = received_at or "Inbound email"
    lines = [
        f"### {header} — {title_subject}",
        f"From: {from_header or from_email}",
        f"To: {', '.join(to)}",
        f"{_MESSAGE_ID_MARKER}{message_id}",
    ]
    if thread_id:
        lines.append(f"Thread ID: {thread_id}")
    if body:
        lines.extend(["", body])
    if attachment_blocks:
        lines.extend(["", "#### Attachments", *attachment_blocks])
    return "\n".join(lines)


def format_attachment_block(
    *,
    attachment: dict[str, Any],
    raw_bytes: bytes | None,
) -> str:
    """Format one attachment as markdown under an inbound email entry."""
    filename = str(attachment.get("filename") or "attachment")
    text = attachment_text_for_supermemory(
        filename=filename,
        content_type=str(attachment.get("content_type") or ""),
        raw_bytes=raw_bytes,
        size=attachment.get("size") if isinstance(attachment.get("size"), int) else None,
    )
    return f"**{filename}**\n{text}"


def merge_contact_content_with_inbound_email(
    *,
    base_content: str,
    existing_document_content: str | None,
    new_entry: str,
    message_id: str,
) -> tuple[str, bool]:
    """Append *new_entry* to the contact doc's inbound section (deduped by *message_id*).

    Returns:
        (merged_content, appended) — *appended* is False when *message_id* was already stored.
    """
    prior_inbound = ""
    if existing_document_content:
        prior_inbound = extract_inbound_emails_section(existing_document_content)

    if inbound_section_has_message_id(prior_inbound, message_id):
        merged = f"{base_content.rstrip()}\n\n{INBOUND_EMAILS_HEADING}\n\n{prior_inbound}\n"
        return merged, False

    combined = f"{prior_inbound}\n\n{new_entry}".strip() if prior_inbound else new_entry.strip()
    merged = f"{base_content.rstrip()}\n\n{INBOUND_EMAILS_HEADING}\n\n{combined}\n"
    return merged, True
