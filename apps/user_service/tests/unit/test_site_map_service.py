"""Unit tests for SiteMapService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.project_inventory import (
    CreateSiteMapOverlayRequest,
    CreateSiteMapOverlaysRequest,
    UpdateProjectLocationRequest,
)
from apps.user_service.app.services.site_map_service import SiteMapService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
OVERLAY_ID = "770e8400-e29b-41d4-a716-446655440002"


def _ctx() -> UserContext:
    """Build user context for site map tests."""
    return UserContext(
        user_id="u1",
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


def _service() -> SiteMapService:
    """Build SiteMapService with mocked dependencies."""
    svc = SiteMapService(db_connection=MagicMock(), user_context=_ctx())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.setup_service.complete_step = AsyncMock(return_value={"step_key": "site_map"})
    svc.projects_repo = MagicMock()
    svc.projects_repo.update_project = AsyncMock(
        return_value={"id": PROJECT_ID, "latitude": 19.0, "longitude": 72.0}
    )
    svc.site_map_repo = MagicMock()
    svc.site_map_repo.insert_overlays = AsyncMock(
        return_value=[
            {
                "id": OVERLAY_ID,
                "project_id": PROJECT_ID,
                "entity_type": "tower",
                "entity_id": "tower-1",
                "latitude": 19.0,
                "longitude": 72.0,
            }
        ]
    )
    svc.site_map_repo.list_overlays = AsyncMock(return_value=[{"id": OVERLAY_ID}])
    svc.site_map_repo.delete_overlay = AsyncMock(return_value={"id": OVERLAY_ID})
    return svc


@pytest.mark.asyncio
async def test_update_location() -> None:
    """Update location patches project coordinates."""
    svc = _service()
    result = await svc.update_location(
        project_id=PROJECT_ID,
        body=UpdateProjectLocationRequest(latitude=19.0, longitude=72.0),
    )
    assert result["latitude"] == 19.0
    svc.projects_repo.update_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_overlays() -> None:
    """Create overlays inserts marker rows."""
    svc = _service()
    rows = await svc.create_overlays(
        project_id=PROJECT_ID,
        body=CreateSiteMapOverlaysRequest(
            items=[
                CreateSiteMapOverlayRequest(
                    entity_type="tower",
                    entity_id="tower-1",
                    latitude=19.0,
                    longitude=72.0,
                )
            ]
        ),
    )
    assert rows[0]["id"] == OVERLAY_ID


@pytest.mark.asyncio
async def test_update_location_empty_when_project_missing() -> None:
    """Missing project update returns empty serialized payload."""
    svc = _service()
    svc.projects_repo.update_project = AsyncMock(return_value=None)
    result = await svc.update_location(
        project_id=PROJECT_ID,
        body=UpdateProjectLocationRequest(latitude=19.0, longitude=72.0),
    )
    assert result == {}


@pytest.mark.asyncio
async def test_list_overlays() -> None:
    """List overlays returns serialized rows."""
    svc = _service()
    rows = await svc.list_overlays(project_id=PROJECT_ID)
    assert rows[0]["id"] == OVERLAY_ID


@pytest.mark.asyncio
async def test_delete_overlay_success() -> None:
    """Delete overlay returns audit payload."""
    svc = _service()
    result = await svc.delete_overlay(project_id=PROJECT_ID, overlay_id=OVERLAY_ID)
    assert result["old_data"]["id"] == OVERLAY_ID


@pytest.mark.asyncio
async def test_delete_overlay_not_found() -> None:
    """Missing overlay raises NotFoundException."""
    svc = _service()
    svc.site_map_repo.delete_overlay = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.delete_overlay(project_id=PROJECT_ID, overlay_id=OVERLAY_ID)


@pytest.mark.asyncio
async def test_complete_site_map() -> None:
    """Complete site map delegates to setup service."""
    svc = _service()
    result = await svc.complete_site_map(project_id=PROJECT_ID)
    assert result["step_key"] == "site_map"
