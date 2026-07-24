"""Unit tests for OrganizationDeleteRequestRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.organization_delete_request_repository import (
    OrganizationDeleteRequestRepository,
)
from apps.user_service.app.schemas.enums import DeleteRequestStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
REQUEST_ID = "660e8400-e29b-41d4-a716-446655440001"
USER_ID = "770e8400-e29b-41d4-a716-446655440002"


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


@pytest.mark.asyncio
async def test_create_and_get_delete_request():
    conn = _FakeConn(row={"id": REQUEST_ID, "organization_id": ORG_ID})
    repo = OrganizationDeleteRequestRepository(db_connection=conn)

    created = await repo.create_delete_request(ORG_ID, USER_ID)
    assert created["id"] == REQUEST_ID
    assert "INSERT INTO organization_delete_requests" in conn.fetchrow_calls[0][0]

    by_id = await repo.get_delete_request_by_id(REQUEST_ID)
    assert by_id["id"] == REQUEST_ID

    conn.row = None
    assert await repo.get_delete_request_by_id(REQUEST_ID) is None


@pytest.mark.asyncio
async def test_get_pending_request_by_organization_and_requester():
    conn = _FakeConn(row={"id": REQUEST_ID})
    repo = OrganizationDeleteRequestRepository(db_connection=conn)

    pending = await repo.get_pending_request_by_organization_and_requester(ORG_ID, USER_ID)
    assert pending["id"] == REQUEST_ID
    _, args = conn.fetchrow_calls[0]
    assert args[2] == DeleteRequestStatus.PENDING.value


@pytest.mark.asyncio
async def test_list_and_count_with_filters():
    conn = _FakeConn(rows=[{"id": REQUEST_ID}], row={"count": 3})
    repo = OrganizationDeleteRequestRepository(db_connection=conn)

    listed = await repo.get_delete_requests_list(
        organization_id=ORG_ID,
        status=DeleteRequestStatus.PENDING.value,
        limit=10,
        offset=5,
    )
    assert len(listed) == 1
    list_query, list_args = conn.fetch_calls[0]
    assert "organization_id = $1" in list_query
    assert "status = $2" in list_query
    assert list_args[-2:] == (10, 5)

    count = await repo.get_delete_requests_count(
        organization_id=ORG_ID,
        status=DeleteRequestStatus.PENDING.value,
    )
    assert count == 3

    conn.row = None
    assert await repo.get_delete_requests_count() == 0


@pytest.mark.asyncio
async def test_update_status_approve_and_reject():
    conn = _FakeConn(row={"id": REQUEST_ID, "status": DeleteRequestStatus.APPROVED.value})
    repo = OrganizationDeleteRequestRepository(db_connection=conn)

    approved = await repo.approve_delete_request(REQUEST_ID, USER_ID, "Approved for closure")
    assert approved["status"] == DeleteRequestStatus.APPROVED.value

    conn.row = {"id": REQUEST_ID, "status": DeleteRequestStatus.REJECTED.value}
    rejected = await repo.reject_delete_request(REQUEST_ID, USER_ID, "Not approved")
    assert rejected["status"] == DeleteRequestStatus.REJECTED.value

    conn.row = None
    with pytest.raises(ValueError, match="already processed"):
        await repo.update_delete_request_status(
            REQUEST_ID,
            DeleteRequestStatus.APPROVED.value,
            USER_ID,
            "Missing row",
        )
