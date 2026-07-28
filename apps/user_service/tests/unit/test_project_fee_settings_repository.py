"""Unit tests for ProjectFeeSettingsRepository with fake connection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.project_fee_settings_repository import (
    ProjectFeeSettingsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
SETTINGS_ID = "770e8400-e29b-41d4-a716-446655440002"
USER_ID = "880e8400-e29b-41d4-a716-446655440003"
CONFIGURED_AT = datetime(2026, 1, 10, tzinfo=timezone.utc)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


def _settings_row(**overrides):
    base = {
        "id": SETTINGS_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "currency": "INR",
        "billing_cycle_type": "monthly",
        "retry_count": 3,
        "retry_interval_days": 7,
        "reminder_count": 2,
        "reminder_interval_days": 3,
        "exhausted_retry_action": "suspend",
        "is_configured": True,
        "configured_at": CONFIGURED_AT,
        "configured_by": USER_ID,
        "created_at": CONFIGURED_AT,
        "updated_at": CONFIGURED_AT,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_get_by_project_id():
    """Get settings scoped to org and project."""
    conn = _FakeConn(row=_settings_row())
    repo = ProjectFeeSettingsRepository(db_connection=conn)

    settings = await repo.get_by_project_id(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert settings["currency"] == "INR"
    query, args = conn.fetchrow_calls[0]
    assert "FROM project_fee_settings" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_get_by_project_id_not_found():
    """Missing settings returns None."""
    conn = _FakeConn(row=None)
    repo = ProjectFeeSettingsRepository(db_connection=conn)

    assert await repo.get_by_project_id(organization_id=ORG_ID, project_id=PROJECT_ID) is None


@pytest.mark.asyncio
async def test_upsert():
    """Upsert inserts or updates fee settings."""
    conn = _FakeConn(row=_settings_row(currency="USD"))
    repo = ProjectFeeSettingsRepository(db_connection=conn)

    settings = await repo.upsert(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        data={
            "currency": "USD",
            "billing_cycle_type": "quarterly",
            "retry_count": 2,
            "retry_interval_days": 5,
            "reminder_count": 1,
            "reminder_interval_days": 2,
            "exhausted_retry_action": "write_off",
            "is_configured": True,
            "configured_at": CONFIGURED_AT,
            "configured_by": USER_ID,
        },
    )

    assert settings["currency"] == "USD"
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO project_fee_settings" in query
    assert "ON CONFLICT (project_id) DO UPDATE" in query
    assert "::billing_cycle_type" in query
    assert "::exhausted_retry_action" in query


@pytest.mark.asyncio
async def test_upsert_optional_fields_none():
    """Upsert allows null configured_at and configured_by."""
    conn = _FakeConn(row=_settings_row(is_configured=False, configured_at=None, configured_by=None))
    repo = ProjectFeeSettingsRepository(db_connection=conn)

    await repo.upsert(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        data={
            "currency": "INR",
            "billing_cycle_type": "monthly",
            "retry_count": 1,
            "retry_interval_days": 1,
            "reminder_count": 0,
            "reminder_interval_days": 0,
            "exhausted_retry_action": "suspend",
            "is_configured": False,
        },
    )

    args = conn.fetchrow_calls[0][1]
    assert args[10] is None
    assert args[11] is None


@pytest.mark.asyncio
async def test_list_configured_projects():
    """List configured projects filters is_configured true."""
    conn = _FakeConn(
        rows=[
            {
                "project_id": PROJECT_ID,
                "currency": "INR",
                "billing_cycle_type": "monthly",
                "retry_count": 3,
                "retry_interval_days": 7,
                "reminder_count": 2,
                "reminder_interval_days": 3,
                "exhausted_retry_action": "suspend",
            }
        ]
    )
    repo = ProjectFeeSettingsRepository(db_connection=conn)

    projects = await repo.list_configured_projects(organization_id=ORG_ID)

    assert len(projects) == 1
    assert projects[0]["project_id"] == PROJECT_ID
    query, args = conn.fetch_calls[0]
    assert "is_configured = true" in query
    assert args == (ORG_ID,)


@pytest.mark.asyncio
async def test_list_configured_projects_empty():
    """No configured projects returns empty list."""
    conn = _FakeConn(rows=[])
    repo = ProjectFeeSettingsRepository(db_connection=conn)

    assert await repo.list_configured_projects(organization_id=ORG_ID) == []
