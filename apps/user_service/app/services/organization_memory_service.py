"""Per-organization Supermemory feature flag and org-memory query access checks."""

from __future__ import annotations

import time
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.utils.common_utils import UserContext, parse_json_field
from libs.shared_middleware.jwt_auth import check_user_access_async
from libs.shared_utils.common_query import (
    COMPANIES_MANAGEMENT_VIEW,
    CONTACTS_MANAGEMENT_VIEW,
    LEADS_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.status_codes import CustomStatusCode

ORGANIZATION_MEMORY_SETTINGS_KEY = "organization_memory"
_CACHE_TTL_SECONDS = 60.0

_CRM_MEMORY_VIEW_PERMISSIONS: tuple[str, ...] = (
    CONTACTS_MANAGEMENT_VIEW,
    COMPANIES_MANAGEMENT_VIEW,
    LEADS_MANAGEMENT_VIEW,
)

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


async def require_org_memory_query_access(
    *,
    db_connection: asyncpg.Connection,
    user_context: UserContext,
) -> None:
    """Require session org + at least one CRM view permission before a memory query.

    Raises:
        ForbiddenException: No organization on session, or no qualifying CRM permission.
    """
    org_id = user_context.organization_id
    if org_id is None:
        raise ForbiddenException(
            message_key="organizations.errors.user_not_a_member_of_any_organization",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    has_access = await check_user_access_async(
        list(_CRM_MEMORY_VIEW_PERMISSIONS),
        user_context.user_id,
        org_id,
        db_connection,
    )
    if not has_access:
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
