"""Per-organization Supermemory feature flag.

Reads ``organizations.settings.organization_memory`` (boolean). When false or
missing, the CRM Supermemory consumer skips sync for that tenant.
"""

from __future__ import annotations

import time
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.utils.common_utils import parse_json_field

ORGANIZATION_MEMORY_SETTINGS_KEY = "organization_memory"
_CACHE_TTL_SECONDS = 60.0

# organization_id -> (enabled, monotonic_timestamp)
_flag_cache: dict[str, tuple[bool, float]] = {}


def invalidate_organization_memory_cache(organization_id: str) -> None:
    """Drop the cached flag for an organization.

    Call after updating organization settings so the next sync sees the new value.
    """
    _flag_cache.pop(str(organization_id), None)


async def is_organization_memory_enabled(
    db_connection: asyncpg.Connection,
    organization_id: str,
) -> bool:
    """Return whether Supermemory sync is enabled for the organization.

    Uses a short in-process cache to avoid loading ``organizations.settings`` on
    every Kafka message.
    """
    org_id = str(organization_id)
    now = time.monotonic()
    cached = _flag_cache.get(org_id)
    if cached is not None and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    repo = OrganizationRepository(db_connection=db_connection)
    org_row = await repo.get_organization_by_id(org_id)
    enabled = _parse_organization_memory_flag(org_row)
    _flag_cache[org_id] = (enabled, now)
    return enabled


def _parse_organization_memory_flag(org_row: dict[str, Any] | None) -> bool:
    """Parse ``settings.organization_memory`` from a repository row."""
    if not org_row:
        return False
    settings = parse_json_field(org_row.get("settings"))
    if not isinstance(settings, dict):
        return False
    return settings.get(ORGANIZATION_MEMORY_SETTINGS_KEY) is True
