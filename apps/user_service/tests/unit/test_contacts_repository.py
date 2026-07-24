"""Unit tests for ContactsRepository SQL-building with fake connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.schemas.enums import ClientStatus, ContactType
from libs.shared_utils.http_exceptions import NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


def _async_mock_conn(*, rows=None, row=None, val=None, execute_result=None):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        """Record fetch and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval and return configured value."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val


@pytest.mark.asyncio
async def test_get_contact_id_by_email_lookup():
    """Email lookup joins auth.users and normalizes email."""
    conn = _FakeConn(row={"id": "c1"})
    repo = ContactsRepository(db_connection=conn)

    contact_id = await repo.get_contact_id_by_email(
        organization_id=ORG_ID,
        email="Jane@Example.com",
    )

    assert contact_id == "c1"
    query, args = conn.fetchrow_calls[0]
    assert "FROM contacts ct" in query
    assert "auth.users" in query
    assert args[2] == "jane@example.com"


@pytest.mark.asyncio
async def test_get_contact_ids_by_emails_bulk():
    """Bulk email lookup uses ANY($3::text[])."""
    conn = _FakeConn(
        rows=[
            {"email_norm": "a@example.com", "id": "c1"},
            {"email_norm": "b@example.com", "id": "c2"},
        ]
    )
    repo = ContactsRepository(db_connection=conn)

    mapping = await repo.get_contact_ids_by_emails(
        organization_id=ORG_ID,
        emails=["a@example.com", "b@example.com"],
    )

    assert mapping == {"a@example.com": "c1", "b@example.com": "c2"}
    query, args = conn.fetch_calls[0]
    assert "= ANY($3::text[])" in query
    assert ClientStatus.DELETED.value in args


@pytest.mark.asyncio
async def test_list_contacts_status_filter():
    """List adds status predicate when provided."""
    conn = _FakeConn(rows=[], val=0)
    repo = ContactsRepository(db_connection=conn)

    await repo.list_contacts(
        organization_id=ORG_ID,
        search=None,
        status=ClientStatus.ACTIVE.value,
        contact_type=None,
        page=1,
        page_size=20,
    )

    count_query, count_args = conn.fetchval_calls[0]
    assert "status = $3" in count_query
    assert ClientStatus.ACTIVE.value in count_args
    list_query, _ = conn.fetch_calls[0]
    assert "company_names_by_contact" in list_query


@pytest.mark.asyncio
async def test_list_contacts_search_predicate():
    """List adds name/email ILIKE when search is set."""
    conn = _FakeConn(rows=[], val=0)
    repo = ContactsRepository(db_connection=conn)

    await repo.list_contacts(
        organization_id=ORG_ID,
        search="jane",
        status=None,
        contact_type=ContactType.OWNER.value,
        page=2,
        page_size=10,
    )

    count_query, count_args = conn.fetchval_calls[0]
    assert "ILIKE" in count_query
    assert "%jane%" in count_args
    assert ContactType.OWNER.value in count_args
    _, list_args = conn.fetch_calls[0]
    assert list_args[-2] == 10
    assert list_args[-1] == 10


@pytest.mark.asyncio
async def test_filter_contact_ids_in_org():
    """Filter returns subset of ids existing in organization."""
    conn = _FakeConn(rows=[{"id": "c1"}, {"id": "c2"}])
    repo = ContactsRepository(db_connection=conn)

    found = await repo.filter_contact_ids_in_organization(
        organization_id=ORG_ID,
        contact_ids=["c1", "c2", "missing"],
    )

    assert found == {"c1", "c2"}
    query, args = conn.fetch_calls[0]
    assert "id = ANY($2::uuid[])" in query
    assert ClientStatus.DELETED.value in args


@pytest.mark.asyncio
async def test_get_contact_ids_by_user_ids():
    """Bulk user_id lookup maps user_id to contact id."""
    conn = _FakeConn(rows=[{"user_id": "u1", "id": "c1"}])
    repo = ContactsRepository(db_connection=conn)

    mapping = await repo.get_contact_ids_by_user_ids(
        organization_id=ORG_ID,
        user_ids=["u1"],
    )

    assert mapping == {"u1": "c1"}
    query, _ = conn.fetch_calls[0]
    assert "user_id = ANY($3::uuid[])" in query


@pytest.mark.asyncio
async def test_is_active_contact_user_true():
    """Active contact user check scopes by org and status."""
    conn = _FakeConn(val=1)
    repo = ContactsRepository(db_connection=conn)

    active = await repo.is_active_contact_user_for_organization(
        user_id="u1",
        organization_id=ORG_ID,
    )

    assert active is True
    query, args = conn.fetchval_calls[0]
    assert "FROM contacts ct" in query
    assert args[0] == "u1"
    assert args[1] == ORG_ID


@pytest.mark.asyncio
async def test_create_contacts_bulk_insert_sql():
    """Bulk create uses INSERT with ON CONFLICT partial index."""
    conn = _FakeConn(rows=[{"id": "c1", "organization_id": ORG_ID}])
    repo = ContactsRepository(db_connection=conn)

    inserted = await repo.create_contacts(
        [
            {
                "organization_id": ORG_ID,
                "user_id": "u1",
                "first_name": "Jane",
                "phones": [],
                "custom_fields": [],
                "additional_data": {},
                "social_pages": [],
            }
        ]
    )

    assert inserted[0]["id"] == "c1"
    query, _ = conn.fetch_calls[0]
    assert "INSERT INTO contacts" in query
    assert "ON CONFLICT (organization_id, user_id)" in query
    assert "DO NOTHING" in query


def test_coerce_jsonb_array_fields_parses_strings():
    """JSON string fields are parsed into lists."""
    row = ContactsRepository._coerce_jsonb_array_fields(  # pylint: disable=protected-access
        {"phones": '["+1"]', "first_name": "Jane"},
        ("phones",),
    )

    assert row["phones"] == ["+1"]
    assert row["first_name"] == "Jane"


@pytest.mark.asyncio
async def test_get_contact_id_by_email_empty():
    """Blank email returns None without DB."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert await repo.get_contact_id_by_email(organization_id=ORG_ID, email="  ") is None
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_contact_ids_by_emails_empty():
    """Empty email list returns empty dict."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert await repo.get_contact_ids_by_emails(organization_id=ORG_ID, emails=[]) == {}
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_is_active_contact_user_missing_ids():
    """Missing user or org returns False."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert (
        await repo.is_active_contact_user_for_organization(user_id="", organization_id=ORG_ID)
        is False
    )
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_create_contacts_empty_list():
    """Empty bulk create returns empty list."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert await repo.create_contacts([]) == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_get_contact_ids_by_user_ids_empty():
    """Empty user_ids returns empty mapping."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert await repo.get_contact_ids_by_user_ids(organization_id=ORG_ID, user_ids=[]) == {}
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_create_contact_with_optional_company_link():
    """Complex CTE insert returns contact and company ids."""
    conn = _async_mock_conn(
        row={
            "contact_id": "c1",
            "company_id": "co1",
            "contact": {"id": "c1", "first_name": "Jane"},
        }
    )
    repo = ContactsRepository(db_connection=conn)

    result = await repo.create_contact_with_optional_company_link(
        organization_id=ORG_ID,
        contact_data={"first_name": "Jane", "phones": []},
        company_id=None,
        company_data={"name": "Acme", "status": "active"},
        company_addresses=None,
        make_primary=True,
    )

    assert result["contact_id"] == "c1"
    assert result["company_id"] == "co1"
    query, _ = _sql_args(conn.fetchrow)
    assert "WITH company_exists AS" in query


@pytest.mark.asyncio
async def test_get_contact_phones_for_update():
    """Phones loader parses JSON list from row."""
    conn = _async_mock_conn(row={"phones": '["+1555"]'})
    repo = ContactsRepository(db_connection=conn)

    phones = await repo.get_contact_phones_for_update(contact_id="c1", organization_id=ORG_ID)

    assert phones == ["+1555"]
    query, _ = _sql_args(conn.fetchrow)
    assert "FOR UPDATE OF ct" in query


@pytest.mark.asyncio
async def test_get_contact_for_update():
    """For-update detail coerces jsonb list fields."""
    conn = _async_mock_conn(
        row={
            "id": "c1",
            "phones": "[]",
            "companies": "[]",
            "leads": "[]",
            "addresses": "[]",
        }
    )
    repo = ContactsRepository(db_connection=conn)

    contact = await repo.get_contact_for_update(contact_id="c1", organization_id=ORG_ID)

    assert contact["id"] == "c1"
    assert contact["phones"] == []


@pytest.mark.asyncio
async def test_get_contact_for_update_by_enrichment_request_id():
    """Enrichment lookup scopes by request id with FOR UPDATE."""
    conn = _async_mock_conn(row={"id": "c1", "enrichment_request_id": "req-1"})
    repo = ContactsRepository(db_connection=conn)

    contact = await repo.get_contact_for_update_by_enrichment_request_id(
        enrichment_request_id="req-1"
    )

    assert contact["id"] == "c1"
    query, args = _sql_args(conn.fetchrow)
    assert "enrichment_request_id = $1" in query
    assert args[1] == ClientStatus.DELETED.value


@pytest.mark.asyncio
async def test_get_contact_for_update_by_enrichment_request_id_empty():
    """Blank enrichment id returns None."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert (
        await repo.get_contact_for_update_by_enrichment_request_id(enrichment_request_id="") is None
    )
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_soft_delete_contact():
    """Soft delete returns updated row."""
    conn = _async_mock_conn(row={"id": "c1", "status": ClientStatus.DELETED.value})
    repo = ContactsRepository(db_connection=conn)

    deleted = await repo.soft_delete_contact(contact_id="c1", organization_id=ORG_ID)

    assert deleted["status"] == ClientStatus.DELETED.value
    query, _ = _sql_args(conn.fetchrow)
    assert "UPDATE contacts" in query


@pytest.mark.asyncio
async def test_soft_delete_contact_not_found():
    """Missing contact raises NotFoundException."""
    conn = _async_mock_conn(row=None)
    repo = ContactsRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.soft_delete_contact(contact_id="missing", organization_id=ORG_ID)


@pytest.mark.asyncio
async def test_get_active_contact_by_user_id():
    """Active contact lookup coerces jsonb fields."""
    conn = _async_mock_conn(row={"id": "c1", "phones": "[]", "emails": "[]"})
    repo = ContactsRepository(db_connection=conn)

    contact = await repo.get_active_contact_by_user_id(user_id="u1", organization_id=ORG_ID)

    assert contact["id"] == "c1"
    assert contact["phones"] == []


@pytest.mark.asyncio
async def test_get_active_contact_by_user_id_empty():
    """Missing ids return None."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert await repo.get_active_contact_by_user_id(user_id="", organization_id=ORG_ID) is None
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_contact():
    """update_contact delegates to update_returning helper."""
    conn = _async_mock_conn(row={"id": "c1", "first_name": "Updated"})
    repo = ContactsRepository(db_connection=conn)
    repo.update_returning = AsyncMock(return_value={"id": "c1", "first_name": "Updated"})  # type: ignore[method-assign]

    updated = await repo.update_contact(
        contact_id="c1",
        organization_id=ORG_ID,
        update_data={"first_name": "Updated"},
    )

    assert updated["first_name"] == "Updated"
    repo.update_returning.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_contact_addresses():
    """create_contact_addresses bulk inserts address rows."""
    conn = _async_mock_conn(rows=[{"id": "a1", "contact_id": "c1"}])
    repo = ContactsRepository(db_connection=conn)
    repo.bulk_insert_returning = AsyncMock(return_value=[{"id": "a1"}])  # type: ignore[method-assign]

    rows = await repo.create_contact_addresses([{"contact_id": "c1", "city": "Mumbai"}])

    assert rows[0]["id"] == "a1"
    repo.bulk_insert_returning.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_contact_address():
    """update_contact_address updates one address row."""
    conn = _async_mock_conn(row={"id": "a1", "city": "Pune"})
    repo = ContactsRepository(db_connection=conn)
    repo.update_returning = AsyncMock(return_value={"id": "a1", "city": "Pune"})  # type: ignore[method-assign]

    updated = await repo.update_contact_address(
        contact_id="c1",
        address_id="a1",
        update_data={"city": "Pune"},
    )

    assert updated["city"] == "Pune"


@pytest.mark.asyncio
async def test_delete_contact_addresses():
    """delete_contact_addresses removes rows by id list."""
    conn = _async_mock_conn(execute_result="DELETE 2")
    repo = ContactsRepository(db_connection=conn)

    await repo.delete_contact_addresses(contact_id="c1", address_ids=["a1", "a2"])

    query, args = _sql_args(conn.execute)
    assert "DELETE FROM contact_addresses" in query
    assert args[0] == "c1"


@pytest.mark.asyncio
async def test_delete_all_contact_addresses():
    """delete_all_contact_addresses removes all rows for contact."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    await repo.delete_all_contact_addresses(contact_id="c1")

    query, args = _sql_args(conn.execute)
    assert "DELETE FROM contact_addresses" in query
    assert args[0] == "c1"


@pytest.mark.asyncio
async def test_get_contact_addresses():
    """get_contact_addresses returns ordered rows."""
    conn = _FakeConn(rows=[{"id": "a1", "is_primary": True}])
    repo = ContactsRepository(db_connection=conn)

    rows = await repo.get_contact_addresses(contact_id="c1")

    assert rows[0]["id"] == "a1"
    query, _ = conn.fetch_calls[0]
    assert "FROM contact_addresses" in query


@pytest.mark.asyncio
async def test_get_contact_details():
    """get_contact_details loads joined companies and addresses."""
    conn = _async_mock_conn(
        row={
            "id": "c1",
            "phones": "[]",
            "emails": "[]",
            "companies": "[]",
            "leads": "[]",
            "addresses": "[]",
        }
    )
    repo = ContactsRepository(db_connection=conn)

    details = await repo.get_contact_details(contact_id="c1", organization_id=ORG_ID)

    assert details["id"] == "c1"
    query, _ = _sql_args(conn.fetchrow)
    assert "contact_companies cc" in query


@pytest.mark.asyncio
async def test_get_contact_details_by_phone():
    """get_contact_details_by_phone matches normalized digits."""
    conn = _async_mock_conn(
        row={
            "id": "c1",
            "phones": "[]",
            "emails": "[]",
            "companies": "[]",
            "leads": "[]",
            "addresses": "[]",
        }
    )
    repo = ContactsRepository(db_connection=conn)

    details = await repo.get_contact_details_by_phone(
        organization_id=ORG_ID,
        phone_number="9876543210",
    )

    assert details["id"] == "c1"
    query, _ = _sql_args(conn.fetchrow)
    assert "phones" in query


@pytest.mark.asyncio
async def test_get_contact_overview():
    """get_contact_overview aggregates counts by type."""
    conn = _async_mock_conn(row={"total": 10, "owners": 4, "tenants": 3, "vendors": 1})
    repo = ContactsRepository(db_connection=conn)

    overview = await repo.get_contact_overview(
        organization_id=ORG_ID,
        status=ClientStatus.ACTIVE.value,
    )

    assert overview["total"] == 10
    query, args = _sql_args(conn.fetchrow)
    assert "COUNT" in query.upper()
    assert ClientStatus.ACTIVE.value in args


@pytest.mark.asyncio
async def test_get_contacts_by_ids():
    """get_contacts_by_ids returns minimal rows for id list."""
    conn = _FakeConn(rows=[{"id": "c1"}, {"id": "c2"}])
    repo = ContactsRepository(db_connection=conn)

    rows = await repo.get_contacts_by_ids(
        organization_id=ORG_ID,
        contact_ids=["c1", "c2"],
    )

    assert len(rows) == 2
    query, _ = conn.fetch_calls[0]
    assert "= ANY" in query


@pytest.mark.asyncio
async def test_insert_contact_success():
    """insert_contact builds dynamic INSERT for property contacts."""
    conn = _async_mock_conn(
        row={
            "id": "c1",
            "organization_id": ORG_ID,
            "contact_type": ContactType.OWNER.value,
            "phones": "[]",
            "emails": "[]",
        }
    )
    repo = ContactsRepository(db_connection=conn)

    inserted = await repo.insert_contact(
        {
            "id": "c1",
            "organization_id": ORG_ID,
            "contact_type": ContactType.OWNER.value,
            "phones": [],
            "emails": [],
        }
    )

    assert inserted["id"] == "c1"
    query, _ = _sql_args(conn.fetchrow)
    assert "INSERT INTO contacts" in query


@pytest.mark.asyncio
async def test_insert_contact_missing_organization_id():
    """insert_contact requires organization_id."""
    repo = ContactsRepository(db_connection=_async_mock_conn())

    with pytest.raises(ValueError, match="organization_id"):
        await repo.insert_contact({"contact_type": ContactType.OWNER.value})


@pytest.mark.asyncio
async def test_filter_contact_ids_empty():
    """filter_contact_ids_in_organization returns empty set for empty input."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    result = await repo.filter_contact_ids_in_organization(
        organization_id=ORG_ID,
        contact_ids=[],
    )

    assert result == set()
    conn.fetch.assert_not_called()
