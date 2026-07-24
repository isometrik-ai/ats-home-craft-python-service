"""Unit tests for VehiclesRepository with mocked asyncpg connection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
VEHICLE_ID = "990e8400-e29b-41d4-a716-446655440004"


def _mock_conn(*, row=None, rows=None, val=None):
    """Build asyncpg-like connection mock."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=val)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


def _vehicle_row(**overrides):
    """Minimal vehicle row dict."""
    row = {
        "id": VEHICLE_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "contact_id": CONTACT_ID,
        "unit_id": UNIT_ID,
        "vehicle_type": "car",
        "registration_number": "ABC-123",
        "make": "Toyota",
        "model": "Camry",
        "color": "Blue",
        "photo_paths": [],
        "fuel_type": "petrol",
        "status": "pending",
        "rejection_reason": None,
        "parking_slot_id": None,
        "status_updated_at": None,
        "sort_order": 0,
        "created_at": None,
        "updated_at": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_list_by_contact_with_unit_filter():
    """list_by_contact scopes by organization, contact, and optional unit."""
    conn = _mock_conn(rows=[_vehicle_row()])
    repo = VehiclesRepository(db_connection=conn)

    rows = await repo.list_by_contact(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
    )

    assert len(rows) == 1
    query, args = _sql_args(conn.fetch)
    assert "FROM vehicles v" in query
    assert "deleted_at IS NULL" in query
    assert args == (ORG_ID, CONTACT_ID, UNIT_ID)


@pytest.mark.asyncio
async def test_get_by_id_active_only():
    """get_by_id excludes soft-deleted vehicles by default."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    vehicle = await repo.get_by_id(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
    )

    assert vehicle["registration_number"] == "ABC-123"
    query, args = _sql_args(conn.fetchrow)
    assert "deleted_at IS NULL" in query
    assert args == (ORG_ID, CONTACT_ID, VEHICLE_ID)


@pytest.mark.asyncio
async def test_get_by_id_include_removed():
    """get_by_id can include removed vehicles when requested."""
    conn = _mock_conn(row=_vehicle_row(status="removed"))
    repo = VehiclesRepository(db_connection=conn)

    vehicle = await repo.get_by_id(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        include_removed=True,
    )

    assert vehicle is not None
    query, _ = _sql_args(conn.fetchrow)
    assert "deleted_at IS NULL" not in query


@pytest.mark.asyncio
async def test_get_by_id_missing():
    """get_by_id returns None when no row."""
    conn = _mock_conn(row=None)
    repo = VehiclesRepository(db_connection=conn)

    assert (
        await repo.get_by_id(
            organization_id=ORG_ID,
            contact_id=CONTACT_ID,
            vehicle_id=VEHICLE_ID,
        )
        is None
    )


@pytest.mark.asyncio
async def test_create_vehicle():
    """create inserts vehicle and returns row."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    created = await repo.create(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        vehicle_type="car",
        registration_number="ABC-123",
        make="Toyota",
        model="Camry",
        color="Blue",
        photo_paths=["/photos/1.jpg"],
        fuel_type="petrol",
    )

    assert created["id"] == VEHICLE_ID
    query, args = _sql_args(conn.fetchrow)
    assert "INSERT INTO vehicles" in query
    assert args[4] == "car"


@pytest.mark.asyncio
async def test_update_vehicle_builds_dynamic_set():
    """update patches allowed columns and sets status_updated_at when status changes."""
    conn = _mock_conn(row=_vehicle_row(status="approved"))
    repo = VehiclesRepository(db_connection=conn)

    updated = await repo.update(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={"status": "approved", "color": "Red", "photo_paths": ["/p.jpg"]},
    )

    assert updated["status"] == "approved"
    query, _ = _sql_args(conn.fetchrow)
    assert "status = $1::vehicle_status" in query
    assert "photo_paths = $3::text[]" in query
    assert "status_updated_at = now()" in query


@pytest.mark.asyncio
async def test_update_empty_data_delegates_to_get_by_id():
    """update with empty payload returns current row via get_by_id."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    result = await repo.update(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={},
    )

    assert result["id"] == VEHICLE_ID
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_update_by_project_with_parking_slot():
    """update_by_project casts parking_slot_id to uuid."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    await repo.update_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={"parking_slot_id": "aa0e8400-e29b-41d4-a716-446655440005"},
    )

    query, _ = _sql_args(conn.fetchrow)
    assert "parking_slot_id = $1::uuid" in query
    assert "project_id = $" in query


@pytest.mark.asyncio
async def test_update_by_project_enum_and_photo_casts():
    """update_by_project applies enum casts and text[] for photo_paths."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    await repo.update_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={
            "status": "active",
            "vehicle_type": "car",
            "fuel_type": "electric",
            "photo_paths": ["a.jpg", "b.jpg"],
        },
    )

    query, args = _sql_args(conn.fetchrow)
    assert "status = $1::vehicle_status" in query
    assert "vehicle_type = $2::vehicle_type" in query
    assert "fuel_type = $3::vehicle_fuel_type" in query
    assert "photo_paths = $4::text[]" in query
    assert "status_updated_at = now()" in query
    assert args[:4] == ("active", "car", "electric", ["a.jpg", "b.jpg"])


@pytest.mark.asyncio
async def test_update_by_project_skips_protected_columns():
    """update_by_project ignores immutable id/org/contact/created_at keys."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    await repo.update_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={
            "id": "ignored",
            "organization_id": ORG_ID,
            "contact_id": CONTACT_ID,
            "created_at": "2020-01-01",
            "make": "Toyota",
        },
    )

    query, args = _sql_args(conn.fetchrow)
    assert "make = $1" in query
    assert "id =" not in query.split("SET")[1].split("WHERE")[0]
    assert args[0] == "Toyota"


@pytest.mark.asyncio
async def test_update_by_project_empty_data():
    """update_by_project with empty payload delegates to get_by_project."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    result = await repo.update_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={},
    )

    assert result["id"] == VEHICLE_ID


@pytest.mark.asyncio
async def test_delete_vehicle():
    """delete hard-deletes vehicle row."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    deleted = await repo.delete(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
    )

    assert deleted["id"] == VEHICLE_ID
    query, _ = _sql_args(conn.fetchrow)
    assert "DELETE FROM vehicles" in query


@pytest.mark.asyncio
async def test_soft_remove_vehicle():
    """soft_remove marks vehicle removed and clears parking slot."""
    conn = _mock_conn(row=_vehicle_row(status="removed"))
    repo = VehiclesRepository(db_connection=conn)

    removed = await repo.soft_remove(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
    )

    assert removed["status"] == "removed"
    query, _ = _sql_args(conn.fetchrow)
    assert "status = 'removed'::vehicle_status" in query
    assert "deleted_at = now()" in query


@pytest.mark.asyncio
async def test_list_by_project_with_status_filter():
    """list_by_project filters by status and can exclude removed."""
    conn = _mock_conn(rows=[_vehicle_row()])
    repo = VehiclesRepository(db_connection=conn)

    rows = await repo.list_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        status="pending",
        include_removed=False,
    )

    assert len(rows) == 1
    query, args = _sql_args(conn.fetch)
    assert "v.status = $3::vehicle_status" in query
    assert "deleted_at IS NULL" in query
    assert args == (ORG_ID, PROJECT_ID, "pending")


@pytest.mark.asyncio
async def test_get_by_project():
    """get_by_project fetches active vehicle in project scope."""
    conn = _mock_conn(row=_vehicle_row())
    repo = VehiclesRepository(db_connection=conn)

    vehicle = await repo.get_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        vehicle_id=VEHICLE_ID,
    )

    assert vehicle is not None
    query, args = _sql_args(conn.fetchrow)
    assert "v.project_id = $2::uuid" in query
    assert args == (ORG_ID, PROJECT_ID, VEHICLE_ID)


@pytest.mark.asyncio
async def test_count_active():
    """count_active returns integer count of non-removed vehicles."""
    conn = _mock_conn(val=3)
    repo = VehiclesRepository(db_connection=conn)

    count = await repo.count_active(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
    )

    assert count == 3
    query, args = _sql_args(conn.fetchval)
    assert "COUNT(*)" in query
    assert args == (ORG_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_update_skips_protected_columns():
    """update ignores immutable id/organization_id/contact_id/created_at keys."""
    conn = _mock_conn(row=_vehicle_row(color="Green"))
    repo = VehiclesRepository(db_connection=conn)

    await repo.update(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        update_data={
            "id": "other",
            "organization_id": "other-org",
            "contact_id": "other-contact",
            "created_at": "2020-01-01",
            "color": "Green",
        },
    )

    query, _ = _sql_args(conn.fetchrow)
    set_clause = query.split("WHERE", 1)[0]
    assert "id = $" not in set_clause
    assert "organization_id = $" not in set_clause
    assert "contact_id = $" not in set_clause
    assert "created_at = $" not in set_clause
    assert "color = $1" in set_clause
