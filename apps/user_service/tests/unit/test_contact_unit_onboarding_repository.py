"""Unit tests for ContactUnitOnboardingRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.contact_unit_onboarding_repository import (
    UNIT_ONBOARDING_STEP_KEYS,
    ContactUnitOnboardingRepository,
)
from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
CONTACT_UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, fetchval_result=True, execute_result="INSERT 0 1"):
        self.rows = rows or []
        self.row = row
        self.fetchval_result = fetchval_result
        self.execute_result = execute_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_ensure_steps():
    """Ensure steps inserts vehicles and household wizard rows."""
    conn = _FakeConn()
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    await repo.ensure_steps(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    query, args = conn.execute_calls[0]
    assert "INSERT INTO contact_unit_onboarding_steps" in query
    assert args[3] == SetupStepStatus.NOT_STARTED.value
    assert args[4] == list(UNIT_ONBOARDING_STEP_KEYS)


@pytest.mark.asyncio
async def test_ensure_steps_for_units():
    """Ensure steps loops over each contact_unit id."""
    conn = _FakeConn()
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    await repo.ensure_steps_for_units(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_ids=[CONTACT_UNIT_ID, "cu-2"],
    )

    assert len(conn.execute_calls) == 2


@pytest.mark.asyncio
async def test_list_steps_for_contact():
    """List all unit-level steps for a contact."""
    conn = _FakeConn(
        rows=[
            {
                "contact_unit_id": CONTACT_UNIT_ID,
                "contact_id": CONTACT_ID,
                "unit_id": UNIT_ID,
                "unit_code": "A-101",
                "step_key": ContactOnboardingStep.VEHICLES.value,
                "status": "not_started",
            }
        ]
    )
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    steps = await repo.list_steps_for_contact(organization_id=ORG_ID, contact_id=CONTACT_ID)

    assert steps[0]["unit_code"] == "A-101"
    query, _ = conn.fetch_calls[0]
    assert "INNER JOIN contact_units cu" in query
    assert "cu.status = 'active'" in query


@pytest.mark.asyncio
async def test_list_steps_for_contact_empty():
    """No active units returns empty step list."""
    conn = _FakeConn(rows=[])
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    assert await repo.list_steps_for_contact(organization_id=ORG_ID, contact_id=CONTACT_ID) == []


@pytest.mark.asyncio
async def test_list_steps_for_unit():
    """List steps for one contact_unit."""
    conn = _FakeConn(
        rows=[
            {
                "contact_unit_id": CONTACT_UNIT_ID,
                "contact_id": CONTACT_ID,
                "step_key": ContactOnboardingStep.HOUSEHOLD.value,
                "status": "completed",
            }
        ]
    )
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    steps = await repo.list_steps_for_unit(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    assert steps[0]["step_key"] == ContactOnboardingStep.HOUSEHOLD.value
    assert conn.fetch_calls[0][1][2] == CONTACT_UNIT_ID


@pytest.mark.asyncio
async def test_complete_step():
    """Complete step marks status completed."""
    conn = _FakeConn(
        row={
            "step_key": ContactOnboardingStep.VEHICLES.value,
            "status": SetupStepStatus.COMPLETED.value,
        }
    )
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    row = await repo.complete_step(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        step_key=ContactOnboardingStep.VEHICLES.value,
    )

    assert row["status"] == SetupStepStatus.COMPLETED.value
    assert conn.fetchrow_calls[0][1][4] == SetupStepStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_complete_step_not_found():
    """Complete on missing step returns None."""
    conn = _FakeConn(row=None)
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    row = await repo.complete_step(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        step_key=ContactOnboardingStep.VEHICLES.value,
    )

    assert row is None


@pytest.mark.asyncio
async def test_skip_step():
    """Skip step marks status skipped."""
    conn = _FakeConn(
        row={
            "step_key": ContactOnboardingStep.HOUSEHOLD.value,
            "status": SetupStepStatus.SKIPPED.value,
        }
    )
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    row = await repo.skip_step(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        step_key=ContactOnboardingStep.HOUSEHOLD.value,
    )

    assert row["status"] == SetupStepStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_all_unit_steps_terminal_true():
    """All active unit steps terminal returns True."""
    conn = _FakeConn(fetchval_result=True)
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    assert await repo.all_unit_steps_terminal(organization_id=ORG_ID, contact_id=CONTACT_ID)
    assert "CROSS JOIN unnest" in conn.fetchval_calls[0][0]


@pytest.mark.asyncio
async def test_all_unit_steps_terminal_false():
    """Incomplete unit steps returns False."""
    conn = _FakeConn(fetchval_result=False)
    repo = ContactUnitOnboardingRepository(db_connection=conn)

    assert not await repo.all_unit_steps_terminal(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
    )
