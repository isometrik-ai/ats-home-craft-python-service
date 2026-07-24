"""Unit tests for UnitConfigsService kind-specific validation and step mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ProjectSetupStep, UnitConfigKind
from apps.user_service.app.schemas.project_inventory import (
    CreatePlotConfigItemRequest,
    CreateUnitConfigRequest,
    UpdateUnitConfigRequest,
)
from apps.user_service.app.services.unit_configs_service import UnitConfigsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(user_id="user-1", email="owner@example.com", organization_id="org-1")


def _service() -> UnitConfigsService:
    """Build UnitConfigsService with mocked repo and setup service."""
    svc = UnitConfigsService(db_connection=MagicMock(), user_context=_user_context())
    svc.configs_repo = MagicMock()
    svc.configs_repo.insert_config = AsyncMock(return_value={"id": "c1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock(return_value={"id": "p1"})
    svc.setup_service.complete_step = AsyncMock(
        return_value={"step_key": "x", "status": "completed"}
    )
    return svc


@pytest.mark.asyncio
async def test_create_apartment_config_requires_core_fields():
    """Apartment config without bedrooms/bathrooms/area is rejected."""
    svc = _service()
    body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.APARTMENT,
        name="2BHK",
        code="2BHK",
    )

    with pytest.raises(ValidationException):
        await svc.create_config(project_id="p1", body=body)


@pytest.mark.asyncio
async def test_create_apartment_config_succeeds_with_fields():
    """Apartment config with required fields inserts successfully."""
    svc = _service()
    body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.APARTMENT,
        name="2BHK",
        code="2BHK",
        bedrooms=2,
        bathrooms=2,
        area_sqft=1200,
    )

    result = await svc.create_config(project_id="p1", body=body)

    assert result["id"] == "c1"
    svc.configs_repo.insert_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_plot_config_requires_plot_type():
    """Plot config without plot_type is rejected."""
    svc = _service()
    body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.PLOT,
        name="Plot A",
        code="PA",
    )

    with pytest.raises(ValidationException):
        await svc.create_config(project_id="p1", body=body)


@pytest.mark.asyncio
async def test_complete_config_step_maps_kind_to_step():
    """Completing a config step maps the kind to the right wizard step."""
    svc = _service()

    await svc.complete_config_step(project_id="p1", config_kind=UnitConfigKind.COMMERCIAL.value)

    svc.setup_service.complete_step.assert_awaited_once()
    _, kwargs = svc.setup_service.complete_step.await_args
    assert kwargs["step_key"] == ProjectSetupStep.COMMERCIAL_CONFIG.value


@pytest.mark.asyncio
async def test_complete_config_step_rejects_invalid_kind():
    """An invalid config kind raises ValidationException."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.complete_config_step(project_id="p1", config_kind="invalid")


@pytest.mark.asyncio
async def test_ensure_config_not_found():
    """Missing config raises NotFoundException."""
    svc = _service()
    svc.configs_repo.get_config = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.delete_config(project_id="p1", config_id="missing")


@pytest.mark.asyncio
async def test_update_config_success():
    """update_config merges patch and persists changes."""
    svc = _service()
    svc.configs_repo.get_config = AsyncMock(
        return_value={
            "id": "c1",
            "config_kind": UnitConfigKind.APARTMENT.value,
            "bedrooms": 2,
            "bathrooms": 2,
            "area_sqft": 1000,
            "name": "2BHK",
        }
    )
    svc.configs_repo.update_config = AsyncMock(return_value={"id": "c1", "name": "2BHK Plus"})

    result = await svc.update_config(
        project_id="p1",
        config_id="c1",
        body=UpdateUnitConfigRequest(name="2BHK Plus"),
    )

    assert result["name"] == "2BHK Plus"


@pytest.mark.asyncio
async def test_create_plot_item_rejects_non_plot_config():
    """Plot items require a plot config kind."""
    svc = _service()
    svc.configs_repo.get_config = AsyncMock(
        return_value={"id": "c1", "config_kind": UnitConfigKind.APARTMENT.value}
    )

    with pytest.raises(ValidationException):
        await svc.create_plot_item(
            project_id="p1",
            config_id="c1",
            body=CreatePlotConfigItemRequest(plot_no="P-1", size_sqft=1000),
        )


@pytest.mark.asyncio
async def test_plot_item_and_media_crud():
    """Plot item and media helpers call repo methods after config check."""
    from apps.user_service.app.schemas.enums import ConfigMediaKind, PlotItemStatus
    from apps.user_service.app.schemas.project_inventory import ConfigMediaRequest

    svc = _service()
    svc.configs_repo.get_config = AsyncMock(
        return_value={"id": "c1", "config_kind": UnitConfigKind.PLOT.value}
    )
    svc.configs_repo.insert_plot_item = AsyncMock(return_value={"id": "item-1"})
    svc.configs_repo.list_plot_items = AsyncMock(return_value=[{"id": "item-1"}])
    svc.configs_repo.delete_plot_item = AsyncMock(return_value={"id": "item-1"})
    svc.configs_repo.insert_media = AsyncMock(return_value={"id": "media-1"})
    svc.configs_repo.list_media = AsyncMock(return_value=[{"id": "media-1"}])
    svc.configs_repo.delete_media = AsyncMock(return_value={"id": "media-1"})

    item = await svc.create_plot_item(
        project_id="p1",
        config_id="c1",
        body=CreatePlotConfigItemRequest(
            plot_no="P-1",
            size_sqft=1200,
            status=PlotItemStatus.EMPTY,
        ),
    )
    assert item["id"] == "item-1"

    items = await svc.list_plot_items(project_id="p1", config_id="c1")
    assert len(items) == 1

    deleted_item = await svc.delete_plot_item(project_id="p1", config_id="c1", item_id="item-1")
    assert deleted_item["old_data"]["id"] == "item-1"

    media = await svc.add_media(
        project_id="p1",
        config_id="c1",
        body=ConfigMediaRequest(
            kind=ConfigMediaKind.FLOOR_PLAN,
            path="/media/plan.png",
            mime="image/png",
            size_bytes=100,
        ),
    )
    assert media["id"] == "media-1"

    media_rows = await svc.list_media(project_id="p1", config_id="c1")
    assert media_rows[0]["id"] == "media-1"

    deleted_media = await svc.delete_media(project_id="p1", config_id="c1", media_id="media-1")
    assert deleted_media["old_data"]["id"] == "media-1"
