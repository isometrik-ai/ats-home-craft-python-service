"""Unit tests for TowersService."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.enums import (
    GateStatus,
    GateType,
    LiftStatus,
    LiftType,
    ProjectSetupStep,
    TowerType,
    UnitNumberingPattern,
)
from apps.user_service.app.schemas.project_setup import (
    CreateFloorRequest,
    CreateTowerGateRequest,
    CreateTowerLiftRequest,
    CreateTowerRequest,
    CreateTowerWingRequest,
    UpdateTowerRequest,
)
from apps.user_service.app.services.towers_service import TowersService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
TOWER_ID = "770e8400-e29b-41d4-a716-446655440002"
WING_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ctx() -> UserContext:
    """Build user context for tower tests."""
    return UserContext(user_id="user-1", email="owner@example.com", organization_id=ORG_ID)


class _FakeTowersRepo:
    """Configurable fake TowersRepository."""

    def __init__(
        self,
        *,
        tower: dict[str, Any] | None = None,
        towers: list[dict[str, Any]] | None = None,
        wings: list[dict[str, Any]] | None = None,
        gates: list[dict[str, Any]] | None = None,
        lifts: list[dict[str, Any]] | None = None,
        floors: list[dict[str, Any]] | None = None,
        insert_error: Exception | None = None,
        wing_belongs: bool = True,
        delete_result: bool = True,
    ) -> None:
        self.tower = tower
        self.towers = towers or []
        self.wings = wings or []
        self.gates = gates or []
        self.lifts = lifts or []
        self.floors = floors or []
        self.insert_error = insert_error
        self.wing_belongs = wing_belongs
        self.delete_result = delete_result
        self.last_insert: dict[str, Any] | None = None
        self.last_update: dict[str, Any] | None = None

    async def get_tower(self, **kwargs):
        """Return configured tower row."""
        del kwargs
        return self.tower

    async def list_towers(self, **kwargs):
        """Return configured tower rows."""
        del kwargs
        return self.towers

    async def insert_tower(self, data):
        """Insert tower or raise configured error."""
        self.last_insert = data
        if self.insert_error:
            raise self.insert_error
        return {"id": TOWER_ID, **data}

    async def update_tower(self, **kwargs):
        """Update tower and return merged row."""
        self.last_update = kwargs
        return {**(self.tower or {}), **kwargs.get("update_data", {})}

    async def delete_tower(self, **kwargs):
        """Delete tower."""
        del kwargs

    async def insert_wing(self, data):
        """Insert wing row."""
        if self.insert_error:
            raise self.insert_error
        return {"id": WING_ID, **data}

    async def list_wings(self, **kwargs):
        """Return wing rows."""
        del kwargs
        return self.wings

    async def delete_wing(self, **kwargs):
        """Delete wing and return success flag."""
        del kwargs
        return self.delete_result

    async def wing_belongs_to_tower(self, **kwargs):
        """Return configured wing ownership result."""
        del kwargs
        return self.wing_belongs

    async def insert_gate(self, data):
        """Insert gate row."""
        return {"id": "gate-1", **data}

    async def list_gates(self, **kwargs):
        """Return gate rows."""
        del kwargs
        return self.gates

    async def delete_gate(self, **kwargs):
        """Delete gate and return success flag."""
        del kwargs
        return self.delete_result

    async def insert_lift(self, data):
        """Insert lift row."""
        return {"id": "lift-1", **data}

    async def list_lifts(self, **kwargs):
        """Return lift rows."""
        del kwargs
        return self.lifts

    async def delete_lift(self, **kwargs):
        """Delete lift and return success flag."""
        del kwargs
        return self.delete_result

    async def insert_floor(self, data):
        """Insert floor row."""
        if self.insert_error:
            raise self.insert_error
        return {"id": "floor-1", **data}

    async def list_floors(self, **kwargs):
        """Return floor rows."""
        del kwargs
        return self.floors

    async def delete_floor(self, **kwargs):
        """Delete floor and return success flag."""
        del kwargs
        return self.delete_result


def _service(repo: _FakeTowersRepo) -> TowersService:
    """Build TowersService with fake repositories."""
    service = TowersService(db_connection=MagicMock(), user_context=_ctx())
    service.towers_repo = repo
    service.setup_service = AsyncMock()
    service.setup_service.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    service.setup_service.complete_step = AsyncMock(
        return_value={"step_key": ProjectSetupStep.TOWER_BUILDER.value}
    )
    return service


@pytest.mark.asyncio
async def test_create_tower_success():
    """Create tower validates project and persists row."""
    repo = _FakeTowersRepo()
    service = _service(repo)
    body = CreateTowerRequest(
        name="Tower A",
        code="TA",
        tower_type=TowerType.RESIDENTIAL,
        numbering_pattern=UnitNumberingPattern.FLOOR_UNIT,
    )

    created = await service.create_tower(project_id=PROJECT_ID, body=body)

    assert created["id"] == TOWER_ID
    assert repo.last_insert["organization_id"] == ORG_ID
    service.setup_service.ensure_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_tower_duplicate_code():
    """Unique violation becomes ConflictException."""
    repo = _FakeTowersRepo(insert_error=UniqueViolationError("duplicate"))
    service = _service(repo)

    with pytest.raises(ConflictException):
        await service.create_tower(
            project_id=PROJECT_ID,
            body=CreateTowerRequest(
                name="Tower A",
                code="TA",
                tower_type=TowerType.RESIDENTIAL,
            ),
        )


@pytest.mark.asyncio
async def test_ensure_tower_not_found():
    """Missing tower raises NotFoundException."""
    repo = _FakeTowersRepo(tower=None)
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service._ensure_tower(project_id=PROJECT_ID, tower_id=TOWER_ID)


@pytest.mark.asyncio
async def test_list_towers():
    """List towers serializes repository rows."""
    repo = _FakeTowersRepo(
        towers=[{"id": TOWER_ID, "name": "Tower A", "code": "TA", "tower_type": "residential"}]
    )
    service = _service(repo)

    rows = await service.list_towers(project_id=PROJECT_ID)

    assert rows[0]["id"] == TOWER_ID


@pytest.mark.asyncio
async def test_update_tower_validates_numbering():
    """Custom numbering requires custom prefix."""
    repo = _FakeTowersRepo(
        tower={
            "id": TOWER_ID,
            "numbering_pattern": UnitNumberingPattern.FLOOR_UNIT.value,
            "custom_prefix": None,
        }
    )
    service = _service(repo)

    with pytest.raises(ValidationException):
        await service.update_tower(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            body=UpdateTowerRequest(numbering_pattern=UnitNumberingPattern.CUSTOM),
        )


@pytest.mark.asyncio
async def test_create_gate_rejects_unknown_wing():
    """Gate creation validates wing ownership."""
    repo = _FakeTowersRepo(
        tower={"id": TOWER_ID, "name": "Tower A"},
        wing_belongs=False,
    )
    service = _service(repo)

    with pytest.raises(ValidationException):
        await service.create_gate(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            body=CreateTowerGateRequest(name="Main Gate", wing_id=WING_ID),
        )


@pytest.mark.asyncio
async def test_create_wing_gate_lift_and_floor():
    """Nested tower entities are created through repository."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID, "name": "Tower A"})
    service = _service(repo)

    wing = await service.create_wing(
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        body=CreateTowerWingRequest(name="East Wing"),
    )
    gate = await service.create_gate(
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        body=CreateTowerGateRequest(
            name="Gate 1", gate_type=GateType.ENTRY, status=GateStatus.ACTIVE
        ),
    )
    lift = await service.create_lift(
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        body=CreateTowerLiftRequest(
            name="Lift 1", lift_type=LiftType.PASSENGER, status=LiftStatus.OPERATIONAL
        ),
    )
    floor = await service.create_floor(
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        body=CreateFloorRequest(level_number=1, display_name="Ground"),
    )

    assert wing["id"] == WING_ID
    assert gate["name"] == "Gate 1"
    assert lift["name"] == "Lift 1"
    assert floor["display_name"] == "Ground"


@pytest.mark.asyncio
async def test_delete_wing_not_found():
    """Deleting missing wing raises NotFoundException."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID}, delete_result=False)
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.delete_wing(project_id=PROJECT_ID, tower_id=TOWER_ID, wing_id=WING_ID)


@pytest.mark.asyncio
async def test_complete_tower_builder():
    """Complete step delegates to setup service."""
    repo = _FakeTowersRepo()
    service = _service(repo)

    result = await service.complete_tower_builder(project_id=PROJECT_ID)

    assert result["step_key"] == ProjectSetupStep.TOWER_BUILDER.value
    service.setup_service.complete_step.assert_awaited_once()
