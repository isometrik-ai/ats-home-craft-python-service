"""Unit tests for ContactOnboardingRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    CONTACT_LEVEL_STEP_KEYS,
    ContactOnboardingRepository,
)
from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"


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
    """Ensure steps inserts contact-level wizard rows."""
    conn = _FakeConn()
    repo = ContactOnboardingRepository(db_connection=conn)

    await repo.ensure_steps(organization_id=ORG_ID, contact_id=CONTACT_ID)

    query, args = conn.execute_calls[0]
    assert "INSERT INTO contact_onboarding_steps" in query
    assert args[2] == SetupStepStatus.NOT_STARTED.value
    assert args[3] == list(CONTACT_LEVEL_STEP_KEYS)


@pytest.mark.asyncio
async def test_ensure_profile_step():
    """Ensure profile step inserts only complete_profile."""
    conn = _FakeConn()
    repo = ContactOnboardingRepository(db_connection=conn)

    await repo.ensure_profile_step(organization_id=ORG_ID, contact_id=CONTACT_ID)

    query, args = conn.execute_calls[0]
    assert args[2] == ContactOnboardingStep.COMPLETE_PROFILE.value


@pytest.mark.asyncio
async def test_list_steps():
    """List steps ordered by canonical wizard order."""
    conn = _FakeConn(
        rows=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": "completed",
            },
            {
                "step_key": ContactOnboardingStep.REVIEW.value,
                "status": "not_started",
            },
        ]
    )
    repo = ContactOnboardingRepository(db_connection=conn)

    steps = await repo.list_steps(organization_id=ORG_ID, contact_id=CONTACT_ID)

    assert len(steps) == 2
    assert "array_position" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_list_steps_empty():
    """No onboarding steps returns empty list."""
    conn = _FakeConn(rows=[])
    repo = ContactOnboardingRepository(db_connection=conn)

    assert await repo.list_steps(organization_id=ORG_ID, contact_id=CONTACT_ID) == []


@pytest.mark.asyncio
async def test_list_profile_step_found():
    """List profile step returns stored row."""
    conn = _FakeConn(
        row={
            "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
            "status": "completed",
            "completed_at": None,
            "updated_at": None,
        }
    )
    repo = ContactOnboardingRepository(db_connection=conn)

    steps = await repo.list_profile_step(organization_id=ORG_ID, contact_id=CONTACT_ID)

    assert len(steps) == 1
    assert steps[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_list_profile_step_default_not_started():
    """Missing profile step returns synthetic not_started row."""
    conn = _FakeConn(row=None)
    repo = ContactOnboardingRepository(db_connection=conn)

    steps = await repo.list_profile_step(organization_id=ORG_ID, contact_id=CONTACT_ID)

    assert len(steps) == 1
    assert steps[0]["step_key"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    assert steps[0]["status"] == SetupStepStatus.NOT_STARTED.value


@pytest.mark.asyncio
async def test_complete_step():
    """Complete step marks status completed."""
    conn = _FakeConn(
        row={
            "step_key": ContactOnboardingStep.SELECT_PROPERTIES.value,
            "status": SetupStepStatus.COMPLETED.value,
        }
    )
    repo = ContactOnboardingRepository(db_connection=conn)

    row = await repo.complete_step(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
    )

    assert row["status"] == SetupStepStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_complete_step_not_found():
    """Complete on missing step returns None."""
    conn = _FakeConn(row=None)
    repo = ContactOnboardingRepository(db_connection=conn)

    row = await repo.complete_step(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        step_key=ContactOnboardingStep.CHOOSE_UNIT.value,
    )

    assert row is None


@pytest.mark.asyncio
async def test_skip_step():
    """Skip step marks status skipped."""
    conn = _FakeConn(
        row={
            "step_key": ContactOnboardingStep.REVIEW.value,
            "status": SetupStepStatus.SKIPPED.value,
        }
    )
    repo = ContactOnboardingRepository(db_connection=conn)

    row = await repo.skip_step(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        step_key=ContactOnboardingStep.REVIEW.value,
    )

    assert row["status"] == SetupStepStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_is_wizard_completed_true():
    """All contact-level steps terminal means wizard complete."""
    conn = _FakeConn(fetchval_result=True)
    repo = ContactOnboardingRepository(db_connection=conn)

    assert await repo.is_wizard_completed(organization_id=ORG_ID, contact_id=CONTACT_ID)
    assert list(CONTACT_LEVEL_STEP_KEYS) in [
        conn.fetchval_calls[0][1][2],
        conn.fetchval_calls[0][1][-1],
    ]


@pytest.mark.asyncio
async def test_is_wizard_completed_false():
    """Incomplete wizard returns False."""
    conn = _FakeConn(fetchval_result=False)
    repo = ContactOnboardingRepository(db_connection=conn)

    assert not await repo.is_wizard_completed(organization_id=ORG_ID, contact_id=CONTACT_ID)
