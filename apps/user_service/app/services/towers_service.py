"""Towers service: towers, wings, gates, lifts, floors, and step completion."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.towers_repository import TowersRepository
from apps.user_service.app.schemas.enums import ProjectSetupStep
from apps.user_service.app.schemas.project_setup import (
    CreateFloorRequest,
    CreateTowerGateRequest,
    CreateTowerLiftRequest,
    CreateTowerRequest,
    CreateTowerWingRequest,
    UpdateTowerRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class TowersService:
    """Business logic for the tower builder step."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.towers_repo = TowersRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from the user context."""
        return self.user_context.organization_id

    async def _ensure_tower(self, *, project_id: str, tower_id: str) -> dict[str, Any]:
        """Return the tower row scoped to org + project or raise 404."""
        await self.setup_service.ensure_project(project_id=project_id)
        tower = await self.towers_repo.get_tower(
            organization_id=self._org_id, project_id=project_id, tower_id=tower_id
        )
        if not tower:
            raise NotFoundException(
                message_key="project_setup.errors.tower_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return tower

    async def _validate_wing(self, *, tower_id: str, wing_id: str | None) -> None:
        """Ensure an optional wing belongs to the tower."""
        if not wing_id:
            return
        ok = await self.towers_repo.wing_belongs_to_tower(
            organization_id=self._org_id, tower_id=tower_id, wing_id=wing_id
        )
        if not ok:
            raise ValidationException(
                message_key="project_setup.errors.wing_not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    # -- towers -------------------------------------------------------------

    async def create_tower(self, *, project_id: str, body: CreateTowerRequest) -> dict[str, Any]:
        """Create a tower."""
        await self.setup_service.ensure_project(project_id=project_id)
        data = body.model_dump()
        data["tower_type"] = body.tower_type.value
        data["numbering_pattern"] = body.numbering_pattern.value
        data["organization_id"] = self._org_id
        data["project_id"] = project_id
        try:
            inserted = await self.towers_repo.insert_tower(data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(inserted)

    async def list_towers(self, *, project_id: str) -> list[dict[str, Any]]:
        """List towers for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.towers_repo.list_towers(
            organization_id=self._org_id, project_id=project_id
        )
        return [serialize_row(row) for row in rows]

    async def update_tower(
        self, *, project_id: str, tower_id: str, body: UpdateTowerRequest
    ) -> dict[str, Any]:
        """Patch a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "tower_type" in patch and body.tower_type:
            patch["tower_type"] = body.tower_type.value
        if "numbering_pattern" in patch and body.numbering_pattern:
            patch["numbering_pattern"] = body.numbering_pattern.value
        try:
            updated = await self.towers_repo.update_tower(
                organization_id=self._org_id,
                project_id=project_id,
                tower_id=tower_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(updated or {})

    async def delete_tower(self, *, project_id: str, tower_id: str) -> dict[str, Any]:
        """Delete a tower."""
        current = await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        await self.towers_repo.delete_tower(
            organization_id=self._org_id, project_id=project_id, tower_id=tower_id
        )
        return {"old_data": serialize_row(current), "new_data": None}

    # -- wings --------------------------------------------------------------

    async def create_wing(
        self, *, project_id: str, tower_id: str, body: CreateTowerWingRequest
    ) -> dict[str, Any]:
        """Create a wing under a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        data = body.model_dump()
        data["organization_id"] = self._org_id
        data["tower_id"] = tower_id
        try:
            inserted = await self.towers_repo.insert_wing(data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(inserted)

    async def list_wings(self, *, project_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List wings for a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        rows = await self.towers_repo.list_wings(organization_id=self._org_id, tower_id=tower_id)
        return [serialize_row(row) for row in rows]

    async def delete_wing(self, *, project_id: str, tower_id: str, wing_id: str) -> dict[str, Any]:
        """Delete a wing."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        deleted = await self.towers_repo.delete_wing(
            organization_id=self._org_id, tower_id=tower_id, wing_id=wing_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.wing_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": wing_id}, "new_data": None}

    # -- gates --------------------------------------------------------------

    async def create_gate(
        self, *, project_id: str, tower_id: str, body: CreateTowerGateRequest
    ) -> dict[str, Any]:
        """Create a gate under a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        await self._validate_wing(tower_id=tower_id, wing_id=body.wing_id)
        data = body.model_dump()
        data["gate_type"] = body.gate_type.value
        data["status"] = body.status.value
        data["organization_id"] = self._org_id
        data["tower_id"] = tower_id
        inserted = await self.towers_repo.insert_gate(data)
        return serialize_row(inserted)

    async def list_gates(self, *, project_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List gates for a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        rows = await self.towers_repo.list_gates(organization_id=self._org_id, tower_id=tower_id)
        return [serialize_row(row) for row in rows]

    async def delete_gate(self, *, project_id: str, tower_id: str, gate_id: str) -> dict[str, Any]:
        """Delete a gate."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        deleted = await self.towers_repo.delete_gate(
            organization_id=self._org_id, tower_id=tower_id, gate_id=gate_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.gate_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": gate_id}, "new_data": None}

    # -- lifts --------------------------------------------------------------

    async def create_lift(
        self, *, project_id: str, tower_id: str, body: CreateTowerLiftRequest
    ) -> dict[str, Any]:
        """Create a lift under a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        data = body.model_dump()
        data["lift_type"] = body.lift_type.value
        data["status"] = body.status.value
        data["organization_id"] = self._org_id
        data["tower_id"] = tower_id
        inserted = await self.towers_repo.insert_lift(data)
        return serialize_row(inserted)

    async def list_lifts(self, *, project_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List lifts for a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        rows = await self.towers_repo.list_lifts(organization_id=self._org_id, tower_id=tower_id)
        return [serialize_row(row) for row in rows]

    async def delete_lift(self, *, project_id: str, tower_id: str, lift_id: str) -> dict[str, Any]:
        """Delete a lift."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        deleted = await self.towers_repo.delete_lift(
            organization_id=self._org_id, tower_id=tower_id, lift_id=lift_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.lift_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": lift_id}, "new_data": None}

    # -- floors -------------------------------------------------------------

    async def create_floor(
        self, *, project_id: str, tower_id: str, body: CreateFloorRequest
    ) -> dict[str, Any]:
        """Create a floor under a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        await self._validate_wing(tower_id=tower_id, wing_id=body.wing_id)
        data = body.model_dump()
        data["organization_id"] = self._org_id
        data["tower_id"] = tower_id
        try:
            inserted = await self.towers_repo.insert_floor(data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(inserted)

    async def list_floors(self, *, project_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List floors for a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        rows = await self.towers_repo.list_floors(organization_id=self._org_id, tower_id=tower_id)
        return [serialize_row(row) for row in rows]

    async def delete_floor(
        self, *, project_id: str, tower_id: str, floor_id: str
    ) -> dict[str, Any]:
        """Delete a floor."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        deleted = await self.towers_repo.delete_floor(
            organization_id=self._org_id, tower_id=tower_id, floor_id=floor_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.floor_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": floor_id}, "new_data": None}

    # -- step ---------------------------------------------------------------

    async def complete_tower_builder(self, *, project_id: str) -> dict[str, Any]:
        """Mark the tower_builder step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.TOWER_BUILDER.value,
        )
