"""Unit tests for inbound email Supermemory section helpers."""

from unittest.mock import patch

from apps.user_service.app.utils.inbound_email_memory import (
    INBOUND_EMAILS_HEADING,
    extract_inbound_emails_section,
    format_attachment_block,
    format_inbound_email_entry,
    inbound_section_has_message_id,
    merge_contact_content_with_inbound_email,
    strip_inbound_emails_section,
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


def test_extract_returns_empty_when_heading_missing() -> None:
    """Documents without the inbound heading yield an empty section."""
    assert extract_inbound_emails_section("# Contact\n\n## Profile") == ""


def test_strip_removes_inbound_section() -> None:
    """strip_inbound_emails_section removes the heading and trailing body."""
    content = f"# Contact\n\n{INBOUND_EMAILS_HEADING}\n\n### Email\nMessage ID: msg-1"
    assert strip_inbound_emails_section(content) == "# Contact"
    assert strip_inbound_emails_section("# Contact only") == "# Contact only"


def test_inbound_section_has_message_id_requires_id() -> None:
    """Empty message_id never matches."""
    assert inbound_section_has_message_id("Message ID: msg-1", "") is False


def test_format_inbound_email_entry_minimal() -> None:
    """Minimal entry uses defaults for subject and from header."""
    entry = format_inbound_email_entry(
        subject=None,
        from_header=None,
        from_email="jane@example.com",
        to=("ops@example.com",),
        thread_id=None,
        message_id="msg-42",
        received_at=None,
        body="",
    )
    assert "(no subject)" in entry
    assert "From: jane@example.com" in entry
    assert "Message ID: msg-42" in entry
    assert "Thread ID:" not in entry


def test_format_inbound_email_entry_with_attachments() -> None:
    """Attachments and thread metadata are included when provided."""
    entry = format_inbound_email_entry(
        subject="Hello",
        from_header="Jane <jane@example.com>",
        from_email="jane@example.com",
        to=("a@example.com", "b@example.com"),
        thread_id="thread-1",
        message_id="msg-99",
        received_at="2026-01-01T12:00:00Z",
        body="Body text",
        attachment_blocks=["**file.pdf**\ncontents"],
    )
    assert "Thread ID: thread-1" in entry
    assert "Body text" in entry
    assert "#### Attachments" in entry
    assert "file.pdf" in entry


@patch("apps.user_service.app.utils.inbound_email_memory.attachment_text_for_supermemory")
def test_format_attachment_block(mock_attachment_text) -> None:
    """Attachment blocks delegate text extraction to agentmail helper."""
    mock_attachment_text.return_value = "extracted text"
    block = format_attachment_block(
        attachment={"filename": "doc.pdf", "content_type": "application/pdf", "size": 128},
        raw_bytes=b"%PDF",
    )
    assert "**doc.pdf**" in block
    assert "extracted text" in block
    mock_attachment_text.assert_called_once_with(
        filename="doc.pdf",
        content_type="application/pdf",
        raw_bytes=b"%PDF",
        size=128,
    )


def test_merge_with_existing_prior_inbound() -> None:
    """New entry appends after prior inbound content when message_id is new."""
    base = "# Contact"
    existing = f"{base}\n\n{INBOUND_EMAILS_HEADING}\n\n### Old\nMessage ID: msg-old\n\nHi"
    merged, appended = merge_contact_content_with_inbound_email(
        base_content=base,
        existing_document_content=existing,
        new_entry="### New\nMessage ID: msg-new\n\nBye",
        message_id="msg-new",
    )
    assert appended is True
    assert "msg-old" in merged
    assert "msg-new" in merged
