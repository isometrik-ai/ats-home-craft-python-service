"""Unit tests for InviteRepository with fake asyncpg connection."""

import pytest

from apps.user_service.app.db.repositories.invite_repository import InviteRepository
from apps.user_service.app.schemas.enums import InviteStatus


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Init fake connection placeholders."""
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute = None
        self.row = None
        self.rows = []
        self.val = 0

    async def fetchrow(self, query, *args):
        """Record fetchrow call."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetch(self, query, *args):
        """Record fetch call."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchval(self, query, *args):
        """Record fetchval call."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val


@pytest.mark.asyncio
async def test_get_invite_by_token_for_update_clause():
    """FOR UPDATE added when requested."""

    conn = _FakeConn()
    conn.row = {"id": "i1"}
    repo = InviteRepository(db_connection=conn)

    invite = await repo.get_invite_by_token("token", for_update=True)

    assert invite["id"] == "i1"
    assert "FOR UPDATE" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_check_existing_invite_builds_params():
    """Status optional changes param count."""

    conn = _FakeConn()
    repo = InviteRepository(db_connection=conn)

    conn.row = {"id": "i1"}
    res = await repo.check_existing_invite("org1", "e@example.com", status=None)
    assert res["id"] == "i1"
    assert conn.fetchrow_calls[0][1] == ("org1", "e@example.com")

    conn.fetchrow_calls.clear()
    conn.row = None
    res2 = await repo.check_existing_invite(
        "org1", "e@example.com", status=InviteStatus.PENDING.value
    )
    assert res2 is None
    assert len(conn.fetchrow_calls[0][1]) == 3


@pytest.mark.asyncio
async def test_update_invite_status_calls_execute():
    """update_invite_status executes and handles accepted_by optional."""

    executed = {}

    async def fake_execute(query, *args):
        executed["call"] = (query.strip(), args)
        return None

    conn = _FakeConn()
    conn.execute = fake_execute
    repo = InviteRepository(db_connection=conn)

    await repo.update_invite_status("id1", InviteStatus.ACCEPTED.value, accepted_by="u1")

    assert "UPDATE organization_invites" in executed["call"][0]
    assert executed["call"][1][0] == InviteStatus.ACCEPTED.value
