"""Unit tests for ContactsService contact overview."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext


def _user_context() -> UserContext:
    """Build an admin user context for contacts tests."""
    return UserContext(
        user_id="admin-1",
        email="admin@example.com",
        organization_id="org-1",
    )


class _FakeContactsRepo:
    """In-memory fake for ContactsRepository."""

    def __init__(self):
        self.overview_result = {
            "total": 26,
            "owners": 16,
            "tenants": 2,
            "vendors": 8,
        }
        self.last_kwargs: dict[str, Any] | None = None

    async def get_contact_overview(self, **kwargs):
        """Return configured overview and record call kwargs."""
        self.last_kwargs = kwargs
        return self.overview_result


def _service(*, contacts_repo: _FakeContactsRepo | None = None) -> ContactsService:
    """Build ContactsService with a fake contacts repository."""
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.contacts_repo = contacts_repo or _FakeContactsRepo()
    return svc


@pytest.mark.asyncio
async def test_get_contact_overview_forwards_org_and_status():
    """Service passes organization_id from user context and forwards status."""
    contacts_repo = _FakeContactsRepo()
    svc = _service(contacts_repo=contacts_repo)

    result = await svc.get_contact_overview(status=ClientStatus.ACTIVE.value)

    assert result == contacts_repo.overview_result
    assert contacts_repo.last_kwargs == {
        "organization_id": "org-1",
        "status": ClientStatus.ACTIVE.value,
    }


@pytest.mark.asyncio
async def test_get_contact_overview_default_status():
    """Service forwards None status for the All tab."""
    contacts_repo = _FakeContactsRepo()
    svc = _service(contacts_repo=contacts_repo)

    result = await svc.get_contact_overview(status=None)

    assert result["total"] == 26
    assert contacts_repo.last_kwargs == {
        "organization_id": "org-1",
        "status": None,
    }
