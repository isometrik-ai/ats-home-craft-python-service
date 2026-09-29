"""Unit tests for FacilityStaffAssignmentsRepository."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.facility_staff_assignments_repository import (
    FacilityStaffAssignmentsRepository,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
MEMBER_ID = "33333333-3333-3333-3333-333333333333"
ASSIGNMENT_ID = "44444444-4444-4444-4444-444444444444"
USER_ID = "55555555-5555-5555-5555-555555555555"
FACILITY_A = "66666666-6666-6666-6666-666666666666"
FACILITY_B = "77777777-7777-7777-7777-777777777777"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        row: dict | None = None,
        execute_result: str = "DELETE 1",
        fetchval_result: str | list[str] | None = None,
    ) -> None:
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result
        self.fetchval_result = fetchval_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query: str, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_list_assignments() -> None:
    conn = _FakeConn(
        rows=[
            {
                "id": ASSIGNMENT_ID,
                "project_member_id": MEMBER_ID,
                "facility_ids": [FACILITY_A],
                "email": "staff@example.com",
            }
        ]
    )
    repo = FacilityStaffAssignmentsRepository(db_connection=conn)

    assignments = await repo.list_assignments(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert assignments[0]["email"] == "staff@example.com"
    query, args = conn.fetch_calls[0]
    assert "facility_staff_assignments" in query
    assert "project_members" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_get_member_found_and_missing() -> None:
    conn = _FakeConn(row={"id": MEMBER_ID, "user_id": USER_ID, "status": "active"})
    repo = FacilityStaffAssignmentsRepository(db_connection=conn)

    member = await repo.get_member(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_member_id=MEMBER_ID,
    )
    assert member is not None
    assert member["user_id"] == USER_ID
    query, args = conn.fetchrow_calls[0]
    assert "FROM project_members" in query
    assert args == (MEMBER_ID, ORG_ID, PROJECT_ID)

    conn.row = None
    missing = await repo.get_member(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_member_id=MEMBER_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_upsert_assignment_returns_id() -> None:
    conn = _FakeConn(fetchval_result=ASSIGNMENT_ID)
    repo = FacilityStaffAssignmentsRepository(db_connection=conn)

    assignment_id = await repo.upsert_assignment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_member_id=MEMBER_ID,
        facility_ids=[FACILITY_A, FACILITY_B],
        user_id=USER_ID,
    )

    assert assignment_id == ASSIGNMENT_ID
    query, args = conn.fetchval_calls[0]
    assert "INSERT INTO facility_staff_assignments" in query
    assert "ON CONFLICT (project_member_id) DO UPDATE" in query
    assert args == (ORG_ID, PROJECT_ID, MEMBER_ID, [FACILITY_A, FACILITY_B], USER_ID)


@pytest.mark.asyncio
async def test_delete_assignment_success_and_miss() -> None:
    conn = _FakeConn(execute_result="DELETE 1")
    repo = FacilityStaffAssignmentsRepository(db_connection=conn)

    deleted = await repo.delete_assignment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        assignment_id=ASSIGNMENT_ID,
    )
    assert deleted is True
    query, args = conn.execute_calls[0]
    assert "DELETE FROM facility_staff_assignments" in query
    assert args == (ASSIGNMENT_ID, ORG_ID, PROJECT_ID)

    conn.execute_result = "DELETE 0"
    not_deleted = await repo.delete_assignment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        assignment_id=ASSIGNMENT_ID,
    )
    assert not_deleted is False


@pytest.mark.asyncio
async def test_facility_ids_for_user_with_and_without_assignment() -> None:
    conn = _FakeConn(fetchval_result=[FACILITY_A, FACILITY_B])
    repo = FacilityStaffAssignmentsRepository(db_connection=conn)

    facility_ids = await repo.facility_ids_for_user(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        user_id=USER_ID,
    )
    assert facility_ids == [FACILITY_A, FACILITY_B]
    query, args = conn.fetchval_calls[0]
    assert "INNER JOIN project_members" in query
    assert args == (ORG_ID, PROJECT_ID, USER_ID)

    conn.fetchval_result = None
    none_ids = await repo.facility_ids_for_user(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        user_id=USER_ID,
    )
    assert none_ids is None


@pytest.mark.asyncio
async def test_remove_facility_from_assignments() -> None:
    conn = _FakeConn()
    repo = FacilityStaffAssignmentsRepository(db_connection=conn)

    await repo.remove_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_A,
    )

    query, args = conn.execute_calls[0]
    assert "array_remove(facility_ids" in query
    assert "= ANY(facility_ids)" in query
    assert args == (ORG_ID, PROJECT_ID, FACILITY_A)
