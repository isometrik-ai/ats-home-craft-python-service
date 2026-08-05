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
from apps.user_service.app.services.project_setup_validation import (
    validate_tower_numbering,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_TOWER_CODE_MAX_LEN = 64
_TOWER_CODE_SUFFIX_RESERVE = 6
_TOWER_CODE_MAX_ATTEMPTS = 1000
_NESTED_TOWER_CREATE_KEYS = frozenset({"wings", "gates", "lifts", "floors"})


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

    @staticmethod
    def _slugify_tower_name(name: str) -> str:
        """Build a URL-friendly tower code base from the display name."""
        clean_name = name.lower().strip()
        normalized = "".join(char if char.isalnum() else "-" for char in clean_name)
        compact = "-".join(part for part in normalized.split("-") if part)
        return compact[:_TOWER_CODE_MAX_LEN]

    async def _resolve_tower_code(
        self,
        *,
        project_id: str,
        name: str,
        code: str | None,
    ) -> str:
        """Use the provided code or generate a unique slug from the tower name."""
        explicit_code = (code or "").strip()
        if explicit_code:
            return explicit_code

        base = self._slugify_tower_name(name) or "tower"
        max_base_len = _TOWER_CODE_MAX_LEN - _TOWER_CODE_SUFFIX_RESERVE
        candidate_base = base[:max_base_len].rstrip("-") or "tower"
        candidate = candidate_base
        suffix = 2

        for _ in range(_TOWER_CODE_MAX_ATTEMPTS):
            if not await self.towers_repo.tower_code_exists(project_id=project_id, code=candidate):
                return candidate
            suffix_text = f"-{suffix}"
            trimmed_base = candidate_base[: _TOWER_CODE_MAX_LEN - len(suffix_text)].rstrip("-")
            candidate = f"{trimmed_base or 'tower'}{suffix_text}"
            suffix += 1

        raise InternalServerErrorException(
            message_key="project_setup.errors.duplicate_code",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    @staticmethod
    def _register_wing_lookup_keys(
        *,
        wing_body: CreateTowerWingRequest,
        wing_row: dict[str, Any],
        key_map: dict[str, str],
    ) -> None:
        """Index a created wing by code and name for nested references."""
        wing_id = str(wing_row["id"])
        if wing_body.code:
            key_map[wing_body.code.strip()] = wing_id
        key_map[wing_body.name.strip()] = wing_id

    def _resolve_nested_wing_id(
        self,
        *,
        wing_client_key: str | None,
        wing_key_map: dict[str, str],
    ) -> str | None:
        """Resolve a nested wing reference from the same create request."""
        if not wing_client_key:
            return None
        wing_id = wing_key_map.get(wing_client_key.strip())
        if not wing_id:
            raise ValidationException(
                message_key="project_setup.errors.nested_wing_client_key_not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"wing_client_key": wing_client_key.strip()},
            )
        return wing_id

    async def create_tower(self, *, project_id: str, body: CreateTowerRequest) -> dict[str, Any]:
        """Create a tower and optionally nested wings, gates, lifts, and floors."""
        await self.setup_service.ensure_project(project_id=project_id)
        validate_tower_numbering(
            numbering_pattern=body.numbering_pattern.value,
            custom_prefix=body.custom_prefix,
        )
        nested_wings = list(body.wings or [])
        nested_gates = list(body.gates or [])
        nested_lifts = list(body.lifts or [])
        nested_floors = list(body.floors or [])
        data = body.model_dump(exclude=_NESTED_TOWER_CREATE_KEYS)
        data["code"] = await self._resolve_tower_code(
            project_id=project_id,
            name=body.name,
            code=body.code,
        )
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
        tower_id = str(inserted["id"])
        result = serialize_row(inserted)
        if not any((nested_wings, nested_gates, nested_lifts, nested_floors)):
            return result

        wing_key_map: dict[str, str] = {}
        created_wings: list[dict[str, Any]] = []
        for wing_body in nested_wings:
            wing = await self.create_wing(
                project_id=project_id,
                tower_id=tower_id,
                body=wing_body,
            )
            self._register_wing_lookup_keys(
                wing_body=wing_body,
                wing_row=wing,
                key_map=wing_key_map,
            )
            created_wings.append(wing)

        created_gates: list[dict[str, Any]] = []
        for gate_body in nested_gates:
            gate = await self.create_gate(
                project_id=project_id,
                tower_id=tower_id,
                body=CreateTowerGateRequest(
                    wing_id=self._resolve_nested_wing_id(
                        wing_client_key=gate_body.wing_client_key,
                        wing_key_map=wing_key_map,
                    ),
                    name=gate_body.name,
                    gate_type=gate_body.gate_type,
                    status=gate_body.status,
                    is_open_24x7=gate_body.is_open_24x7,
                    operating_hours=gate_body.operating_hours,
                    sort_order=gate_body.sort_order,
                ),
            )
            created_gates.append(gate)

        created_lifts: list[dict[str, Any]] = []
        for lift_body in nested_lifts:
            lift = await self.create_lift(
                project_id=project_id,
                tower_id=tower_id,
                body=lift_body,
            )
            created_lifts.append(lift)

        created_floors: list[dict[str, Any]] = []
        for floor_body in nested_floors:
            floor = await self.create_floor(
                project_id=project_id,
                tower_id=tower_id,
                body=CreateFloorRequest(
                    wing_id=self._resolve_nested_wing_id(
                        wing_client_key=floor_body.wing_client_key,
                        wing_key_map=wing_key_map,
                    ),
                    level_number=floor_body.level_number,
                    display_name=floor_body.display_name,
                    sort_order=floor_body.sort_order,
                    is_parking=floor_body.is_parking,
                ),
            )
            created_floors.append(floor)

        result["wings"] = created_wings
        result["gates"] = created_gates
        result["lifts"] = created_lifts
        result["floors"] = created_floors
        return result

    async def list_towers(self, *, project_id: str) -> list[dict[str, Any]]:
        """List towers for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.towers_repo.list_towers(
            organization_id=self._org_id, project_id=project_id
        )
        return [serialize_row(row) for row in rows]

    async def get_tower_detail(self, *, project_id: str, tower_id: str) -> dict[str, Any]:
        """Return a tower with nested wings, gates, lifts, and floors."""
        tower = await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        result = serialize_row(tower)
        result["wings"] = await self.list_wings(project_id=project_id, tower_id=tower_id)
        result["gates"] = await self.list_gates(project_id=project_id, tower_id=tower_id)
        result["lifts"] = await self.list_lifts(project_id=project_id, tower_id=tower_id)
        result["floors"] = await self.list_floors(project_id=project_id, tower_id=tower_id)
        return result

    async def update_tower(
        self, *, project_id: str, tower_id: str, body: UpdateTowerRequest
    ) -> dict[str, Any]:
        """Patch a tower."""
        await self._ensure_tower(project_id=project_id, tower_id=tower_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        current = await self.towers_repo.get_tower(
            organization_id=self._org_id, project_id=project_id, tower_id=tower_id
        )
        numbering_pattern = (
            body.numbering_pattern.value
            if body.numbering_pattern
            else str(current.get("numbering_pattern"))
        )
        custom_prefix = (
            body.custom_prefix if "custom_prefix" in patch else current.get("custom_prefix")
        )
        validate_tower_numbering(
            numbering_pattern=numbering_pattern,
            custom_prefix=custom_prefix,
        )
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
