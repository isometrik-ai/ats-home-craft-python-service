"""Unit tests for OrganizationRepository SQL with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.schemas.enums import (
    OrganizationStatus,
    PlanType,
    SuperadminOrganizationListStatus,
)
from libs.shared_utils.http_exceptions import NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


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
async def test_create_organization_inserts_columns():
    """Create builds INSERT from organization_data keys."""
    conn = _FakeConn(row={"id": ORG_ID, "name": "Acme"})
    repo = OrganizationRepository(db_connection=conn)

    result = await repo.create_organization(
        {"name": "Acme", "slug": "acme", "status": OrganizationStatus.ACTIVE.value}
    )

    assert result["id"] == ORG_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO organizations" in query
    assert "name" in query and "slug" in query
    assert args == ("Acme", "acme", OrganizationStatus.ACTIVE.value)


@pytest.mark.asyncio
async def test_get_orgs_list_search_filter():
    """List query adds ILIKE search across name/slug/domain."""
    conn = _FakeConn(rows=[{"id": ORG_ID, "name": "Acme", "member_count": 3}])
    repo = OrganizationRepository(db_connection=conn)

    rows = await repo.get_organizations_list(search="acme", status=None, limit=20, offset=0)

    assert len(rows) == 1
    query, args = conn.fetch_calls[0]
    assert "ILIKE" in query
    assert "%acme%" in args
    assert OrganizationStatus.DELETED.value in args


@pytest.mark.asyncio
async def test_get_orgs_count_excludes_deleted():
    """Count query excludes deleted organizations."""
    conn = _FakeConn(val=5)
    repo = OrganizationRepository(db_connection=conn)

    total = await repo.get_organizations_count(search=None, status=OrganizationStatus.ACTIVE.value)

    assert total == 5
    query, args = conn.fetchval_calls[0]
    assert "COUNT(*)" in query
    assert OrganizationStatus.DELETED.value in args
    assert OrganizationStatus.ACTIVE.value in args


@pytest.mark.asyncio
async def test_get_organization_by_id_found():
    """Get by id joins member count and scopes org id."""
    conn = _FakeConn(row={"id": ORG_ID, "name": "Acme", "member_count": 2})
    repo = OrganizationRepository(db_connection=conn)

    row = await repo.get_organization_by_id(ORG_ID)

    assert row["name"] == "Acme"
    query, args = conn.fetchrow_calls[0]
    assert "FROM organizations o" in query
    assert args[0] == ORG_ID


@pytest.mark.asyncio
async def test_check_org_exists_true():
    """Exists check uses NOT deleted predicate."""
    conn = _FakeConn(val=True)
    repo = OrganizationRepository(db_connection=conn)

    exists = await repo.check_organization_exists(ORG_ID)

    assert exists is True
    query, args = conn.fetchval_calls[0]
    assert "EXISTS" in query
    assert args == (ORG_ID, OrganizationStatus.DELETED.value)


@pytest.mark.asyncio
async def test_check_slug_unique_excludes_id():
    """Slug uniqueness optionally excludes current organization id."""
    conn = _FakeConn(val=True)
    repo = OrganizationRepository(db_connection=conn)

    unique = await repo.check_slug_unique("acme", exclude_id=ORG_ID)

    assert unique is True
    query, args = conn.fetchval_calls[0]
    assert "slug = $1" in query
    assert "id != $3" in query
    assert args == ("acme", OrganizationStatus.DELETED.value, ORG_ID)


@pytest.mark.asyncio
async def test_update_organization_sets_fields():
    """Update builds dynamic SET clause and returns row."""
    conn = _FakeConn(row={"id": ORG_ID, "name": "New Name"})
    repo = OrganizationRepository(db_connection=conn)

    updated = await repo.update_organization(ORG_ID, {"name": "New Name"})

    assert updated["name"] == "New Name"
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE organizations" in query
    assert "name = $1" in query
    assert args[0] == "New Name"
    assert args[-2] == ORG_ID


@pytest.mark.asyncio
async def test_update_organization_not_found_raises():
    """Missing organization on update raises NotFoundException."""
    conn = _FakeConn(row=None)
    repo = OrganizationRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.update_organization(ORG_ID, {"name": "Ghost"})


@pytest.mark.asyncio
async def test_get_user_active_orgs():
    """Active orgs query joins organization_members."""
    conn = _FakeConn(rows=[{"id": ORG_ID, "name": "Acme"}])
    repo = OrganizationRepository(db_connection=conn)

    orgs = await repo.get_user_active_organizations("user-1")

    assert orgs[0]["name"] == "Acme"
    query, args = conn.fetch_calls[0]
    assert "INNER JOIN organization_members" in query
    assert args[0] == "user-1"


def test_build_organization_conditions_search_and_status():
    """WHERE builder adds search and status predicates."""
    repo = OrganizationRepository(db_connection=None)
    where, params = repo._build_organization_conditions(  # pylint: disable=protected-access
        search=" acme ",
        status=OrganizationStatus.ACTIVE.value,
    )

    assert "o.status != $1" in where
    assert "ILIKE" in where
    assert "o.status = $" in where
    assert "%acme%" in params


def test_build_superadmin_list_where_pending_deletion():
    """Superadmin WHERE adds pending deletion exists clause."""
    repo = OrganizationRepository(db_connection=None)
    where, params = repo._build_superadmin_organization_list_where(  # pylint: disable=protected-access
        search=None,
        plan_type=None,
        list_status=SuperadminOrganizationListStatus.PENDING_DELETION.value,
    )

    assert "organization_delete_requests" in where
    assert OrganizationStatus.DELETED.value in params


def test_build_superadmin_list_where_suspended():
    """Suspended filter excludes pending deletion and matches status."""
    repo = OrganizationRepository(db_connection=None)
    where, params = repo._build_superadmin_organization_list_where(  # pylint: disable=protected-access
        search="owner",
        plan_type=PlanType.TRIAL.value,
        list_status=SuperadminOrganizationListStatus.SUSPENDED.value,
    )

    assert "NOT EXISTS" in where
    assert OrganizationStatus.SUSPENDED.value in params
    assert PlanType.TRIAL.value in params


@pytest.mark.asyncio
async def test_get_superadmin_organizations_list():
    """Superadmin list parses total and filters null sentinel rows."""
    rows = [
        {
            "_total_count": 2,
            "id": ORG_ID,
            "name": "Acme",
            "created_at": None,
            "member_count": 1,
            "owner_user_id": "u1",
            "owner_email": "a@b.com",
            "owner_first_name": "A",
            "owner_last_name": "B",
            "plan_type": "trial",
            "list_status": SuperadminOrganizationListStatus.ACTIVE.value,
        },
        {"_total_count": 2, "id": None},
    ]
    conn = _FakeConn(rows=rows)
    repo = OrganizationRepository(db_connection=conn)

    items, total = await repo.get_superadmin_organizations_list(
        search="acme",
        plan_type=PlanType.TRIAL.value,
        list_status=SuperadminOrganizationListStatus.ACTIVE.value,
        sort_field="name",
        sort_order="asc",
        limit=20,
        offset=0,
    )

    assert total == 2
    assert len(items) == 1
    assert items[0]["name"] == "Acme"
    query, _ = conn.fetch_calls[0]
    assert "WITH base AS" in query


@pytest.mark.asyncio
async def test_get_organization_with_owner_for_impersonation():
    """Impersonation query joins owner lateral."""
    conn = _FakeConn(row={"id": ORG_ID, "owner_email": "owner@example.com"})
    repo = OrganizationRepository(db_connection=conn)

    row = await repo.get_organization_with_owner_for_impersonation(ORG_ID)

    assert row["owner_email"] == "owner@example.com"
    query, args = conn.fetchrow_calls[0]
    assert "owner_user_id" in query
    assert args[0] == ORG_ID


@pytest.mark.asyncio
async def test_get_organization_details():
    """Details query filters by status."""
    conn = _FakeConn(row={"id": ORG_ID, "name": "Acme"})
    repo = OrganizationRepository(db_connection=conn)

    row = await repo.get_organization_details(ORG_ID, status=OrganizationStatus.ACTIVE)

    assert row["name"] == "Acme"
    query, args = conn.fetchrow_calls[0]
    assert args[1] == OrganizationStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_get_organization_context_by_isometrik_project_id():
    """Isometrik lookup returns org id and name tuple."""
    conn = _FakeConn(row={"id": ORG_ID, "name": "Acme"})
    repo = OrganizationRepository(db_connection=conn)

    result = await repo.get_organization_context_by_isometrik_project_id("proj-1")

    assert result == (ORG_ID, "Acme")


@pytest.mark.asyncio
async def test_get_organization_context_by_isometrik_project_id_missing():
    """Missing project returns None."""
    conn = _FakeConn(row=None)
    repo = OrganizationRepository(db_connection=conn)

    assert await repo.get_organization_context_by_isometrik_project_id("missing") is None


@pytest.mark.asyncio
async def test_update_subscription_users():
    """Subscription users increment uses jsonb_set."""
    conn = _FakeConn(row={"id": ORG_ID})
    repo = OrganizationRepository(db_connection=conn)

    await repo.update_subscription_users(ORG_ID, increment_by=2)

    query, args = conn.fetchrow_calls[0]
    assert "jsonb_set" in query
    assert args[1] == 2


@pytest.mark.asyncio
async def test_update_subscription_users_not_found():
    """Missing org on subscription update raises."""
    conn = _FakeConn(row=None)
    repo = OrganizationRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.update_subscription_users(ORG_ID)


@pytest.mark.asyncio
async def test_is_user_organization_owner():
    """Owner check returns True when row exists."""
    conn = _FakeConn(row={"?column?": 1})
    repo = OrganizationRepository(db_connection=conn)

    assert await repo.is_user_organization_owner(ORG_ID, "user-1") is True


@pytest.mark.asyncio
async def test_delete_organization_success():
    """Soft delete returns when row updated."""
    conn = _FakeConn(row={"id": ORG_ID})
    repo = OrganizationRepository(db_connection=conn)

    await repo.delete_organization(ORG_ID)

    query, args = conn.fetchrow_calls[0]
    assert "SET status = $2" in query
    assert args[1] == OrganizationStatus.DELETED.value


@pytest.mark.asyncio
async def test_delete_organization_not_found():
    """Missing org on delete raises NotFoundException."""
    conn = _FakeConn(row=None)
    repo = OrganizationRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.delete_organization(ORG_ID)


@pytest.mark.asyncio
async def test_update_organization_empty_data():
    """Empty update_data returns empty dict."""
    conn = _FakeConn()
    repo = OrganizationRepository(db_connection=conn)

    assert await repo.update_organization(ORG_ID, {}) == {}
    assert not conn.fetchrow_calls
