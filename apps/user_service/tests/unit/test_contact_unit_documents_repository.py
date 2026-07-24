"""Unit tests for ContactUnitDocumentsRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.contact_unit_documents_repository import (
    ContactUnitDocumentsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_UNIT_ID = "660e8400-e29b-41d4-a716-446655440001"
DOC_ID = "770e8400-e29b-41d4-a716-446655440002"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_result="DELETE 1"):
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result

    async def fetch(self, query, *args):
        del query, args
        return self.rows

    async def fetchrow(self, query, *args):
        del query, args
        return self.row

    async def execute(self, query, *args):
        del query, args
        return self.execute_result


@pytest.mark.asyncio
async def test_list_by_contact_unit():
    """List returns document rows for one contact_units link."""
    conn = _FakeConn(rows=[{"id": DOC_ID, "document_type": "lease"}])
    repo = ContactUnitDocumentsRepository(db_connection=conn)

    rows = await repo.list_by_contact_unit(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
    )

    assert rows[0]["document_type"] == "lease"


@pytest.mark.asyncio
async def test_insert_document():
    """Insert returns the created document row."""
    conn = _FakeConn(row={"id": DOC_ID, "file_path": "org/lease.pdf"})
    repo = ContactUnitDocumentsRepository(db_connection=conn)

    row = await repo.insert_document(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        document_type="lease",
        file_path="org/lease.pdf",
        file_name="lease.pdf",
        uploaded_by_user_id="user-1",
    )

    assert row["file_path"] == "org/lease.pdf"


@pytest.mark.asyncio
async def test_delete_document():
    """Delete returns True when one row is removed."""
    conn = _FakeConn(execute_result="DELETE 1")
    repo = ContactUnitDocumentsRepository(db_connection=conn)

    deleted = await repo.delete_document(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        document_id=DOC_ID,
    )

    assert deleted is True


@pytest.mark.asyncio
async def test_get_document():
    """Get returns one document row when present."""
    conn = _FakeConn(row={"id": DOC_ID, "document_type": "tax_receipt"})
    repo = ContactUnitDocumentsRepository(db_connection=conn)

    row = await repo.get_document(
        organization_id=ORG_ID,
        contact_unit_id=CONTACT_UNIT_ID,
        document_id=DOC_ID,
    )

    assert row is not None
    assert row["document_type"] == "tax_receipt"

    conn.row = None
    assert (
        await repo.get_document(
            organization_id=ORG_ID,
            contact_unit_id=CONTACT_UNIT_ID,
            document_id=DOC_ID,
        )
        is None
    )
