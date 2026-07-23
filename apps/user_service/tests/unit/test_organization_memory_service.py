"""Unit tests for organization memory feature flag helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.organization_memory_service import (
    ORGANIZATION_MEMORY_SETTINGS_KEY,
    _flag_cache,
    _parse_organization_memory_flag,
    effective_organization_memory_enabled,
    invalidate_organization_memory_cache,
    is_organization_memory_enabled,
    require_org_memory_query_access,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ForbiddenException


def test_effective_organization_memory_enabled():
    """Missing key, null settings, or empty dict all mean enabled."""
    assert effective_organization_memory_enabled(None) is True
    assert effective_organization_memory_enabled({}) is True
    assert effective_organization_memory_enabled({ORGANIZATION_MEMORY_SETTINGS_KEY: None}) is True


def test_effective_memory_enabled_explicit_values():
    """Explicit true/false in settings is respected."""
    assert effective_organization_memory_enabled({ORGANIZATION_MEMORY_SETTINGS_KEY: True}) is True
    assert effective_organization_memory_enabled({ORGANIZATION_MEMORY_SETTINGS_KEY: False}) is False


def test_build_update_payload_when_settings_missing():
    """Toggle persists when organizations.settings is null or has no memory key."""
    service = OrganizationService.__new__(OrganizationService)

    db_payload = service._build_update_payload(
        existing_settings=None,
        update_data={"organization_memory": False},
    )

    assert db_payload["settings"][ORGANIZATION_MEMORY_SETTINGS_KEY] is False

    db_payload_on = service._build_update_payload(
        existing_settings={"practice_areas": {"primary": ["Litigation"]}},
        update_data={"organization_memory": True},
    )

    assert db_payload_on["settings"][ORGANIZATION_MEMORY_SETTINGS_KEY] is True
    assert db_payload_on["settings"]["practice_areas"]["primary"] == ["Litigation"]


def test_effective_organization_memory_non_dict_settings() -> None:
    """Non-dict parsed settings default to enabled."""
    assert effective_organization_memory_enabled([1, 2, 3]) is True


def test_parse_organization_memory_flag_missing_row() -> None:
    """Missing organization row means memory is disabled."""
    assert _parse_organization_memory_flag(None) is False


def test_invalidate_organization_memory_cache() -> None:
    """Cache invalidation removes cached org entries."""
    _flag_cache["org-1"] = (True, 0.0)
    invalidate_organization_memory_cache("org-1")
    assert "org-1" not in _flag_cache


@pytest.mark.asyncio
async def test_is_organization_memory_enabled_uses_cache() -> None:
    """Second lookup within TTL uses in-process cache."""
    _flag_cache.clear()
    db = MagicMock()
    with patch(
        "apps.user_service.app.services.organization_memory_service.OrganizationRepository"
    ) as repo_cls:
        repo_cls.return_value.get_organization_by_id = AsyncMock(
            return_value={"settings": {ORGANIZATION_MEMORY_SETTINGS_KEY: False}}
        )
        first = await is_organization_memory_enabled(db, "org-1")
        second = await is_organization_memory_enabled(db, "org-1")
    assert first is False
    assert second is False
    repo_cls.return_value.get_organization_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_require_org_memory_query_access_no_org() -> None:
    """Memory query requires organization on session."""
    with pytest.raises(ForbiddenException):
        await require_org_memory_query_access(
            db_connection=MagicMock(),
            user_context=UserContext(user_id="u1", email="a@b.com", organization_id=None),
        )


@pytest.mark.asyncio
async def test_require_org_memory_query_access_denied() -> None:
    """Memory query requires CRM view permission."""
    with patch(
        "apps.user_service.app.services.organization_memory_service.check_user_access_async",
        AsyncMock(return_value=False),
    ):
        with pytest.raises(ForbiddenException):
            await require_org_memory_query_access(
                db_connection=MagicMock(),
                user_context=UserContext(user_id="u1", email="a@b.com", organization_id="org-1"),
            )


@pytest.mark.asyncio
async def test_require_org_memory_query_access_granted() -> None:
    """Memory query succeeds when user has CRM permissions."""
    with patch(
        "apps.user_service.app.services.organization_memory_service.check_user_access_async",
        AsyncMock(return_value=True),
    ):
        await require_org_memory_query_access(
            db_connection=MagicMock(),
            user_context=UserContext(user_id="u1", email="a@b.com", organization_id="org-1"),
        )
