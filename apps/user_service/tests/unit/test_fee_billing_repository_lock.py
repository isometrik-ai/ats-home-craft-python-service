"""Unit tests for fee billing unit lock helper."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.fee_billing_repository import (
    FeeBillingRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"


class _FakeConn:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return []


@pytest.mark.asyncio
async def test_lock_unit_for_fee_billing_uses_advisory_and_row_locks() -> None:
    """Move-out and payment paths share a per-unit transactional lock."""
    conn = _FakeConn()
    repo = FeeBillingRepository(db_connection=conn)

    await repo.lock_unit_for_fee_billing(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
    )

    assert "pg_advisory_xact_lock" in conn.execute_calls[0][0]
    assert f"unit_fee_billing:{ORG_ID}:{PROJECT_ID}:{UNIT_ID}" in conn.execute_calls[0][1]
    assert "FOR UPDATE" in conn.fetch_calls[0][0]
    assert conn.fetch_calls[0][1] == (ORG_ID, PROJECT_ID, UNIT_ID)
