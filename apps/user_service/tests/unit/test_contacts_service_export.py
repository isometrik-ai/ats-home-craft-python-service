"""Unit tests for ContactsService CSV export."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.contacts import ContactsExportQuery
from apps.user_service.app.schemas.enums import ClientStatus, ContactType
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext


def _service() -> ContactsService:
    return ContactsService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="admin@example.com",
            organization_id="org-123",
        ),
    )


def test_format_export_phone_prefers_primary() -> None:
    phones = [
        {"phone_number": "111", "phone_isd_code": "+1", "is_primary": False},
        {"phone_number": "222", "phone_isd_code": "+91", "is_primary": True},
    ]
    assert ContactsService._format_export_phone(phones) == ("222", "+91")


def test_join_export_list() -> None:
    assert ContactsService._join_export_list(["Owner", "Family"]) == "Owner;Family"
    assert ContactsService._join_export_list([]) == ""


@pytest.mark.asyncio
async def test_export_contacts_csv_writes_rows(monkeypatch) -> None:
    service = _service()
    fake_rows = {
        "items": [
            {
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "phones": [
                    {"phone_number": "9876543210", "phone_isd_code": "+91", "is_primary": True}
                ],
                "status": ClientStatus.ACTIVE.value,
                "role_types": ["Owner", "Family"],
                "company_names": ["Acme"],
            }
        ],
        "total": 1,
    }
    monkeypatch.setattr(
        ContactsService,
        "list_contacts",
        AsyncMock(return_value=fake_rows),
    )

    csv_text = await service.export_contacts_csv(
        query=ContactsExportQuery(
            status=ClientStatus.ACTIVE,
            contact_type=ContactType.OWNER,
            project_id="project-1",
        )
    )
    assert (
        "first_name,last_name,email,phone_number,phone_isd_code,status,role_types,company_names"
        in csv_text
    )
    assert "Jane,Doe,jane@example.com,9876543210,+91,active,Owner;Family,Acme" in csv_text
