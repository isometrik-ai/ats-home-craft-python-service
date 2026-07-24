"""Unit tests for InviteRepository with fake asyncpg connection."""

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.invite_repository import (
    InviteRepository,
    PatchPendingInviteResult,
)
from apps.user_service.app.schemas.enums import InviteStatus
from libs.shared_utils.http_exceptions import NotFoundException


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Init fake connection placeholders."""
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute_impl = None
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

    async def execute(self, query, *args):
        """Record execute call."""
        if self.execute_impl is not None:
            return await self.execute_impl(query, *args)
        return "UPDATE 1"


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
    conn.execute_impl = fake_execute
    repo = InviteRepository(db_connection=conn)

    await repo.update_invite_status("id1", InviteStatus.ACCEPTED.value, accepted_by="u1")

    assert "UPDATE organization_invites" in executed["call"][0]
    assert executed["call"][1][0] == InviteStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_create_invite_and_get_by_id():
    """Create invite inserts json metadata; get by id uses org join."""
    conn = _FakeConn()
    conn.row = {"id": "i1", "email": "e@example.com"}
    repo = InviteRepository(db_connection=conn)

    created = await repo.create_invite(
        {
            "organization_id": "org1",
            "email": "e@example.com",
            "role_id": "role1",
            "token_hash": "hash",
            "invited_by": "u1",
            "expires_at": datetime(2026, 12, 31, tzinfo=timezone.utc),
            "metadata": {"source": "admin"},
        }
    )
    assert created["email"] == "e@example.com"
    assert "INSERT INTO organization_invites" in conn.fetchrow_calls[0][0]

    invite = await repo.get_invite_by_id("i1")
    assert invite["email"] == "e@example.com"
    assert "LEFT JOIN organizations o" in conn.fetchrow_calls[1][0]


@pytest.mark.asyncio
async def test_get_organization_invites_and_count():
    """List and count honor optional status filter."""
    conn = _FakeConn()
    conn.rows = [{"id": "i1"}]
    conn.val = 4
    repo = InviteRepository(db_connection=conn)

    invites = await repo.get_organization_invites(
        "org1", limit=10, offset=0, status=InviteStatus.PENDING.value
    )
    assert len(invites) == 1

    count = await repo.get_organization_invites_count("org1", status=InviteStatus.PENDING.value)
    assert count == 4


@pytest.mark.asyncio
async def test_check_user_membership():
    """Membership check returns boolean from EXISTS."""
    conn = _FakeConn()
    conn.val = True
    repo = InviteRepository(db_connection=conn)

    assert await repo.check_user_membership("org1", "e@example.com") is True


@pytest.mark.asyncio
async def test_update_invite_status_not_found_raises():
    """Missing invite raises NotFoundException."""
    conn = _FakeConn()

    async def fake_execute(*_args, **_kwargs):
        return "UPDATE 0"

    conn.execute_impl = fake_execute
    repo = InviteRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.update_invite_status("missing", InviteStatus.ACCEPTED.value)


@pytest.mark.asyncio
async def test_update_invite_expiration_and_token():
    """Expiration and token refresh return updated rows."""
    conn = _FakeConn()
    conn.row = {"id": "i1", "expires_at": datetime(2026, 12, 31, tzinfo=timezone.utc)}
    repo = InviteRepository(db_connection=conn)

    expires = datetime(2027, 1, 1, tzinfo=timezone.utc)
    updated = await repo.update_invite_expiration("i1", expires)
    assert updated["id"] == "i1"

    conn.row = {"id": "i1", "token_hash": "new-hash"}
    refreshed = await repo.update_invite_token_and_expiration("i1", "new-hash", expires)
    assert refreshed["token_hash"] == "new-hash"


@pytest.mark.asyncio
async def test_renew_expired_invite():
    """Renew only updates expired pending invites."""
    conn = _FakeConn()
    conn.row = {"id": "i1", "status": InviteStatus.PENDING.value}
    repo = InviteRepository(db_connection=conn)

    renewed = await repo.renew_expired_invite(
        "i1",
        {
            "role_id": "role1",
            "token_hash": "hash2",
            "invited_by": "u1",
            "expires_at": datetime(2027, 1, 1, tzinfo=timezone.utc),
            "metadata": {},
        },
    )
    assert renewed["status"] == InviteStatus.PENDING.value
    assert "expires_at <= NOW()" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_patch_pending_invitation_outcomes():
    """Patch returns flags and updated row metadata."""
    conn = _FakeConn()
    repo = InviteRepository(db_connection=conn)

    conn.row = None
    empty = await repo.patch_pending_invitation(
        "i1", "org1", InviteStatus.PENDING.value, role_id="role2"
    )
    assert empty == PatchPendingInviteResult(updated_row=None, invite_ok=False, role_ok=False)

    conn.row = {
        "invite_ok": True,
        "role_ok": False,
        "id": None,
    }
    role_missing = await repo.patch_pending_invitation(
        "i1", "org1", InviteStatus.PENDING.value, role_id="role2"
    )
    assert role_missing.invite_ok is True
    assert role_missing.role_ok is False

    conn.row = {
        "invite_ok": True,
        "role_ok": True,
        "id": "i1",
        "organization_id": "org1",
        "email": "e@example.com",
        "role_id": "role2",
        "token_hash": "hash",
        "invited_by": "u1",
        "status": InviteStatus.PENDING.value,
        "expires_at": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "metadata": {},
        "previous_role_id": "role1",
    }
    patched = await repo.patch_pending_invitation(
        "i1", "org1", InviteStatus.PENDING.value, role_id="role2"
    )
    assert patched.updated_row["role_id"] == "role2"
    assert patched.previous_role_id == "role1"


@pytest.mark.asyncio
async def test_delete_invite_success_and_not_found():
    """Delete invite validates org scope."""
    conn = _FakeConn()
    conn.val = "i1"
    repo = InviteRepository(db_connection=conn)

    await repo.delete_invite("i1", "org1")

    conn.val = None
    with pytest.raises(NotFoundException):
        await repo.delete_invite("missing", "org1")
