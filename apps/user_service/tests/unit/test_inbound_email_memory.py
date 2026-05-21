"""Unit tests for inbound email Supermemory section helpers."""

from apps.user_service.app.utils.inbound_email_memory import (
    INBOUND_EMAILS_HEADING,
    extract_inbound_emails_section,
    inbound_section_has_message_id,
    merge_contact_content_with_inbound_email,
)


def test_merge_appends_inbound_section() -> None:
    """New emails are appended under ## Inbound emails on the contact document."""
    base = "# Contact: Jane\n\n## Profile\n- Email: jane@example.com"
    merged, appended = merge_contact_content_with_inbound_email(
        base_content=base,
        existing_document_content=None,
        new_entry="### 2026-01-01 — Hello\nMessage ID: msg-1\n\nHi",
        message_id="msg-1",
    )
    assert appended is True
    assert INBOUND_EMAILS_HEADING in merged
    assert "msg-1" in merged


def test_merge_dedupes_message_id() -> None:
    """Duplicate message_id is not appended twice."""
    base = "# Contact: Jane"
    existing = f"{base}\n\n{INBOUND_EMAILS_HEADING}\n\n### Email\nMessage ID: msg-1\n\nHi"
    merged, appended = merge_contact_content_with_inbound_email(
        base_content=base,
        existing_document_content=existing,
        new_entry="### Email 2\nMessage ID: msg-1\n\ndupe",
        message_id="msg-1",
    )
    assert appended is False
    assert inbound_section_has_message_id(extract_inbound_emails_section(merged), "msg-1")
