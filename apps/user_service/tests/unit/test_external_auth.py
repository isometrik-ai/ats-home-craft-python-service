"""Unit tests for external auth dependencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from apps.user_service.app.dependencies.external_auth import (
    get_organization_context,
    resolve_external_organization_id,
)
from apps.user_service.app.schemas.enums import OrganizationStatus
from libs.shared_middleware.isometrik_external_auth import IsometrikExternalContext
from libs.shared_utils.http_exceptions import UnauthorizedException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.asyncio
async def test_resolve_external_organization_id_success(monkeypatch):
    """Valid org id sets external actor email on request."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    conn = MagicMock()
    repo = AsyncMock()
    repo.get_organization_by_id.return_value = {
        "id": ORG_ID,
        "name": "Sunrise Heights",
        "status": OrganizationStatus.ACTIVE.value,
    }
    monkeypatch.setattr(
        "apps.user_service.app.dependencies.external_auth.OrganizationRepository",
        lambda db_connection: repo,
    )

    org_id = await resolve_external_organization_id(request, ORG_ID, conn)

    assert org_id == ORG_ID
    assert request.state.external_actor_email == "api@sunrise-heights.com"


@pytest.mark.asyncio
async def test_resolve_external_organization_id_blank_raises():
    """Blank organization id is rejected."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(UnauthorizedException):
        await resolve_external_organization_id(request, "  ", MagicMock())


@pytest.mark.asyncio
async def test_resolve_external_organization_id_deleted_raises(monkeypatch):
    """Deleted organizations are rejected."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    repo = AsyncMock()
    repo.get_organization_by_id.return_value = {
        "id": ORG_ID,
        "status": OrganizationStatus.DELETED.value,
    }
    monkeypatch.setattr(
        "apps.user_service.app.dependencies.external_auth.OrganizationRepository",
        lambda db_connection: repo,
    )
    with pytest.raises(UnauthorizedException):
        await resolve_external_organization_id(request, ORG_ID, MagicMock())


@pytest.mark.asyncio
async def test_get_organization_context_success(monkeypatch):
    """Isometrik project id resolves to organization context."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    repo = AsyncMock()
    repo.get_organization_context_by_isometrik_project_id.return_value = (
        ORG_ID,
        "Sunrise Heights",
    )
    monkeypatch.setattr(
        "apps.user_service.app.dependencies.external_auth.OrganizationRepository",
        lambda db_connection: repo,
    )
    ctx = IsometrikExternalContext(project_id="proj-123", raw={})

    org_id = await get_organization_context(request, MagicMock(), ctx)

    assert org_id == ORG_ID
    assert request.state.external_actor_email == "api@sunrise-heights.com"


@pytest.mark.asyncio
async def test_get_organization_context_not_found_raises(monkeypatch):
    """Unknown Isometrik project id raises unauthorized."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    repo = AsyncMock()
    repo.get_organization_context_by_isometrik_project_id.return_value = None
    monkeypatch.setattr(
        "apps.user_service.app.dependencies.external_auth.OrganizationRepository",
        lambda db_connection: repo,
    )
    ctx = IsometrikExternalContext(project_id="missing", raw={})

    with pytest.raises(UnauthorizedException):
        await get_organization_context(request, MagicMock(), ctx)
