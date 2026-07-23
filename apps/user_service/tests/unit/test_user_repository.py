"""Unit tests for UserRepository with AsyncMock db connection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.enums import OrganizationMemberStatus


def _mock_conn(*, row=None, rows=None, val=None, execute_result="UPDATE 1"):
    """Build asyncpg-like connection mock."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


def test_normalize_phone():
    """_normalize_phone strips leading plus sign."""
    repo = UserRepository(db_connection=_mock_conn())

    assert repo._normalize_phone("+919876543210") == "919876543210"  # pylint: disable=protected-access
    assert repo._normalize_phone("") == ""  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_get_organization_member_status_by_email_found():
    """Member status lookup returns status when row exists."""
    conn = _mock_conn(row={"status": "active"})
    repo = UserRepository(db_connection=conn)

    status = await repo.get_organization_member_status_by_email("user@example.com")

    assert status == "active"
    query, args = _sql_args(conn.fetchrow)
    assert "organization_members" in query
    assert args == ("user@example.com", OrganizationMemberStatus.DELETED.value)


@pytest.mark.asyncio
async def test_get_organization_member_status_by_email_missing():
    """Member status lookup returns None when no row."""
    conn = _mock_conn(row=None)
    repo = UserRepository(db_connection=conn)

    assert await repo.get_organization_member_status_by_email("missing@example.com") is None


@pytest.mark.asyncio
async def test_get_auth_user_by_email_not_found():
    """Auth user lookup returns None when email is missing."""
    conn = _mock_conn(row=None)
    repo = UserRepository(db_connection=conn)

    assert await repo.get_auth_user_by_email("missing@example.com") is None


@pytest.mark.asyncio
async def test_get_auth_user_by_phone_not_found():
    """Phone lookup returns None when phone is missing."""
    conn = _mock_conn(row=None)
    repo = UserRepository(db_connection=conn)

    assert await repo.get_auth_user_by_phone("+1999") is None


@pytest.mark.asyncio
async def test_get_auth_user_by_email():
    """Auth user lookup converts row to dict."""
    conn = _mock_conn(row={"id": "u1", "email": "a@b.com"})
    repo = UserRepository(db_connection=conn)

    user = await repo.get_auth_user_by_email("a@b.com")

    assert user == {"id": "u1", "email": "a@b.com"}
    query, _ = _sql_args(conn.fetchrow)
    assert "FROM auth.users" in query


@pytest.mark.asyncio
async def test_get_auth_user_by_phone():
    """Phone lookup queries auth.users by phone."""
    conn = _mock_conn(row={"id": "u1", "phone": "+1555"})
    repo = UserRepository(db_connection=conn)

    user = await repo.get_auth_user_by_phone("+1555")

    assert user["phone"] == "+1555"
    query, args = _sql_args(conn.fetchrow)
    assert "phone = $1" in query
    assert args == ("+1555",)


@pytest.mark.asyncio
async def test_get_auth_users_by_phone_or_email_both():
    """Combined lookup builds OR clause for phone and email."""
    conn = _mock_conn(rows=[{"id": "u1"}, {"id": "u2"}])
    repo = UserRepository(db_connection=conn)

    users = await repo.get_auth_users_by_phone_or_email(phone="+1", email="a@b.com")

    assert len(users) == 2
    query, args = _sql_args(conn.fetch)
    assert " OR " in query
    assert args == ("+1", "a@b.com")


@pytest.mark.asyncio
async def test_get_auth_users_by_phone_or_email_empty():
    """No identifiers returns empty list without DB call."""
    conn = _mock_conn()
    repo = UserRepository(db_connection=conn)

    assert await repo.get_auth_users_by_phone_or_email() == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_get_user_details_by_id_all_columns():
    """Default select uses star for all columns."""
    conn = _mock_conn(row={"id": "u1", "email": "a@b.com"})
    repo = UserRepository(db_connection=conn)

    details = await repo.get_user_details_by_id("u1")

    assert details["id"] == "u1"
    query, args = _sql_args(conn.fetchrow)
    assert "SELECT *" in query
    assert args == ("u1",)


@pytest.mark.asyncio
async def test_get_user_details_by_id_allowlisted_columns():
    """Only allowlisted columns appear in SELECT."""
    conn = _mock_conn(row={"id": "u1", "email": "a@b.com"})
    repo = UserRepository(db_connection=conn)

    await repo.get_user_details_by_id("u1", select_columns=["email", "invalid_col"])

    query, _ = _sql_args(conn.fetchrow)
    assert "email" in query
    assert "invalid_col" not in query


@pytest.mark.asyncio
async def test_phone_exists_for_other_user_exclude_self():
    """Phone check excludes current user when user_id provided."""
    conn = _mock_conn(row={"?column?": 1})
    repo = UserRepository(db_connection=conn)

    exists = await repo.phone_exists_for_other_user("+1555", user_id="u1")

    assert exists is True
    query, args = _sql_args(conn.fetchrow)
    assert "id != $2" in query
    assert args == ("1555", "u1")


@pytest.mark.asyncio
async def test_verify_current_password_valid():
    """Password verification returns True when crypt matches."""
    conn = _mock_conn(val=True)
    repo = UserRepository(db_connection=conn)

    ok = await repo.verify_current_password("u1", "secret")

    assert ok is True
    query, args = _sql_args(conn.fetchval)
    assert "crypt($1, encrypted_password)" in query
    assert args == ("secret", "u1")


@pytest.mark.asyncio
async def test_verify_current_password_invalid():
    """Password verification returns False when crypt does not match."""
    conn = _mock_conn(val=False)
    repo = UserRepository(db_connection=conn)

    assert await repo.verify_current_password("u1", "wrong") is False


@pytest.mark.asyncio
async def test_verify_credentials_by_email_not_found():
    """Email credential check returns False when user missing."""
    conn = _mock_conn(row=None)
    repo = UserRepository(db_connection=conn)

    assert await repo._verify_credentials_by_email("a@b.com", "pw") is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_verify_credentials_by_email_wrong_password():
    """Email credential check returns False when password invalid."""
    conn = _mock_conn(row={"password_valid": False})
    repo = UserRepository(db_connection=conn)

    assert await repo._verify_credentials_by_email("a@b.com", "pw") is False  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_verify_credentials_by_email_success():
    """Email credential check returns True when both match."""
    conn = _mock_conn(row={"password_valid": True})
    repo = UserRepository(db_connection=conn)

    assert await repo._verify_credentials_by_email("a@b.com", "pw") is True  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_get_user_details_by_id_missing():
    """get_user_details_by_id returns None when user not found."""
    conn = _mock_conn(row=None)
    repo = UserRepository(db_connection=conn)

    assert await repo.get_user_details_by_id("missing-user") is None


@pytest.mark.asyncio
async def test_phone_exists_for_other_user_without_exclude():
    """Phone check without user_id scans all users."""
    conn = _mock_conn(row={"?column?": 1})
    repo = UserRepository(db_connection=conn)

    assert await repo.phone_exists_for_other_user("+1555") is True
    query, args = _sql_args(conn.fetchrow)
    assert "id !=" not in query
    assert args == ("1555",)
