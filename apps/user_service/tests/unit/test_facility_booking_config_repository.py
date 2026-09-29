"""Unit tests for FacilityBookingConfigRepository."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from apps.user_service.app.db.repositories.facility_booking_config_repository import (
    CONFIG_JSONB_COLUMNS,
    FacilityBookingConfigRepository,
    decode_jsonb,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
USER_ID = "44444444-4444-4444-4444-444444444444"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        row: dict | None = None,
        fetchval_result: str | None = None,
    ) -> None:
        self.rows = rows or []
        self.row = row
        self.fetchval_result = fetchval_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result


def test_decode_jsonb_parses_config_columns() -> None:
    pricing = {"hourly_cents": 1500}
    row = {"pricing": json.dumps(pricing), "slot_minutes": 60}
    decoded = decode_jsonb(row, CONFIG_JSONB_COLUMNS)
    assert decoded["pricing"] == pricing
    assert decoded["slot_minutes"] == 60


@pytest.mark.asyncio
async def test_insert_config_via_bulk_insert() -> None:
    conn = _FakeConn()
    repo = FacilityBookingConfigRepository(db_connection=conn)
    config_row = {
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "facility_id": FACILITY_ID,
        "default_hours": [{"open": 360, "close": 1320}] * 7,
        "pricing": {"hourly_cents": 1000},
        "policies": {"max_duration_min": 120},
        "setup": {"slot_minutes": 30},
        "created_by_user_id": USER_ID,
    }

    with patch.object(
        FacilityBookingConfigRepository,
        "bulk_insert_returning",
        new=AsyncMock(
            return_value=[
                {
                    **config_row,
                    "default_hours": json.dumps(config_row["default_hours"]),
                    "pricing": json.dumps(config_row["pricing"]),
                    "policies": json.dumps(config_row["policies"]),
                    "setup": json.dumps(config_row["setup"]),
                }
            ]
        ),
    ) as bulk:
        inserted = await repo.insert_config(config_row)

    bulk.assert_awaited_once()
    assert inserted["pricing"]["hourly_cents"] == 1000
    kwargs = bulk.call_args.kwargs
    assert kwargs["table"] == "facility_booking_configs"
    assert kwargs["jsonb_columns"] == CONFIG_JSONB_COLUMNS


@pytest.mark.asyncio
async def test_get_config_decodes_and_missing() -> None:
    conn = _FakeConn(
        row={
            "facility_id": FACILITY_ID,
            "facility_name": "Pool",
            "policies": json.dumps({"cancellation_hours": 24}),
        }
    )
    repo = FacilityBookingConfigRepository(db_connection=conn)

    found = await repo.get_config(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )
    assert found is not None
    assert found["policies"]["cancellation_hours"] == 24
    assert "INNER JOIN facilities" in conn.fetchrow_calls[0][0]

    conn.row = None
    missing = await repo.get_config(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_list_configs_filter_flags() -> None:
    conn = _FakeConn(rows=[{"facility_name": "Gym", "pricing": "{}"}])
    repo = FacilityBookingConfigRepository(db_connection=conn)

    all_configs = await repo.list_configs(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        bookable_only=False,
        resident_visible_only=False,
    )
    assert len(all_configs) == 1
    query_all = conn.fetch_calls[0][0]
    where_all = query_all.split("WHERE", 1)[1]
    assert "f.is_bookable" not in where_all

    await repo.list_configs(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        bookable_only=True,
        resident_visible_only=True,
    )
    query_filtered = conn.fetch_calls[1][0]
    where_filtered = query_filtered.split("WHERE", 1)[1]
    assert "f.is_bookable" in where_filtered
    assert "f.active AND f.status = 'active'" in where_filtered


@pytest.mark.asyncio
async def test_update_config_success_and_stale() -> None:
    conn = _FakeConn(row={"id": "cfg-1"})
    repo = FacilityBookingConfigRepository(db_connection=conn)

    updated = await repo.update_config(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        expected_version=2,
        update_data={"pricing": {"hourly_cents": 2000}, "slot_minutes": 45},
    )
    assert updated == {"id": "cfg-1"}
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE facility_booking_configs" in query
    assert "version = version + 1" in query
    assert "::jsonb" in query
    assert json.dumps({"hourly_cents": 2000}) in args
    assert args[-3:] == (FACILITY_ID, ORG_ID, 2)

    conn.row = None
    stale = await repo.update_config(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        expected_version=99,
        update_data={"accepting_bookings": False},
    )
    assert stale is None


@pytest.mark.asyncio
async def test_get_settings_and_upsert_settings() -> None:
    conn = _FakeConn(row={"timezone": "America/New_York", "currency_code": "USD"})
    repo = FacilityBookingConfigRepository(db_connection=conn)

    settings = await repo.get_settings(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert settings["timezone"] == "America/New_York"
    assert "project_booking_settings" in conn.fetchrow_calls[0][0]

    conn.row = {
        "timezone": "Asia/Kolkata",
        "currency_code": "INR",
        "wallet_enabled": True,
    }
    upserted = await repo.upsert_settings(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        timezone="Asia/Kolkata",
        currency_code="INR",
        user_id=USER_ID,
        invoice_frequency="monthly",
        wallet_enabled=False,
        online_enabled=True,
        cash_enabled=True,
        wallet_credit_limit=5000,
    )
    assert upserted["currency_code"] == "INR"
    query, args = conn.fetchrow_calls[1]
    assert "ON CONFLICT (project_id) DO UPDATE" in query
    assert args[0:5] == (ORG_ID, PROJECT_ID, "Asia/Kolkata", "INR", USER_ID)


@pytest.mark.asyncio
async def test_get_organization_timezone() -> None:
    conn = _FakeConn(fetchval_result="Europe/London")
    repo = FacilityBookingConfigRepository(db_connection=conn)

    tz = await repo.get_organization_timezone(ORG_ID)
    assert tz == "Europe/London"
    query, args = conn.fetchval_calls[0]
    assert "organizations" in query
    assert args == (ORG_ID,)

    conn.fetchval_result = None
    missing = await repo.get_organization_timezone(ORG_ID)
    assert missing is None
