"""Unit tests for ProjectSetupRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.project_setup_repository import (
    ProjectSetupRepository,
)
from apps.user_service.app.schemas.enums import ProjectSetupStep, SetupStepStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"


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
async def test_ensure_steps_inserts_missing():
    """Ensure steps bulk-inserts wizard steps."""
    conn = _FakeConn()
    repo = ProjectSetupRepository(db_connection=conn)

    await repo.ensure_steps(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        step_keys=[ProjectSetupStep.PROJECT_BASICS.value],
    )

    query, args = conn.execute_calls[0]
    assert "INSERT INTO project_setup_steps" in query
    assert "ON CONFLICT (project_id, step_key) DO NOTHING" in query
    assert args[2] == SetupStepStatus.NOT_STARTED.value


@pytest.mark.asyncio
async def test_ensure_steps_empty_noop():
    """Empty step list skips database call."""
    conn = _FakeConn()
    repo = ProjectSetupRepository(db_connection=conn)

    await repo.ensure_steps(organization_id=ORG_ID, project_id=PROJECT_ID, step_keys=[])

    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_skip_steps_marks_skipped():
    """Skip steps upserts skipped status."""
    conn = _FakeConn()
    repo = ProjectSetupRepository(db_connection=conn)

    await repo.skip_steps(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        step_keys=[ProjectSetupStep.SITE_MAP.value],
    )

    query, args = conn.execute_calls[0]
    assert args[2] == SetupStepStatus.SKIPPED.value
    assert "completed_at" in query


@pytest.mark.asyncio
async def test_skip_steps_empty_noop():
    """Empty skip list skips database call."""
    conn = _FakeConn()
    repo = ProjectSetupRepository(db_connection=conn)

    await repo.skip_steps(organization_id=ORG_ID, project_id=PROJECT_ID, step_keys=[])

    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_set_step_status_with_data():
    """Set step status upserts row with optional data payload."""
    conn = _FakeConn(
        row={
            "step_key": ProjectSetupStep.TOWER_BUILDER.value,
            "status": SetupStepStatus.COMPLETED.value,
        }
    )
    repo = ProjectSetupRepository(db_connection=conn)

    row = await repo.set_step_status(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        step_key=ProjectSetupStep.TOWER_BUILDER.value,
        status=SetupStepStatus.COMPLETED.value,
        data={"towers": 2},
    )

    assert row["status"] == SetupStepStatus.COMPLETED.value
    query, _ = conn.fetchrow_calls[0]
    assert "::project_setup_step" in query
    assert "::setup_step_status" in query


@pytest.mark.asyncio
async def test_set_step_status_without_data():
    """Set step status without data passes NULL json."""
    conn = _FakeConn(row={"step_key": ProjectSetupStep.INVENTORIES.value, "status": "in_progress"})
    repo = ProjectSetupRepository(db_connection=conn)

    row = await repo.set_step_status(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        step_key=ProjectSetupStep.INVENTORIES.value,
        status=SetupStepStatus.IN_PROGRESS.value,
    )

    assert row is not None
    assert conn.fetchrow_calls[0][1][4] is None


@pytest.mark.asyncio
async def test_list_steps():
    """List steps ordered by canonical wizard order."""
    conn = _FakeConn(
        rows=[
            {"step_key": ProjectSetupStep.PROJECT_BASICS.value, "status": "completed"},
            {"step_key": ProjectSetupStep.TOWER_BUILDER.value, "status": "not_started"},
        ]
    )
    repo = ProjectSetupRepository(db_connection=conn)

    steps = await repo.list_steps(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert len(steps) == 2
    query, _ = conn.fetch_calls[0]
    assert "array_position" in query


@pytest.mark.asyncio
async def test_list_steps_empty():
    """No setup steps returns empty list."""
    conn = _FakeConn(rows=[])
    repo = ProjectSetupRepository(db_connection=conn)

    assert await repo.list_steps(organization_id=ORG_ID, project_id=PROJECT_ID) == []


@pytest.mark.asyncio
async def test_get_step():
    """Get single setup step row."""
    conn = _FakeConn(row={"step_key": ProjectSetupStep.FACILITIES.value, "status": "skipped"})
    repo = ProjectSetupRepository(db_connection=conn)

    step = await repo.get_step(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        step_key=ProjectSetupStep.FACILITIES.value,
    )

    assert step["status"] == "skipped"


@pytest.mark.asyncio
async def test_get_step_not_found():
    """Missing step returns None."""
    conn = _FakeConn(row=None)
    repo = ProjectSetupRepository(db_connection=conn)

    step = await repo.get_step(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        step_key=ProjectSetupStep.FLOOR_PLANS.value,
    )

    assert step is None


@pytest.mark.asyncio
async def test_is_completed_true():
    """All steps completed or skipped means wizard complete."""
    conn = _FakeConn(fetchval_result=True)
    repo = ProjectSetupRepository(db_connection=conn)

    assert await repo.is_completed(organization_id=ORG_ID, project_id=PROJECT_ID) is True
    assert "COUNT(*) FILTER" in conn.fetchval_calls[0][0]


@pytest.mark.asyncio
async def test_is_completed_false():
    """Incomplete wizard returns False."""
    conn = _FakeConn(fetchval_result=False)
    repo = ProjectSetupRepository(db_connection=conn)

    assert await repo.is_completed(organization_id=ORG_ID, project_id=PROJECT_ID) is False
