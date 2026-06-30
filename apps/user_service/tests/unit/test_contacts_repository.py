"""Unit tests for ContactsRepository."""

import pytest

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.schemas.enums import ClientStatus


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, row=None):
        self.row = row
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query, *args):
        """Record fetchrow calls and return the configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_get_contact_id_by_email_queries_auth_users():
    """Email lookup joins auth.users on contact user_id."""
    conn = _FakeConn(row={"id": "contact-1"})
    repo = ContactsRepository(db_connection=conn)

    result = await repo.get_contact_id_by_email(
        organization_id="org-1",
        email="User@Example.com",
    )

    assert result == "contact-1"
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "auth.users" in query
    assert args == ("org-1", ClientStatus.DELETED.value, "user@example.com")


@pytest.mark.asyncio
async def test_get_contact_id_by_email_not_found():
    """No row means email is not taken."""
    conn = _FakeConn(row=None)
    repo = ContactsRepository(db_connection=conn)

    result = await repo.get_contact_id_by_email(
        organization_id="org-1",
        email="missing@example.com",
    )

    assert result is None


@pytest.mark.asyncio
async def test_get_contact_id_by_email_blank_email():
    """Blank email short-circuits without a DB call."""
    conn = _FakeConn()
    repo = ContactsRepository(db_connection=conn)

    result = await repo.get_contact_id_by_email(organization_id="org-1", email="   ")

    assert result is None
    assert not conn.fetchrow_calls


def test_normalize_row_empty_lists_to_objects():
    """Legacy [] values for object-shaped JSONB columns become {}."""
    row = ContactsRepository._normalize_row(  # pylint: disable=protected-access
        {
            "social_pages": [],
            "documents": [],
            "phones": [],
            "tags": None,
        }
    )

    assert isinstance(row["social_pages"], dict) and not row["social_pages"]
    assert isinstance(row["documents"], dict) and not row["documents"]
    assert isinstance(row["phones"], list) and not row["phones"]
    assert isinstance(row["tags"], list) and not row["tags"]
