"""Inventory service: floor_inventory matrix upsert and step completion."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.inventory_repository import (
    InventoryRepository,
)
from apps.user_service.app.schemas.enums import ProjectSetupStep
from apps.user_service.app.schemas.project_inventory import UpsertFloorInventoryRequest
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class InventoryService:
    """Business logic for the inventories step."""

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

    async def complete_inventories(self, *, project_id: str) -> dict[str, Any]:
        """Mark the inventories step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.INVENTORIES.value,
        )
