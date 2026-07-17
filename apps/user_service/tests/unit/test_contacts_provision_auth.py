"""Unit tests for idempotent contact auth provisioning."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException

ORG_ID = "11111111-1111-1111-1111-111111111111"
CONTACT_ID = "22222222-2222-2222-2222-222222222222"


def _ctx() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(user_id=None, email=None, organization_id=ORG_ID)


class _FakeContactsRepo:
    """In-memory contacts repository stub for provision-auth tests."""

    def __init__(self, contact: dict[str, Any]) -> None:
        self.contact = dict(contact)
        self.update_calls: list[dict[str, Any]] = []

    async def get_contact_for_update(
        self,
        *,
        contact_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Return the seeded contact when ids match."""
        if contact_id == CONTACT_ID and organization_id == ORG_ID:
            return dict(self.contact)
        return None

    async def update_contact(
        self,
        *,
        contact_id: str,  # pylint: disable=unused-argument
        organization_id: str,  # pylint: disable=unused-argument
        update_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply update_data to the in-memory contact."""
        self.update_calls.append(update_data)
        self.contact.update(update_data)
        return dict(self.contact)


@pytest.mark.asyncio
async def test_provision_auth_skips_when_user_id_set() -> None:
    """Skip provisioning when the contact already has a linked auth user."""
    repo = _FakeContactsRepo(
        {
            "id": CONTACT_ID,
            "user_id": "auth-user-1",
            "isometrik_user_id": "iso-1",
            "phones": [{"is_primary": True, "phone_isd_code": "+91", "phone_number": "9876543210"}],
            "emails": [],
        }
    )
    service = ContactsService(db_connection=object(), user_context=_ctx())
    service.contacts_repo = repo  # type: ignore[assignment]

    result = await service.provision_auth_for_existing_contact(
        contact_id=CONTACT_ID,
        password="Secret@123",
    )

    assert result["user_id"] == "auth-user-1"
    assert not repo.update_calls


@pytest.mark.asyncio
async def test_isometrik_reuse_returns_existing_id() -> None:
    """Return a stored Isometrik user id without calling the external API."""
    service = ContactsService(db_connection=object(), user_context=_ctx())

    result = await service._create_or_reuse_isometrik_user(
        contact_id=CONTACT_ID,
        isometrik_payload={"user_id": CONTACT_ID},
        isometrik_credentials={"userSecret": "s"},
        existing_isometrik_user_id="iso-existing",
    )

    assert result == "iso-existing"


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contacts_service.login_to_isometrik",
    new_callable=AsyncMock,
)
@patch(
    "apps.user_service.app.services.contacts_service.create_isometrik_user",
    new_callable=AsyncMock,
)
async def test_isometrik_reuse_recovers_on_conflict(
    mock_create: AsyncMock,
    mock_login: AsyncMock,
) -> None:
    """Reuse an existing Isometrik user when create returns conflict."""
    mock_create.side_effect = ConflictException(message_key="Conflict")
    mock_login.return_value = {"userId": "iso-from-login"}

    service = ContactsService(db_connection=object(), user_context=_ctx())
    result = await service._create_or_reuse_isometrik_user(
        contact_id=CONTACT_ID,
        isometrik_payload={"user_id": CONTACT_ID},
        isometrik_credentials={"userSecret": "s"},
    )

    assert result == "iso-from-login"
    mock_login.assert_awaited_once()


@pytest.mark.asyncio
@patch.object(ContactsService, "_provision_contact_auth_identity", new_callable=AsyncMock)
async def test_provision_passes_existing_isometrik_id(
    mock_provision: AsyncMock,
) -> None:
    """Forward stored isometrik_user_id into auth identity provisioning."""
    mock_provision.return_value = ("auth-user-2", "iso-2", None)
    repo = _FakeContactsRepo(
        {
            "id": CONTACT_ID,
            "user_id": None,
            "isometrik_user_id": "iso-existing",
            "phones": [{"is_primary": True, "phone_isd_code": "+91", "phone_number": "9876543210"}],
            "emails": [],
        }
    )
    service = ContactsService(
        db_connection=object(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )
    service.contacts_repo = repo  # type: ignore[assignment]

    await service.provision_auth_for_existing_contact(
        contact_id=CONTACT_ID,
        password="Secret@123",
    )

    mock_provision.assert_awaited_once()
    assert mock_provision.await_args.kwargs["existing_isometrik_user_id"] == "iso-existing"
