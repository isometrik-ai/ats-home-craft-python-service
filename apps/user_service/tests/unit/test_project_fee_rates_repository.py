"""Unit tests for ProjectFeeRatesRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.project_fee_rates_repository import (
    ProjectFeeRatesRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
RATE_ID = "770e8400-e29b-41d4-a716-446655440002"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_list_by_project_id():
    conn = _FakeConn(
        rows=[
            {
                "id": RATE_ID,
                "unit_config_kind": "apartment",
                "rate_amount_minor_per_unit": 50000,
            }
        ]
    )
    repo = ProjectFeeRatesRepository(db_connection=conn)

    rates = await repo.list_by_project_id(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert len(rates) == 1
    assert rates[0]["unit_config_kind"] == "apartment"
    query, args = conn.fetch_calls[0]
    assert "FROM project_fee_rates" in query
    assert "ORDER BY unit_config_kind" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_upsert_batch_empty_returns_empty_list():
    conn = _FakeConn()
    repo = ProjectFeeRatesRepository(db_connection=conn)

    result = await repo.upsert_batch(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        rates=[],
    )

    assert result == []
    assert conn.fetchrow_calls == []


@pytest.mark.asyncio
async def test_upsert_batch_inserts_each_rate_with_casts():
    rate_row = {
        "id": RATE_ID,
        "unit_config_kind": "villa",
        "rate_amount_minor_per_unit": 120000,
        "measurement_unit": "sqft",
        "billing_frequency": "monthly",
        "fee_start_trigger": "possession",
        "start_offset_days": 30,
        "minimum_fee_minor": 10000,
    }
    conn = _FakeConn(row=rate_row)
    repo = ProjectFeeRatesRepository(db_connection=conn)

    results = await repo.upsert_batch(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        rates=[
            {
                "unit_config_kind": "villa",
                "rate_amount_minor_per_unit": 120000,
                "measurement_unit": "sqft",
                "billing_frequency": "monthly",
                "fee_start_trigger": "possession",
                "start_offset_days": 30,
                "minimum_fee_minor": 10000,
            },
            {
                "unit_config_kind": "apartment",
                "rate_amount_minor_per_unit": 80000,
                "measurement_unit": "sqft",
                "billing_frequency": "monthly",
                "fee_start_trigger": "possession",
                "minimum_fee_minor": 5000,
            },
        ],
    )

    assert len(results) == 2
    assert len(conn.fetchrow_calls) == 2
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO project_fee_rates" in query
    assert "ON CONFLICT (project_id, unit_config_kind) DO UPDATE" in query
    assert "::unit_config_kind" in query
    assert "::measurement_unit" in query
    assert "::billing_frequency" in query
    assert "::fee_start_trigger" in query
    assert args[0] == ORG_ID
    assert args[1] == PROJECT_ID
    assert args[2] == "villa"


@pytest.mark.asyncio
async def test_delete_kinds_not_in_with_kinds():
    conn = _FakeConn()
    repo = ProjectFeeRatesRepository(db_connection=conn)

    await repo.delete_kinds_not_in(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        kinds=["apartment", "villa"],
    )

    query, args = conn.execute_calls[0]
    assert "DELETE FROM project_fee_rates" in query
    assert "NOT (unit_config_kind = ANY" in query
    assert args == (ORG_ID, PROJECT_ID, ["apartment", "villa"])


@pytest.mark.asyncio
async def test_delete_kinds_not_in_empty_kinds_deletes_all_for_project():
    conn = _FakeConn()
    repo = ProjectFeeRatesRepository(db_connection=conn)

    await repo.delete_kinds_not_in(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        kinds=[],
    )

    query, args = conn.execute_calls[0]
    assert "DELETE FROM project_fee_rates" in query
    assert "ANY" not in query
    assert args == (ORG_ID, PROJECT_ID)
