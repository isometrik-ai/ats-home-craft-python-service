"""Inventory service: floor_inventory matrix and inventory menu summary."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.inventory_repository import (
    InventoryRepository,
)
from apps.user_service.app.schemas.enums import (
    ProjectSetupStep,
    UnitConfigKind,
    UnitStatus,
)
from apps.user_service.app.schemas.project_inventory import (
    InventorySummaryBuilding,
    InventorySummaryFloor,
    InventorySummaryHeader,
    InventorySummaryPlotConfig,
    InventorySummaryPlotItem,
    InventorySummaryResponse,
    InventorySummaryUnit,
    UpsertFloorInventoryRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import (
    serialize_row,
    serialize_value,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_SOLD_STATUSES = frozenset({UnitStatus.OCCUPIED.value, UnitStatus.BLOCKED.value})
_UNSOLD_STATUSES = frozenset({UnitStatus.VACANT.value})


def is_sold_status(status: str) -> bool:
    """Return True when a unit status counts as sold."""
    return status in _SOLD_STATUSES


def is_unsold_status(status: str) -> bool:
    """Return True when a unit status counts as unsold."""
    return status in _UNSOLD_STATUSES


def resolve_unit_kind(*, config_kind: str | None, tower_type: str | None) -> str | None:
    """Resolve apartment/commercial classification for a unit."""
    if config_kind in {UnitConfigKind.APARTMENT.value, UnitConfigKind.COMMERCIAL.value}:
        return config_kind
    if tower_type == "residential":
        return UnitConfigKind.APARTMENT.value
    if tower_type == "commercial":
        return UnitConfigKind.COMMERCIAL.value
    return config_kind


def build_inventory_summary(
    *,
    project_id: str,
    towers: list[dict[str, Any]],
    units: list[dict[str, Any]],
    floors: list[dict[str, Any]],
    plot_configs: list[dict[str, Any]],
    plot_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate raw inventory rows into the summary response payload."""
    # pylint: disable=too-complex
    inventory_units = [unit for unit in units if not unit.get("is_parking")]

    apartments = 0
    commercial = 0
    sold_count = 0
    unsold_count = 0

    for unit in inventory_units:
        kind = resolve_unit_kind(
            config_kind=unit.get("config_kind"),
            tower_type=unit.get("tower_type"),
        )
        if kind == UnitConfigKind.APARTMENT.value:
            apartments += 1
        elif kind == UnitConfigKind.COMMERCIAL.value:
            commercial += 1

        status = str(unit.get("status", ""))
        if is_sold_status(status):
            sold_count += 1
        elif is_unsold_status(status):
            unsold_count += 1

    sellable_total = sold_count + unsold_count
    sold_percent = round((sold_count / sellable_total) * 100) if sellable_total else 0

    plots = (
        len(plot_items)
        if plot_items
        else sum(1 for unit in inventory_units if unit.get("plot_item_id"))
    )

    units_by_tower: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in inventory_units:
        tower_id = unit.get("tower_id")
        if tower_id:
            units_by_tower[str(tower_id)].append(unit)

    buildings: list[InventorySummaryBuilding] = []
    for tower in towers:
        tower_id = str(tower["id"])
        tower_units = units_by_tower.get(tower_id, [])
        buildings.append(
            InventorySummaryBuilding(
                id=tower_id,
                name=str(tower["name"]),
                code=str(tower["code"]),
                tower_type=str(tower["tower_type"]),
                upper_floor_count=int(tower.get("upper_floor_count") or 0),
                basement_count=int(tower.get("basement_count") or 0),
                units_per_floor_default=tower.get("units_per_floor_default"),
                unit_count=len(tower_units),
                sold_count=sum(1 for unit in tower_units if is_sold_status(str(unit["status"]))),
                unsold_count=sum(
                    1 for unit in tower_units if is_unsold_status(str(unit["status"]))
                ),
                active=bool(tower.get("active", True)),
            )
        )

    summary_units = [
        InventorySummaryUnit(
            id=str(unit["id"]),
            code=str(unit["code"]),
            tower_id=str(unit["tower_id"]) if unit.get("tower_id") else None,
            floor_id=str(unit["floor_id"]) if unit.get("floor_id") else None,
            config_id=str(unit["config_id"]) if unit.get("config_id") else None,
            config_kind=unit.get("config_kind"),
            status=str(unit["status"]),
            sort_order=int(unit.get("sort_order") or 0),
            is_parking=bool(unit.get("is_parking")),
            plot_item_id=str(unit["plot_item_id"]) if unit.get("plot_item_id") else None,
        )
        for unit in units
    ]

    floors_by_tower: dict[str, list[InventorySummaryFloor]] = defaultdict(list)
    for floor in floors:
        tower_id = str(floor["tower_id"])
        floors_by_tower[tower_id].append(
            InventorySummaryFloor(
                id=str(floor["id"]),
                level_number=int(floor["level_number"]),
                display_name=str(floor["display_name"]),
                sort_order=int(floor.get("sort_order") or 0),
                is_parking=bool(floor.get("is_parking")),
            )
        )

    items_by_config: dict[str, list[InventorySummaryPlotItem]] = defaultdict(list)
    for item in plot_items:
        config_id = str(item["config_id"])
        items_by_config[config_id].append(
            InventorySummaryPlotItem(
                id=str(item["id"]),
                plot_no=str(item["plot_no"]),
                size_sqft=float(item["size_sqft"]),
                description=item.get("description"),
                status=str(item["status"]),
                is_corner=bool(item.get("is_corner")),
                sort_order=int(item.get("sort_order") or 0),
                unit_id=str(item["unit_id"]) if item.get("unit_id") else None,
                unit_status=str(item["unit_status"]) if item.get("unit_status") else None,
            )
        )

    summary_plot_configs = [
        InventorySummaryPlotConfig(
            id=str(config["id"]),
            name=str(config["name"]),
            code=str(config["code"]),
            items=items_by_config.get(str(config["id"]), []),
        )
        for config in plot_configs
    ]

    response = InventorySummaryResponse(
        project_id=project_id,
        header=InventorySummaryHeader(
            buildings=sum(1 for tower in towers if tower.get("active", True)),
            apartments=apartments,
            commercial=commercial,
            plots=plots,
            sold_count=sold_count,
            unsold_count=unsold_count,
            sold_percent=sold_percent,
        ),
        buildings=buildings,
        units=summary_units,
        floors=dict(floors_by_tower),
        plot_configs=summary_plot_configs,
    )
    return response.model_dump()


class InventoryService:
    """Business logic for the inventories step and inventory menu."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.inventory_repo = InventoryRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def upsert_inventory(
        self, *, project_id: str, body: UpsertFloorInventoryRequest
    ) -> list[dict[str, Any]]:
        """Validate references then upsert the inventory matrix."""
        await self.setup_service.ensure_project(project_id=project_id)
        tower_ids = [item.tower_id for item in body.items]
        floor_ids = [item.floor_id for item in body.items]
        config_ids = [item.config_id for item in body.items]
        quantities = [item.quantity for item in body.items]

        valid = await self.inventory_repo.references_valid(
            organization_id=self._org_id,
            project_id=project_id,
            tower_ids=tower_ids,
            floor_ids=floor_ids,
            config_ids=config_ids,
        )
        if not valid:
            raise ValidationException(
                message_key="project_setup.errors.invalid_reference",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        rows = await self.inventory_repo.upsert_items(
            organization_id=self._org_id,
            project_id=project_id,
            tower_ids=tower_ids,
            floor_ids=floor_ids,
            config_ids=config_ids,
            quantities=quantities,
        )
        return [serialize_row(row) for row in rows]

    async def list_inventory(self, *, project_id: str) -> list[dict[str, Any]]:
        """List the inventory matrix for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.inventory_repo.list_inventory(
            organization_id=self._org_id, project_id=project_id
        )
        return [serialize_row(row) for row in rows]

    async def get_inventory_summary(
        self,
        *,
        project_id: str,
        tower_id: str | None = None,
        status: UnitStatus | None = None,
        include_plot_items: bool = True,
    ) -> dict[str, Any]:
        """Build the post-setup inventory menu summary for a project."""
        await self.setup_service.ensure_project(project_id=project_id)

        towers = await self.inventory_repo.list_summary_towers(
            organization_id=self._org_id,
            project_id=project_id,
        )
        if tower_id and not any(str(tower["id"]) == tower_id for tower in towers):
            raise NotFoundException(
                message_key="project_setup.errors.tower_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        status_value = status.value if status else None
        units = await self.inventory_repo.list_summary_units(
            organization_id=self._org_id,
            project_id=project_id,
            tower_id=tower_id,
            status=status_value,
        )
        floors = await self.inventory_repo.list_summary_floors(
            organization_id=self._org_id,
            project_id=project_id,
            tower_id=tower_id,
        )

        plot_configs: list[dict[str, Any]] = []
        plot_items: list[dict[str, Any]] = []
        if include_plot_items:
            plot_configs = await self.inventory_repo.list_summary_plot_configs(
                organization_id=self._org_id,
                project_id=project_id,
            )
            plot_items = await self.inventory_repo.list_summary_plot_items(
                organization_id=self._org_id,
                project_id=project_id,
            )

        serialized_towers = [serialize_row(row) for row in towers]
        serialized_units = [
            {key: serialize_value(val) for key, val in row.items()} for row in units
        ]
        serialized_floors = [serialize_row(row) for row in floors]
        serialized_plot_configs = [serialize_row(row) for row in plot_configs]
        serialized_plot_items = [
            {key: serialize_value(val) for key, val in row.items()} for row in plot_items
        ]

        return build_inventory_summary(
            project_id=project_id,
            towers=serialized_towers,
            units=serialized_units,
            floors=serialized_floors,
            plot_configs=serialized_plot_configs,
            plot_items=serialized_plot_items,
        )

    async def complete_inventories(self, *, project_id: str) -> dict[str, Any]:
        """Mark the inventories step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.INVENTORIES.value,
        )
