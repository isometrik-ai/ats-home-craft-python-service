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
    CreateFloorBulkItem,
    CreateFloorRequest,
    CreateTowerGateBulkItem,
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
        existing_codes: set[str] | None = None,
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
        self.existing_codes = existing_codes or set()

    async def get_tower(self, **kwargs):
        """Return configured tower row."""
        del kwargs
        return self.tower

    async def list_towers(self, **kwargs):
        """Return configured tower rows."""
        del kwargs
        return self.towers

    async def tower_code_exists(self, *, project_id: str, code: str) -> bool:
        """Return whether the code is already used in the project."""
        del project_id
        return code in self.existing_codes

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
async def test_create_tower_with_nested_entities():
    """Create tower can optionally create wings, gates, lifts, and floors."""
    wing_counter = {"n": 0}

    async def _insert_wing(data):
        wing_counter["n"] += 1
        return {"id": f"wing-{wing_counter['n']}", **data}

    repo = _FakeTowersRepo(tower={"id": TOWER_ID, "project_id": PROJECT_ID})
    repo.insert_wing = _insert_wing
    service = _service(repo)
    body = CreateTowerRequest(
        name="Tower A",
        code="TA",
        tower_type=TowerType.RESIDENTIAL,
        numbering_pattern=UnitNumberingPattern.FLOOR_UNIT,
        has_wings=True,
        wings=[
            CreateTowerWingRequest(
                name="East Wing",
                code="EAST",
            )
        ],
        gates=[
            CreateTowerGateBulkItem(
                name="Main Gate",
                gate_type=GateType.ENTRY,
                wing_client_key="EAST",
            )
        ],
        lifts=[CreateTowerLiftRequest(name="Lift 1")],
        floors=[
            CreateFloorBulkItem(level_number=0, display_name="Ground", wing_client_key="EAST"),
        ],
    )

    created = await service.create_tower(project_id=PROJECT_ID, body=body)

    assert created["id"] == TOWER_ID
    assert len(created["wings"]) == 1
    assert created["wings"][0]["name"] == "East Wing"
    assert len(created["gates"]) == 1
    assert created["gates"][0]["name"] == "Main Gate"
    assert len(created["lifts"]) == 1
    assert len(created["floors"]) == 1
    assert created["floors"][0]["display_name"] == "Ground"


@pytest.mark.asyncio
async def test_create_tower_without_nested_omits_child_arrays():
    """Tower-only create keeps the legacy flat response shape."""
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
    assert "wings" not in created
    assert "gates" not in created


@pytest.mark.asyncio
async def test_create_tower_rejects_unknown_wing_client_key():
    """Nested gate wing_client_key must match a wing in the same request."""
    from apps.user_service.app.schemas.project_setup import CreateTowerGateBulkItem

    repo = _FakeTowersRepo()
    service = _service(repo)
    body = CreateTowerRequest(
        name="Tower A",
        code="TA",
        tower_type=TowerType.RESIDENTIAL,
        gates=[CreateTowerGateBulkItem(name="Gate 1", wing_client_key="missing")],
    )

    with pytest.raises(ValidationException):
        await service.create_tower(project_id=PROJECT_ID, body=body)


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
async def test_create_tower_generates_code_from_name():
    """Missing code is generated from the tower name."""
    repo = _FakeTowersRepo()
    service = _service(repo)
    body = CreateTowerRequest(
        name="Tower D",
        tower_type=TowerType.RESIDENTIAL,
        numbering_pattern=UnitNumberingPattern.FLOOR_UNIT,
    )

    created = await service.create_tower(project_id=PROJECT_ID, body=body)

    assert created["code"] == "tower-d"
    assert repo.last_insert is not None
    assert repo.last_insert["code"] == "tower-d"


@pytest.mark.asyncio
async def test_create_tower_generated_code_suffix_on_conflict():
    """Generated tower code gets a numeric suffix when the base slug exists."""
    repo = _FakeTowersRepo(existing_codes={"tower-d"})
    service = _service(repo)
    body = CreateTowerRequest(
        name="Tower D",
        tower_type=TowerType.RESIDENTIAL,
        numbering_pattern=UnitNumberingPattern.FLOOR_UNIT,
    )

    created = await service.create_tower(project_id=PROJECT_ID, body=body)

    assert created["code"] == "tower-d-2"


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
async def test_get_tower_detail():
    """Get tower detail returns nested wings, gates, lifts, and floors."""
    repo = _FakeTowersRepo(
        tower={
            "id": TOWER_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "name": "Tower A",
            "code": "TA",
            "tower_type": "residential",
            "basement_count": 0,
            "upper_floor_count": 10,
            "numbering_pattern": "floor_unit",
            "starting_unit_number": 1,
            "has_wings": True,
            "sort_order": 0,
            "active": True,
        },
        wings=[{"id": WING_ID, "name": "East Wing", "tower_id": TOWER_ID}],
        gates=[{"id": "gate-1", "name": "Main Gate", "tower_id": TOWER_ID}],
        lifts=[{"id": "lift-1", "name": "Lift 1", "tower_id": TOWER_ID}],
        floors=[{"id": "floor-1", "display_name": "Ground", "tower_id": TOWER_ID}],
    )
    service = _service(repo)

    detail = await service.get_tower_detail(project_id=PROJECT_ID, tower_id=TOWER_ID)

    assert detail["id"] == TOWER_ID
    assert detail["name"] == "Tower A"
    assert len(detail["wings"]) == 1
    assert len(detail["gates"]) == 1
    assert len(detail["lifts"]) == 1
    assert len(detail["floors"]) == 1


@pytest.mark.asyncio
async def test_get_tower_detail_not_found():
    """Missing tower raises NotFoundException."""
    repo = _FakeTowersRepo(tower=None)
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.get_tower_detail(project_id=PROJECT_ID, tower_id=TOWER_ID)


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


@pytest.mark.asyncio
async def test_update_tower_success():
    """Update tower merges patch and returns serialized row."""
    repo = _FakeTowersRepo(
        tower={
            "id": TOWER_ID,
            "name": "Tower A",
            "numbering_pattern": UnitNumberingPattern.FLOOR_UNIT.value,
            "custom_prefix": None,
        }
    )
    service = _service(repo)

    updated = await service.update_tower(
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        body=UpdateTowerRequest(name="Tower Alpha"),
    )

    assert updated["name"] == "Tower Alpha"
    assert repo.last_update is not None


@pytest.mark.asyncio
async def test_update_tower_duplicate_code():
    """Update tower maps unique violation to ConflictException."""
    repo = _FakeTowersRepo(
        tower={
            "id": TOWER_ID,
            "numbering_pattern": UnitNumberingPattern.FLOOR_UNIT.value,
            "custom_prefix": None,
        }
    )

    async def _raise_unique(**kwargs):
        del kwargs
        raise UniqueViolationError("duplicate")

    repo.update_tower = _raise_unique
    service = _service(repo)

    with pytest.raises(ConflictException):
        await service.update_tower(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            body=UpdateTowerRequest(code="TB"),
        )


@pytest.mark.asyncio
async def test_delete_tower():
    """Delete tower returns old_data snapshot."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID, "name": "Tower A", "code": "TA"})
    service = _service(repo)

    result = await service.delete_tower(project_id=PROJECT_ID, tower_id=TOWER_ID)

    assert result["old_data"]["id"] == TOWER_ID
    assert result["new_data"] is None


@pytest.mark.asyncio
async def test_list_wings_gates_lifts_floors():
    """List nested tower entities serializes repository rows."""
    repo = _FakeTowersRepo(
        tower={"id": TOWER_ID, "name": "Tower A"},
        wings=[{"id": WING_ID, "name": "East Wing"}],
        gates=[{"id": "gate-1", "name": "Gate 1"}],
        lifts=[{"id": "lift-1", "name": "Lift 1"}],
        floors=[{"id": "floor-1", "display_name": "Ground"}],
    )
    service = _service(repo)

    wings = await service.list_wings(project_id=PROJECT_ID, tower_id=TOWER_ID)
    gates = await service.list_gates(project_id=PROJECT_ID, tower_id=TOWER_ID)
    lifts = await service.list_lifts(project_id=PROJECT_ID, tower_id=TOWER_ID)
    floors = await service.list_floors(project_id=PROJECT_ID, tower_id=TOWER_ID)

    assert wings[0]["name"] == "East Wing"
    assert gates[0]["name"] == "Gate 1"
    assert lifts[0]["name"] == "Lift 1"
    assert floors[0]["display_name"] == "Ground"


@pytest.mark.asyncio
async def test_create_wing_duplicate_code():
    """Wing insert unique violation becomes ConflictException."""
    repo = _FakeTowersRepo(
        tower={"id": TOWER_ID},
        insert_error=UniqueViolationError("duplicate"),
    )
    service = _service(repo)

    with pytest.raises(ConflictException):
        await service.create_wing(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            body=CreateTowerWingRequest(name="East Wing"),
        )


@pytest.mark.asyncio
async def test_create_floor_rejects_unknown_wing():
    """Floor creation validates wing ownership."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID}, wing_belongs=False)
    service = _service(repo)

    with pytest.raises(ValidationException):
        await service.create_floor(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            body=CreateFloorRequest(level_number=1, display_name="Ground", wing_id=WING_ID),
        )


@pytest.mark.asyncio
async def test_create_floor_duplicate_code():
    """Floor insert unique violation becomes ConflictException."""
    repo = _FakeTowersRepo(
        tower={"id": TOWER_ID},
        insert_error=UniqueViolationError("duplicate"),
    )
    service = _service(repo)

    with pytest.raises(ConflictException):
        await service.create_floor(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            body=CreateFloorRequest(level_number=1, display_name="Ground"),
        )


@pytest.mark.asyncio
async def test_delete_gate_not_found():
    """Deleting missing gate raises NotFoundException."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID}, delete_result=False)
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.delete_gate(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            gate_id="missing-gate",
        )


@pytest.mark.asyncio
async def test_delete_lift_not_found():
    """Deleting missing lift raises NotFoundException."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID}, delete_result=False)
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.delete_lift(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            lift_id="missing-lift",
        )


@pytest.mark.asyncio
async def test_delete_floor_not_found():
    """Deleting missing floor raises NotFoundException."""
    repo = _FakeTowersRepo(tower={"id": TOWER_ID}, delete_result=False)
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.delete_floor(
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            floor_id="missing-floor",
        )
