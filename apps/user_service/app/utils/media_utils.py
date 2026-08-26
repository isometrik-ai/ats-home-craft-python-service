"""Storage path to public media URL helpers."""

from __future__ import annotations

from apps.user_service.app.config.app_settings import shared_settings


def public_media_url(path: str | None) -> str | None:
    """Convert a storage object key/path to a public media URL."""
    if not path:
        return None
    cleaned = str(path).strip()
    if not cleaned:
        return None
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    base = shared_settings.cloudflare_r2.media_url.rstrip("/")
    return f"{base}/{cleaned.lstrip('/')}"
