"""Minimal organization memory helpers (Graphiti removed)."""

from __future__ import annotations

from typing import Any

ORGANIZATION_MEMORY_SETTINGS_KEY = "organization_memory"


def effective_organization_memory_enabled(settings: Any) -> bool:
    """Return whether org memory is enabled from stored settings."""
    if not isinstance(settings, dict):
        return False
    value = settings.get(ORGANIZATION_MEMORY_SETTINGS_KEY)
    return bool(value) if isinstance(value, bool) else False


def invalidate_organization_memory_cache(_organization_id: str) -> None:
    """No-op — in-memory Graphiti cache removed."""
