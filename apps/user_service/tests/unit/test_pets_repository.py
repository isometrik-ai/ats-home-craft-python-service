"""Unit tests for PetsRepository with fake asyncpg connection."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.db.repositories.pets_repository import PetsRepository
from apps.user_service.app.schemas.enums.pets import PetStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
CONTACT_ID = "880e8400-e29b-41d4-a716-446655440003"
PET_ID = "990e8400-e29b-41d4-a716-446655440004"
TOWER_ID = "aa0e8400-e29b-41d4-a716-446655440005"


def _pet_row(**overrides) -> dict:
    base = {
        "id": PET_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": UNIT_ID,
        "created_by_contact_id": CONTACT_ID,
        "name": "Buddy",
        "pet_type": "Dog",
        "breed": "Labrador",
        "gender": "male",
        "date_of_birth": date(2020, 1, 15),
        "vaccination_status": "complete",
        "photo_paths": ["photos/1.jpg"],
        "status": PetStatus.ACTIVE.value,
        "removal_reason": None,
        "removed_by_contact_id": None,
        "deleted_at": None,
        "sort_order": 0,
        "created_at": None,
        "updated_at": None,
        "creator_prefix": None,
        "creator_first_name": "Jane",
        "creator_last_name": "Doe",
        "creator_profile_photo_url": None,
        "creator_relationship": "owner",
        "unit_code": "A-101",
        "unit_label": "101",
        "tower_id": TOWER_ID,
        "tower_name": "Tower A",
    }
    base.update(overrides)
    return base


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val


@pytest.mark.asyncio
async def test_create():
    conn = _FakeConn(row=_pet_row())
    repo = PetsRepository(db_connection=conn)

    pet = await repo.create(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        created_by_contact_id=CONTACT_ID,
        name="Buddy",
        pet_type="Dog",
        breed="Labrador",
        gender="male",
        date_of_birth=date(2020, 1, 15),
        vaccination_status="complete",
        photo_paths=["photos/1.jpg"],
    )

    assert pet["name"] == "Buddy"
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO pets" in query
    assert PetStatus.ACTIVE.value in query
    assert args[0:4] == (ORG_ID, PROJECT_ID, UNIT_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_update_empty_update_data_loads_existing():
    conn = _FakeConn(row=_pet_row(name="Buddy"))
    repo = PetsRepository(db_connection=conn)

    pet = await repo.update(
        organization_id=ORG_ID,
        pet_id=PET_ID,
        update_data={},
    )

    assert pet["name"] == "Buddy"
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "FROM pets p" in query
    assert "UPDATE pets" not in query
    assert args[0:2] == (ORG_ID, PET_ID)


@pytest.mark.asyncio
async def test_update_with_fields():
    conn = _FakeConn(row=_pet_row(name="Max"))
    repo = PetsRepository(db_connection=conn)

    pet = await repo.update(
        organization_id=ORG_ID,
        pet_id=PET_ID,
        update_data={"name": "Max", "gender": "female"},
    )

    assert pet["name"] == "Max"
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE pets p" in query
    assert "name = $3" in query
    assert "gender = $4::pet_gender" in query
    assert "updated_at = now()" in query
    assert args == (ORG_ID, PET_ID, "Max", "female")


@pytest.mark.asyncio
async def test_update_returns_none_when_missing():
    conn = _FakeConn(row=None)
    repo = PetsRepository(db_connection=conn)

    assert (
        await repo.update(
            organization_id=ORG_ID,
            pet_id=PET_ID,
            update_data={"name": "Ghost"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_soft_remove():
    conn = _FakeConn(row=_pet_row(status=PetStatus.REMOVED.value))
    repo = PetsRepository(db_connection=conn)

    pet = await repo.soft_remove(
        organization_id=ORG_ID,
        pet_id=PET_ID,
        reason="Moved out",
        removed_by_contact_id=CONTACT_ID,
    )

    assert pet is not None
    query, args = conn.fetchrow_calls[0]
    assert PetStatus.REMOVED.value in query
    assert "removal_reason = $3" in query
    assert args == (ORG_ID, PET_ID, "Moved out", CONTACT_ID)


@pytest.mark.asyncio
async def test_soft_remove_all_active_for_unit():
    conn = _FakeConn(rows=[_pet_row(), _pet_row(id="pet-2")])
    repo = PetsRepository(db_connection=conn)

    pets = await repo.soft_remove_all_active_for_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
        reason="Unit vacated",
        removed_by_contact_id=None,
    )

    assert len(pets) == 2
    query, args = conn.fetch_calls[0]
    assert "UPDATE pets p" in query
    assert "p.unit_id = $2::uuid" in query
    assert args == (ORG_ID, UNIT_ID, "Unit vacated", None)


@pytest.mark.asyncio
async def test_get_by_id_active_only_default():
    conn = _FakeConn(row=_pet_row())
    repo = PetsRepository(db_connection=conn)

    pet = await repo.get_by_id(organization_id=ORG_ID, pet_id=PET_ID)

    assert pet["name"] == "Buddy"
    query, args = conn.fetchrow_calls[0]
    assert PetStatus.ACTIVE.value in query
    assert "deleted_at IS NULL" in query
    assert args == (ORG_ID, PET_ID)


@pytest.mark.asyncio
async def test_get_by_id_with_project_and_include_removed():
    conn = _FakeConn(row=_pet_row(status=PetStatus.REMOVED.value))
    repo = PetsRepository(db_connection=conn)

    pet = await repo.get_by_id(
        organization_id=ORG_ID,
        pet_id=PET_ID,
        project_id=PROJECT_ID,
        active_only=False,
    )

    assert pet is not None
    query, args = conn.fetchrow_calls[0]
    assert "p.project_id = $3::uuid" in query
    assert PetStatus.ACTIVE.value not in query.split("WHERE")[1]
    assert args == (ORG_ID, PET_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_get_by_id_missing():
    conn = _FakeConn(row=None)
    repo = PetsRepository(db_connection=conn)

    assert await repo.get_by_id(organization_id=ORG_ID, pet_id=PET_ID) is None


@pytest.mark.asyncio
async def test_list_for_project_with_all_filters():
    conn = _FakeConn(rows=[_pet_row()], val=1)
    repo = PetsRepository(db_connection=conn)

    rows, total = await repo.list_for_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        search="buddy",
        unit_id=UNIT_ID,
        tower_id=TOWER_ID,
        pet_type="Dog",
        breed="Labrador",
        status=PetStatus.REMOVED.value,
        page=2,
        page_size=10,
    )

    assert len(rows) == 1
    assert total == 1
    count_query, count_args = conn.fetchval_calls[0]
    assert PetStatus.REMOVED.value in count_query
    assert "p.unit_id = $3::uuid" in count_query
    assert "u.tower_id = $4::uuid" in count_query
    assert "lower(p.pet_type) = lower($5)" in count_query
    assert "lower(p.breed) = lower($6)" in count_query
    assert "ILIKE $7" in count_query
    assert count_args == (
        ORG_ID,
        PROJECT_ID,
        UNIT_ID,
        TOWER_ID,
        "Dog",
        "Labrador",
        "%buddy%",
    )

    list_query, list_args = conn.fetch_calls[0]
    assert "ORDER BY p.status, p.sort_order, p.created_at DESC" in list_query
    assert "LIMIT $8::int OFFSET $9::int" in list_query
    assert list_args[-2:] == (10, 10)


@pytest.mark.asyncio
async def test_list_for_project_no_extra_filters():
    conn = _FakeConn(rows=[], val=0)
    repo = PetsRepository(db_connection=conn)

    _, total = await repo.list_for_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert total == 0
    count_query, count_args = conn.fetchval_calls[0]
    assert PetStatus.ACTIVE.value in count_query
    assert count_args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_get_project_summary():
    conn = _FakeConn(row={"active_count": 3, "total_count": 5})
    repo = PetsRepository(db_connection=conn)

    summary = await repo.get_project_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert summary == {"active_count": 3, "total_count": 5}
    query, args = conn.fetchrow_calls[0]
    assert "COUNT(*) FILTER" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_get_project_summary_empty_row():
    conn = _FakeConn(row=None)
    repo = PetsRepository(db_connection=conn)

    summary = await repo.get_project_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )
    assert summary == {"active_count": 0, "total_count": 0}


@pytest.mark.asyncio
async def test_count_active_for_unit():
    conn = _FakeConn(val=4)
    repo = PetsRepository(db_connection=conn)

    count = await repo.count_active_for_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
    )

    assert count == 4
    query, args = conn.fetchval_calls[0]
    assert PetStatus.ACTIVE.value in query
    assert args == (ORG_ID, UNIT_ID)


@pytest.mark.asyncio
async def test_list_for_unit():
    conn = _FakeConn(rows=[_pet_row()], val=2)
    repo = PetsRepository(db_connection=conn)

    rows, total = await repo.list_for_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
        page=2,
        page_size=5,
    )

    assert len(rows) == 1
    assert total == 2
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.unit_id = $2::uuid" in count_query
    assert count_args == (ORG_ID, UNIT_ID)

    list_query, list_args = conn.fetch_calls[0]
    assert "LIMIT $3::int OFFSET $4::int" in list_query
    assert list_args == (ORG_ID, UNIT_ID, 5, 5)
