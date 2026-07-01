"""Unit tests for ContactsService.create_contact."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.enums import ContactType
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException
from libs.shared_utils.status_codes import CustomStatusCode


def _user_context() -> UserContext:
    """Build a minimal user context for contact service tests."""
    return UserContext(
        user_id="user-1",
        email="admin@example.com",
        organization_id="org-1",
    )


def _minimal_body() -> CreateContactRequest:
    """Build a minimal valid create-contact request."""
    return CreateContactRequest(
        emails=[{"email": "jane@example.com", "is_primary": True}],
        contact_type=ContactType.OWNER,
        first_name="Jane",
    )


@pytest.mark.asyncio
async def test_create_contact_inserts_full_row():
    """Create provisions auth + Isometrik and persists all mapped fields."""
    service = ContactsService(db_connection=MagicMock(), user_context=_user_context())
    service.contacts_repo.get_contact_id_by_email = AsyncMock(return_value=None)
    service._validate_custom_fields = AsyncMock(return_value=[])
    service._provision_contact_auth_identity = AsyncMock(
        return_value=("auth-user-1", "isometrik-1", "temp-pass")
    )
    service.org_repo.get_organization_by_id = AsyncMock(return_value={"name": "Acme"})
    service.contacts_repo.insert_contact = AsyncMock(
        return_value={
            "id": "new-contact-id",
            "organization_id": "org-1",
            "user_id": "auth-user-1",
            "isometrik_user_id": "isometrik-1",
            "status": "active",
            "contact_type": "Owner",
            "emails": [
                {"email": "jane@example.com", "is_primary": True},
                {"email": "jane.work@example.com", "is_primary": False},
            ],
            "phones": [],
            "tags": [],
            "created_at": None,
            "updated_at": None,
        }
    )

    body = CreateContactRequest(
        emails=[
            {"email": "jane@example.com", "is_primary": True},
            {"email": "jane.work@example.com", "is_primary": False},
        ],
        contact_type=ContactType.OWNER,
        first_name="Jane",
    )

    with patch(
        "apps.user_service.app.services.contacts_service.send_client_creation_email"
    ) as send_email:
        result = await service.create_contact(body)

    assert result["contact_id"]
    assert result["old_data"] is None
    assert result["new_data"]["email"] == "jane@example.com"
    service._provision_contact_auth_identity.assert_awaited_once()
    service.contacts_repo.get_contact_id_by_email.assert_awaited_once_with(
        organization_id="org-1",
        email="jane@example.com",
    )
    inserted = service.contacts_repo.insert_contact.await_args.args[0]
    assert inserted["contact_type"] == "Owner"
    assert inserted["emails"] == [
        {"email": "jane@example.com", "is_primary": True},
        {"email": "jane.work@example.com", "is_primary": False},
    ]
    assert inserted["custom_fields"] == []
    assert inserted["user_id"] == "auth-user-1"
    assert inserted["isometrik_user_id"] == "isometrik-1"
    send_email.assert_called_once()


@pytest.mark.asyncio
async def test_create_contact_duplicate_email_conflict():
    """Duplicate primary email in org raises ConflictException."""
    service = ContactsService(db_connection=MagicMock(), user_context=_user_context())
    service.contacts_repo.get_contact_id_by_email = AsyncMock(return_value="existing-id")
    service.contacts_repo.insert_contact = AsyncMock()
    service._validate_custom_fields = AsyncMock(return_value=[])
    service._provision_contact_auth_identity = AsyncMock()

    with pytest.raises(ConflictException) as exc_info:
        await service.create_contact(_minimal_body())

    assert exc_info.value.custom_code == CustomStatusCode.CONFLICT
    service.contacts_repo.insert_contact.assert_not_called()
    service._provision_contact_auth_identity.assert_not_called()
