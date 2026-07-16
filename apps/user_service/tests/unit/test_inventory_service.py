"""Unit tests for inventory summary aggregation and service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import UnitStatus
from apps.user_service.app.services.inventory_service import (
    InventoryService,
    build_inventory_summary,
    is_sold_status,
    is_unsold_status,
    resolve_unit_kind,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException


def _tower(
    *,
    tower_id: str,
    name: str,
    code: str,
    tower_type: str = "residential",
    upper_floor_count: int = 18,
    units_per_floor_default: int = 4,
) -> dict:
    """Build a minimal tower row."""
    return {
        "id": tower_id,
        "name": name,
        "code": code,
        "tower_type": tower_type,
        "upper_floor_count": upper_floor_count,
        "basement_count": 0,
        "units_per_floor_default": units_per_floor_default,
        "active": True,
    }


def _unit(
    *,
    unit_id: str,
    code: str,
    tower_id: str,
    floor_id: str,
    status: str = "occupied",
    config_kind: str | None = "apartment",
    tower_type: str | None = "residential",
) -> dict:
    """Build a minimal joined unit row."""
    return {
        "id": unit_id,
        "code": code,
        "tower_id": tower_id,
        "floor_id": floor_id,
        "config_id": "cfg-1",
        "status": status,
        "sort_order": 1,
        "is_parking": False,
        "plot_item_id": None,
        "config_kind": config_kind,
        "tower_type": tower_type,
    }


def test_status_helpers():
    """Sold/unsold helpers follow the agreed mapping."""
    assert is_sold_status("occupied") is True
    assert is_sold_status("blocked") is True
    assert is_unsold_status("vacant") is True
    assert is_sold_status("vacant") is False
    assert is_unsold_status("occupied") is False


def test_resolve_unit_kind_prefers_config_kind():
    """Config kind wins over tower type when both are present."""
    assert resolve_unit_kind(config_kind="commercial", tower_type="residential") == "commercial"
    assert resolve_unit_kind(config_kind=None, tower_type="residential") == "apartment"


def test_build_inventory_summary_header_and_buildings():
    """Summary aggregates header stats and per-tower sold counts."""
    tower_a = _tower(tower_id="tower-a", name="Tower A", code="A")
    tower_b = _tower(
        tower_id="tower-b",
        name="Retail Plaza",
        code="RP",
        tower_type="commercial",
        upper_floor_count=1,
        units_per_floor_default=4,
    )
    units = [
        _unit(unit_id="u1", code="A-1801", tower_id="tower-a", floor_id="f18", status="occupied"),
        _unit(unit_id="u2", code="A-1802", tower_id="tower-a", floor_id="f18", status="occupied"),
        _unit(unit_id="u3", code="A-0801", tower_id="tower-a", floor_id="f8", status="vacant"),
        _unit(
            unit_id="u4",
            code="RP-101",
            tower_id="tower-b",
            floor_id="f1",
            status="occupied",
            config_kind="commercial",
            tower_type="commercial",
        ),
    ]
    floors = [
        {
            "id": "f18",
            "tower_id": "tower-a",
            "level_number": 18,
            "display_name": "F18",
            "sort_order": 18,
            "is_parking": False,
        }
    ]
    plot_configs = [{"id": "plot-cfg", "name": "Phase 1", "code": "P1"}]
    plot_items = [
        {
            "id": "plot-1",
            "config_id": "plot-cfg",
            "plot_no": "P-01",
            "size_sqft": 2400,
            "status": "empty",
            "is_corner": False,
            "sort_order": 1,
            "unit_id": None,
            "unit_status": None,
        }
    ]

    summary = build_inventory_summary(
        project_id="project-1",
        towers=[tower_a, tower_b],
        units=units,
        floors=floors,
        plot_configs=plot_configs,
        plot_items=plot_items,
    )

    assert summary["project_id"] == "project-1"
    assert summary["header"]["buildings"] == 2
    assert summary["header"]["apartments"] == 3
    assert summary["header"]["commercial"] == 1
    assert summary["header"]["plots"] == 1
    assert summary["header"]["sold_count"] == 3
    assert summary["header"]["unsold_count"] == 1
    assert summary["header"]["sold_percent"] == 75

    building_a = summary["buildings"][0]
    assert building_a["name"] == "Tower A"
    assert building_a["unit_count"] == 3
    assert building_a["sold_count"] == 2
    assert building_a["unsold_count"] == 1

    assert summary["floors"]["tower-a"][0]["display_name"] == "F18"
    assert summary["plot_configs"][0]["items"][0]["plot_no"] == "P-01"


def test_summary_excludes_parking():
    """Parking slots are returned but excluded from inventory totals."""
    tower = _tower(tower_id="tower-a", name="Tower A", code="A")
    units = [
        _unit(unit_id="u1", code="A-1801", tower_id="tower-a", floor_id="f18", status="vacant"),
        {
            "id": "p1",
            "code": "P-001",
            "tower_id": "tower-a",
            "floor_id": "f-b1",
            "config_id": None,
            "status": "vacant",
            "sort_order": 1,
            "is_parking": True,
            "plot_item_id": None,
            "config_kind": None,
            "tower_type": "residential",
        },
    ]

    summary = build_inventory_summary(
        project_id="project-1",
        towers=[tower],
        units=units,
        floors=[],
        plot_configs=[],
        plot_items=[],
    )

    assert summary["header"]["apartments"] == 1
    assert summary["header"]["unsold_count"] == 1
    assert len(summary["units"]) == 2


def _service() -> InventoryService:
    """Build InventoryService with mocked dependencies."""
    svc = InventoryService(db_connection=MagicMock(), user_context=_user_context())
    svc.inventory_repo = MagicMock()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock(return_value={"id": "project-1"})
    return svc


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(user_id="user-1", email="owner@example.com", organization_id="org-1")


@pytest.mark.asyncio
async def test_get_summary_returns_payload():
    """Service loads raw rows and returns the summary payload."""
    svc = _service()
    svc.inventory_repo.list_summary_towers = AsyncMock(
        return_value=[_tower(tower_id="tower-a", name="Tower A", code="A")]
    )
    svc.inventory_repo.list_summary_units = AsyncMock(
        return_value=[
            _unit(
                unit_id="u1", code="A-1801", tower_id="tower-a", floor_id="f18", status="occupied"
            )
        ]
    )
    svc.inventory_repo.list_summary_floors = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_plot_configs = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_plot_items = AsyncMock(return_value=[])

    result = await svc.get_inventory_summary(project_id="project-1")

    assert result["project_id"] == "project-1"
    assert result["header"]["sold_count"] == 1
    assert result["buildings"][0]["unit_count"] == 1
    svc.inventory_repo.list_summary_plot_configs.assert_awaited_once()
    svc.inventory_repo.list_summary_plot_items.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_summary_skips_plot_items():
    """Plot queries are skipped when include_plot_items is false."""
    svc = _service()
    svc.inventory_repo.list_summary_towers = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_units = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_floors = AsyncMock(return_value=[])

    await svc.get_inventory_summary(project_id="project-1", include_plot_items=False)

    svc.inventory_repo.list_summary_plot_configs.assert_not_called()
    svc.inventory_repo.list_summary_plot_items.assert_not_called()


@pytest.mark.asyncio
async def test_get_inventory_summary_rejects_unknown_tower():
    """Unknown tower_id filter returns 404."""
    svc = _service()
    svc.inventory_repo.list_summary_towers = AsyncMock(return_value=[])

    with pytest.raises(NotFoundException):
        await svc.get_inventory_summary(project_id="project-1", tower_id="missing-tower")


@pytest.mark.asyncio
async def test_get_inventory_summary_passes_status_filter():
    """Status filter is forwarded to the repository query."""
    svc = _service()
    svc.inventory_repo.list_summary_towers = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_units = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_floors = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_plot_configs = AsyncMock(return_value=[])
    svc.inventory_repo.list_summary_plot_items = AsyncMock(return_value=[])

    await svc.get_inventory_summary(project_id="project-1", status=UnitStatus.VACANT)

    svc.inventory_repo.list_summary_units.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        tower_id=None,
        status="vacant",
    )
