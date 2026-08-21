"""Unit tests for ContactRolesRepository."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.contact_roles_repository import (
    ContactRolesRepository,
)
from apps.user_service.app.schemas.enums import ContactType

ORG = "11111111-1111-1111-1111-111111111111"
CONTACT = "22222222-2222-2222-2222-222222222222"
UNIT = "33333333-3333-3333-3333-333333333333"
PROJECT = "44444444-4444-4444-4444-444444444444"


class _FakeConn:
    def __init__(self, *, rows=None, row=None, val=0):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val


def _role_row(**overrides) -> dict:
    base = {
        "id": "role-1",
        "organization_id": ORG,
        "contact_id": CONTACT,
        "role_type": ContactType.OWNER.value,
        "status": "active",
        "unit_id": UNIT,
        "project_id": PROJECT,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_insert_role():
    conn = _FakeConn(row=_role_row())
    repo = ContactRolesRepository(db_connection=conn)
    role = await repo.insert_role(
        organization_id=ORG,
        contact_id=CONTACT,
        role_type=ContactType.OWNER.value,
        project_id=PROJECT,
        unit_id=UNIT,
    )
    assert role["role_type"] == ContactType.OWNER.value
    assert "::public.contact_role_type" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_end_active_roles_for_unit_filters():
    conn = _FakeConn(rows=[_role_row()])
    repo = ContactRolesRepository(db_connection=conn)

    ended = await repo.end_active_roles_for_unit(
        organization_id=ORG,
        unit_id=UNIT,
        role_types=[ContactType.TENANT.value],
        contact_id=CONTACT,
    )
    assert ended[0]["contact_id"] == CONTACT
    query, _ = conn.fetch_calls[0]
    assert "role_type = ANY($5" in query
    assert "contact_id = $6::uuid" in query


@pytest.mark.asyncio
async def test_list_active_roles_for_contact():
    conn = _FakeConn(rows=[_role_row()])
    repo = ContactRolesRepository(db_connection=conn)
    roles = await repo.list_active_roles_for_contact(
        organization_id=ORG,
        contact_id=CONTACT,
    )
    assert roles[0]["id"] == "role-1"
    assert "ended_at IS NULL" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_convenience_insert_methods():
    conn = _FakeConn(row=_role_row())
    repo = ContactRolesRepository(db_connection=conn)

    owner = await repo.insert_owner_role(
        organization_id=ORG,
        contact_id=CONTACT,
        project_id=PROJECT,
        unit_id=UNIT,
        contact_unit_id="cu-1",
    )
    assert owner["role_type"] == ContactType.OWNER.value

    conn.row = _role_row(role_type=ContactType.TENANT.value)
    tenant = await repo.insert_tenant_role(
        organization_id=ORG,
        contact_id=CONTACT,
        project_id=PROJECT,
        unit_id=UNIT,
        contact_unit_id="cu-1",
    )
    assert tenant["role_type"] == ContactType.TENANT.value

    conn.row = _role_row(role_type=ContactType.FAMILY.value)
    family = await repo.insert_family_role(
        organization_id=ORG,
        contact_id=CONTACT,
        project_id=PROJECT,
        unit_id=UNIT,
        contact_unit_id="cu-1",
        relationship="spouse",
    )
    assert family["role_type"] == ContactType.FAMILY.value


@pytest.mark.asyncio
async def test_count_active_tenants_for_unit():
    conn = _FakeConn(val=1)
    repo = ContactRolesRepository(db_connection=conn)
    count = await repo.count_active_tenants_for_unit(
        organization_id=ORG,
        unit_id=UNIT,
    )
    assert count == 1
    assert "role_type = $3" in conn.fetchval_calls[0][0]
