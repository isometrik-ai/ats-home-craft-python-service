"""Unit tests for ContactUnitDocumentsService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ContactUnitDocumentType
from apps.user_service.app.schemas.project_inventory import CreateUnitDocumentRequest
from apps.user_service.app.services.contact_unit_documents_service import (
    ContactUnitDocumentsService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException


def _service() -> ContactUnitDocumentsService:
    """Build service with mocked repositories."""
    user_context = UserContext(
        user_id="user-1",
        email="admin@example.com",
        organization_id="org-1",
    )
    svc = ContactUnitDocumentsService(
        db_connection=MagicMock(),
        user_context=user_context,
    )
    svc.documents_repo = AsyncMock()
    svc.units_repo = AsyncMock()
    svc.units_repo.get_unit.return_value = {"id": "unit-1", "project_id": "proj-1"}
    svc.units_repo.get_unit_owner_contact.return_value = {
        "contact_unit_id": "cu-1",
        "contact_id": "contact-1",
    }
    return svc


@pytest.mark.asyncio
async def test_list_unit_documents_returns_rows():
    """List maps repository rows to API documents."""
    svc = _service()
    svc.documents_repo.list_by_contact_unit.return_value = [
        {
            "id": "doc-1",
            "contact_unit_id": "cu-1",
            "document_type": "lease",
            "file_path": "org/lease.jpg",
            "file_name": "Lease.jpg",
            "uploaded_by_user_id": "user-1",
            "created_at": "2026-07-23T12:00:00+00:00",
            "updated_at": "2026-07-23T12:00:00+00:00",
        }
    ]

    items = await svc.list_unit_documents(project_id="proj-1", unit_id="unit-1")

    assert items[0]["document_type"] == "lease"
    assert items[0]["file_name"] == "Lease.jpg"


@pytest.mark.asyncio
async def test_add_unit_document_inserts_for_owner():
    """Add delegates to repository with current owner contact_unit_id."""
    svc = _service()
    svc.documents_repo.insert_document.return_value = {
        "id": "doc-1",
        "contact_unit_id": "cu-1",
        "document_type": "tax_receipt",
        "file_path": "org/tax.jpg",
        "file_name": "Tax_Receipt.jpg",
        "uploaded_by_user_id": "user-1",
        "created_at": "2026-07-23T12:00:00+00:00",
        "updated_at": "2026-07-23T12:00:00+00:00",
    }
    body = CreateUnitDocumentRequest(
        document_type=ContactUnitDocumentType.TAX_RECEIPT,
        file_path="org/tax.jpg",
        file_name="Tax_Receipt.jpg",
    )

    result = await svc.add_unit_document(
        project_id="proj-1",
        unit_id="unit-1",
        body=body,
    )

    svc.documents_repo.insert_document.assert_awaited_once()
    assert result["document_type"] == "tax_receipt"


@pytest.mark.asyncio
async def test_add_unit_document_requires_owner():
    """Add fails when the unit has no owner allotment."""
    svc = _service()
    svc.units_repo.get_unit_owner_contact.return_value = None

    with pytest.raises(NotFoundException):
        await svc.add_unit_document(
            project_id="proj-1",
            unit_id="unit-1",
            body=CreateUnitDocumentRequest(
                document_type=ContactUnitDocumentType.LEASE,
                file_path="org/lease.jpg",
            ),
        )


@pytest.mark.asyncio
async def test_delete_unit_document():
    """Delete removes document for current owner allotment."""
    svc = _service()
    svc.documents_repo.delete_document.return_value = True

    await svc.delete_unit_document(
        project_id="proj-1",
        unit_id="unit-1",
        document_id="doc-1",
    )

    svc.documents_repo.delete_document.assert_awaited_once_with(
        organization_id="org-1",
        contact_unit_id="cu-1",
        document_id="doc-1",
    )


@pytest.mark.asyncio
async def test_delete_unit_document_not_found():
    """Delete raises when document id is missing."""
    svc = _service()
    svc.documents_repo.delete_document.return_value = False

    with pytest.raises(NotFoundException):
        await svc.delete_unit_document(
            project_id="proj-1",
            unit_id="unit-1",
            document_id="doc-missing",
        )
