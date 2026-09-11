"""Unit tests for ParkingAllotmentRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.parking_allotment_repository import (
    ParkingAllotmentRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, total: int = 0, rows=None):
        self.total = total
        self.rows = rows or []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.total

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows


@pytest.mark.asyncio
async def test_list_slots_slot_type_filter_uses_normalized_subtype_sql():
    """slot_type filter must match normalized facility_subtype (same as API response)."""
    conn = _FakeConn(total=2)
    repo = ParkingAllotmentRepository(db_connection=conn)

    rows, total = await repo.list_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_type="basement",
        page=1,
        page_size=50,
    )

    assert total == 2
    assert rows == []
    count_query, count_args = conn.fetchval_calls[0]
    list_query, list_args = conn.fetch_calls[0]
    assert "WHEN TRIM(COALESCE(f.facility_subtype, '')) = '' THEN 'open'" in count_query
    assert "basement" in count_args
    assert count_args[2] == "basement"
    assert list_args[2] == "basement"


@pytest.mark.asyncio
async def test_list_slots_search_uses_slot_code_label_sql():
    """Search must match the same slot_code_label shown in the by-slot UI."""
    conn = _FakeConn(total=1)
    repo = ParkingAllotmentRepository(db_connection=conn)

    await repo.list_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        search="LUX-A-B1-2",
        page=1,
        page_size=20,
    )

    count_query, count_args = conn.fetchval_calls[0]
    list_query, list_args = conn.fetch_calls[0]
    assert "CONCAT_WS(" in count_query
    assert "NULLIF(TRIM(COALESCE(t.code, '')), '')" in count_query
    assert "NULLIF(TRIM(COALESCE(fps.slot_code, '')), '')" in count_query
    assert "LPAD(fps.slot_number::text, 3, '0')" in count_query
    assert "CONCAT(" not in count_query
    assert count_args[2] == "%LUX-A-B1-2%"
    assert list_args[2] == "%LUX-A-B1-2%"
