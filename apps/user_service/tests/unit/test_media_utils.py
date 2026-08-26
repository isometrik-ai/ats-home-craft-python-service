"""Tests for media URL helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from apps.user_service.app.utils.media_utils import public_media_url


def test_public_media_url_returns_none_for_empty_values() -> None:
    assert public_media_url(None) is None
    assert public_media_url("") is None
    assert public_media_url("   ") is None


def test_public_media_url_keeps_absolute_urls() -> None:
    url = public_media_url("https://cdn.example.com/photo.jpg")
    assert url == "https://cdn.example.com/photo.jpg"


@patch("apps.user_service.app.utils.media_utils.shared_settings")
def test_public_media_url_prefixes_object_keys(mock_settings: MagicMock) -> None:
    mock_settings.cloudflare_r2.media_url = "https://media.example.com"
    url = public_media_url("org/daily-help/photo_lakshmi.jpg")
    assert url == "https://media.example.com/org/daily-help/photo_lakshmi.jpg"
