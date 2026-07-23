"""Unit tests for EmailTemplateRepository with fake connection."""

from __future__ import annotations

import json

import pytest

from apps.user_service.app.db.repositories.email_template_repository import (
    EmailTemplateRepository,
)
from apps.user_service.app.schemas.enums import EmailTemplateStatus, EmailTemplateType

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
TEMPLATE_ID = "660e8400-e29b-41d4-a716-446655440001"


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
async def test_columns_expr():
    expr = EmailTemplateRepository._columns_expr()
    assert "organization_id" in expr
    assert "\n" not in expr


@pytest.mark.asyncio
async def test_insert_default_layout():
    conn = _FakeConn(row={"id": TEMPLATE_ID, "is_default": True})
    repo = EmailTemplateRepository(db_connection=conn)

    row = await repo.insert_default_layout(ORG_ID)

    assert row["is_default"] is True
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO email_templates" in query
    assert args[2] == EmailTemplateType.LAYOUT.value
    assert args[3] == EmailTemplateStatus.PUBLISHED.value


@pytest.mark.asyncio
async def test_create_and_list_templates():
    conn = _FakeConn(
        row={"id": TEMPLATE_ID, "name": "Welcome"},
        rows=[{"id": TEMPLATE_ID, "name": "Welcome"}],
    )
    repo = EmailTemplateRepository(db_connection=conn)

    created = await repo.create_template(
        {
            "organization_id": ORG_ID,
            "name": "Welcome",
            "template_type": EmailTemplateType.TRIGGER.value,
            "status": EmailTemplateStatus.DRAFT.value,
            "html_content": "<p>Hi</p>",
            "variables": [{"name": "first_name"}],
        }
    )
    assert created["name"] == "Welcome"
    _, args = conn.fetchrow_calls[0]
    assert json.loads(args[6]) == [{"name": "first_name"}]

    listed = await repo.list_templates(
        ORG_ID,
        template_type=EmailTemplateType.TRIGGER.value,
        status=EmailTemplateStatus.DRAFT.value,
    )
    assert len(listed) == 1
    list_query, list_args = conn.fetch_calls[0]
    assert "template_type = $2" in list_query
    assert "status = $3" in list_query
    assert list_args == (
        ORG_ID,
        EmailTemplateType.TRIGGER.value,
        EmailTemplateStatus.DRAFT.value,
    )


@pytest.mark.asyncio
async def test_get_default_layout_and_by_id():
    conn = _FakeConn(row={"id": TEMPLATE_ID})
    repo = EmailTemplateRepository(db_connection=conn)

    layout = await repo.get_default_layout(ORG_ID)
    assert layout["id"] == TEMPLATE_ID
    assert "is_default = TRUE" in conn.fetchrow_calls[0][0]

    template = await repo.get_template_by_id(ORG_ID, TEMPLATE_ID)
    assert template["id"] == TEMPLATE_ID

    conn.row = None
    assert await repo.get_template_by_id(ORG_ID, TEMPLATE_ID) is None


@pytest.mark.asyncio
async def test_update_and_delete_template():
    conn = _FakeConn(row={"id": TEMPLATE_ID, "subject": "Updated"})
    repo = EmailTemplateRepository(db_connection=conn)

    unchanged = await repo.update_template(ORG_ID, TEMPLATE_ID, {})
    assert unchanged["subject"] == "Updated"
    assert "id = $2::uuid" in conn.fetchrow_calls[0][0]

    conn.fetchrow_calls.clear()
    updated = await repo.update_template(
        ORG_ID,
        TEMPLATE_ID,
        {"subject": "Updated", "variables": ["a"]},
    )
    assert updated["subject"] == "Updated"
    assert "variables = $2::jsonb" in conn.fetchrow_calls[0][0]

    conn.row = {"id": TEMPLATE_ID}
    deleted = await repo.delete_template(ORG_ID, TEMPLATE_ID)
    assert deleted["id"] == TEMPLATE_ID
    assert "DELETE FROM email_templates" in conn.fetchrow_calls[1][0]
