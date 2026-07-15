"""Unit tests for AgentMail attachment helpers."""

from libs.shared_utils.agentmail_service import (
    attachment_text_for_supermemory,
    normalize_attachment_meta,
)


def test_normalize_attachment_meta() -> None:
    """Webhook attachment objects normalize to a stable shape."""
    meta = normalize_attachment_meta(
        {
            "attachment_id": "att_1",
            "filename": "doc.pdf",
            "content_type": "application/pdf",
            "size": 100,
        }
    )
    assert meta is not None
    assert meta["attachment_id"] == "att_1"
    assert meta["filename"] == "doc.pdf"


def test_attachment_text_includes_decoded_plaintext() -> None:
    """Text attachments embed decoded content in the Supermemory body."""
    text = attachment_text_for_supermemory(
        filename="notes.txt",
        content_type="text/plain",
        raw_bytes=b"hello world",
        size=11,
    )
    assert "hello world" in text
    assert "notes.txt" in text
