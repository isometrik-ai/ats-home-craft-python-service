"""Unit tests for HouseholdInvitationsRepository with fake connection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.household_invitations_repository import (
    HouseholdInvitationsRepository,
)
from apps.user_service.app.schemas.enums import HouseholdInvitationStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
CONTACT_UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
INVITED_BY_ID = "880e8400-e29b-41d4-a716-446655440003"
INVITATION_ID = "990e8400-e29b-41d4-a716-446655440004"
TOKEN_HASH = "abc123hash"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_result="UPDATE 1"):
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result
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
        return self.execute_result


@pytest.mark.asyncio
async def test_insert_invitation_defaults_pending():
    """Insert creates invitation with default pending status."""
    expires = datetime(2026, 12, 31, tzinfo=timezone.utc)
    conn = _FakeConn(
        row={
            "id": INVITATION_ID,
            "status": HouseholdInvitationStatus.PENDING.value,
            "token_hash": TOKEN_HASH,
        }
    )
    repo = HouseholdInvitationsRepository(db_connection=conn)

    invitation = await repo.insert_invitation(
        {
            "organization_id": ORG_ID,
            "contact_id": CONTACT_ID,
            "contact_unit_id": CONTACT_UNIT_ID,
            "invited_by_contact_id": INVITED_BY_ID,
            "phone_isd_code": "+91",
            "phone_number": "9876543210",
            "token": "plain-token",
            "token_hash": TOKEN_HASH,
            "expires_at": expires,
        }
    )

    assert invitation["status"] == HouseholdInvitationStatus.PENDING.value
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO household_invitations" in query
    assert "::household_invitation_status" in query
    assert args[8] == HouseholdInvitationStatus.PENDING.value


@pytest.mark.asyncio
async def test_get_by_token_hash():
    """Lookup by token hash without lock."""
    conn = _FakeConn(row={"id": INVITATION_ID, "token_hash": TOKEN_HASH})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    invitation = await repo.get_by_token_hash(TOKEN_HASH)

    assert invitation["id"] == INVITATION_ID
    query, _ = conn.fetchrow_calls[0]
    assert "FOR UPDATE" not in query


@pytest.mark.asyncio
async def test_get_by_token_hash_for_update():
    """Lookup by token hash with row lock."""
    conn = _FakeConn(row={"id": INVITATION_ID})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    await repo.get_by_token_hash(TOKEN_HASH, for_update=True)

    assert "FOR UPDATE" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_get_by_token_hash_not_found():
    """Missing token hash returns None."""
    conn = _FakeConn(row=None)
    repo = HouseholdInvitationsRepository(db_connection=conn)

    assert await repo.get_by_token_hash("missing") is None


@pytest.mark.asyncio
async def test_get_pending_by_contact_unit():
    """Pending invitation lookup scopes to contact_unit."""
    conn = _FakeConn(row={"id": INVITATION_ID, "status": "pending"})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    invitation = await repo.get_pending_by_contact_unit(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    assert invitation is not None
    query, args = conn.fetchrow_calls[0]
    assert args[2] == HouseholdInvitationStatus.PENDING.value


@pytest.mark.asyncio
async def test_get_by_contact_unit():
    """Fetch invitation row for contact_unit link."""
    conn = _FakeConn(row={"id": INVITATION_ID})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    invitation = await repo.get_by_contact_unit(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    assert invitation["id"] == INVITATION_ID


@pytest.mark.asyncio
async def test_reactivate_invitation():
    """Reactivate updates cancelled/expired/declined rows."""
    expires = datetime(2026, 12, 31, tzinfo=timezone.utc)
    conn = _FakeConn(row={"id": INVITATION_ID, "status": "pending"})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    row = await repo.reactivate_invitation(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        invited_by_contact_id=INVITED_BY_ID,
        phone_isd_code="+91",
        phone_number="9876543210",
        token="new-token",
        token_hash="new-hash",
        expires_at=expires,
    )

    assert row["status"] == "pending"
    query, _ = conn.fetchrow_calls[0]
    assert "UPDATE household_invitations" in query
    assert "accepted_at = NULL" in query


@pytest.mark.asyncio
async def test_reactivate_invitation_not_found():
    """Non-reactivatable invitation returns None."""
    conn = _FakeConn(row=None)
    repo = HouseholdInvitationsRepository(db_connection=conn)

    row = await repo.reactivate_invitation(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        invited_by_contact_id=INVITED_BY_ID,
        phone_isd_code="+91",
        phone_number="9876543210",
        token="new-token",
        token_hash="new-hash",
        expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

    assert row is None


@pytest.mark.asyncio
async def test_cancel_by_contact_unit_success():
    """Cancel pending invitation returns True on update."""
    conn = _FakeConn(execute_result="UPDATE 1")
    repo = HouseholdInvitationsRepository(db_connection=conn)

    cancelled = await repo.cancel_by_contact_unit(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    assert cancelled is True
    query, args = conn.execute_calls[0]
    assert args[2] == HouseholdInvitationStatus.PENDING.value
    assert args[3] == HouseholdInvitationStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_by_contact_unit_no_rows():
    """Cancel with zero rows updated returns False."""
    conn = _FakeConn(execute_result="UPDATE 0")
    repo = HouseholdInvitationsRepository(db_connection=conn)

    assert not await repo.cancel_by_contact_unit(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )


@pytest.mark.asyncio
async def test_renew_invitation():
    """Renew refreshes token and expiry for pending invitation."""
    expires = datetime(2026, 12, 31, tzinfo=timezone.utc)
    conn = _FakeConn(row={"id": INVITATION_ID, "token_hash": "renewed-hash"})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    row = await repo.renew_invitation(
        invitation_id=INVITATION_ID,
        token="renewed-token",
        token_hash="renewed-hash",
        expires_at=expires,
    )

    assert row["token_hash"] == "renewed-hash"
    assert HouseholdInvitationStatus.PENDING.value in conn.fetchrow_calls[0][1]


@pytest.mark.asyncio
async def test_update_pending_phone():
    """Update phone on pending invitation."""
    conn = _FakeConn(row={"id": INVITATION_ID})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    row = await repo.update_pending_phone(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        phone_isd_code="+1",
        phone_number="5551234",
    )

    assert row["id"] == INVITATION_ID


@pytest.mark.asyncio
async def test_mark_accepted_and_declined():
    """Mark accepted and declined update status."""
    conn = _FakeConn(row={"id": INVITATION_ID, "status": "accepted"})
    repo = HouseholdInvitationsRepository(db_connection=conn)

    accepted = await repo.mark_accepted(invitation_id=INVITATION_ID)
    assert accepted["status"] == "accepted"
    assert "accepted_at = now()" in conn.fetchrow_calls[0][0]

    conn.row = {"id": INVITATION_ID, "status": "declined"}
    declined = await repo.mark_declined(invitation_id=INVITATION_ID)
    assert declined["status"] == "declined"
    assert conn.fetchrow_calls[1][1][2] == HouseholdInvitationStatus.PENDING.value


@pytest.mark.asyncio
async def test_mark_declined_not_found():
    """Decline on non-pending invitation returns None."""
    conn = _FakeConn(row=None)
    repo = HouseholdInvitationsRepository(db_connection=conn)

    assert await repo.mark_declined(invitation_id=INVITATION_ID) is None
