"""Unit tests for ContactsService company inference on contact create."""

from __future__ import annotations

from typing import Any

import pytest

from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "org-1"


def _ctx() -> UserContext:
    """Build a reusable UserContext for tests."""
    return UserContext(
        user_id="33333333-3333-3333-3333-333333333333",
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


class _FakeContactsRepo:
    """Fake ContactsRepository for testing."""

    def __init__(self) -> None:
        """Initialize the fake contacts repository."""
        self.calls: dict[str, Any] = {}
        self.existing_contact_id_by_email: str | None = None
        self.create_result: dict[str, Any] = {
            "contact_id": "c-1",
            "company_id": "co-1",
            "contact": {},
        }

    async def get_contact_id_by_email(self, *, organization_id: str, email: str) -> str | None:
        """Return existing contact id by email (fake)."""
        self.calls["get_contact_id_by_email"] = (organization_id, email)
        return self.existing_contact_id_by_email

    async def create_contact_with_optional_company_link(
        self,
        *,
        organization_id: str,
        contact_data: dict[str, Any],
        company_id: str | None,
        company_data: dict[str, Any] | None,
        company_addresses: list[dict[str, Any]] | None,
        make_primary: bool,
    ) -> dict[str, Any]:
        """Capture create call parameters (fake)."""
        self.calls["create_contact_with_optional_company_link"] = {
            "organization_id": organization_id,
            "contact_data": contact_data,
            "company_id": company_id,
            "company_data": company_data,
            "company_addresses": company_addresses,
            "make_primary": make_primary,
        }
        return dict(self.create_result)

    async def create_contact_addresses(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Capture addresses create call (fake)."""
        self.calls["create_contact_addresses"] = rows
        return []


class _FakeCompaniesRepo:
    """Fake CompaniesRepository for testing."""

    def __init__(self) -> None:
        """Initialize the fake companies repository."""
        self.calls: dict[str, Any] = {}
        self.by_name: dict[str, str] = {}

    async def get_company_ids_by_names(
        self,
        *,
        organization_id: str,
        names: list[str],
    ) -> dict[str, str]:
        """Return company ids keyed by normalized name (fake)."""
        self.calls["get_company_ids_by_names"] = (organization_id, names)
        # The real repo returns a dict keyed by normalized lowercase name.
        return dict(self.by_name)


class _FakeOrgRepo:
    """Fake OrganizationRepository for testing."""

    async def get_organization_by_id(self, org_id: str) -> dict[str, Any] | None:
        """Get an organization by ID."""
        return {"id": org_id, "name": "Test Org"}


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("rohit@appscrip.co", "appscrip"),
        ("Rohit <rohit@appscrip.co>", "appscrip"),
        ("rohit@sub.appscrip.co", "appscrip"),
        ("rohit@gmail.com", None),
        ("rohit@zoho.com", None),
        ("", None),
        (None, None),
    ],
)
def test_infer_company_name_from_email(email: str | None, expected: str | None) -> None:
    """Test company name inference from email."""
    assert ContactsService._infer_company_name_from_email(email) == expected


@pytest.mark.asyncio
async def test_create_contact_links_existing_company() -> None:
    """Test creating a contact links to an existing company by inferred name."""
    service = ContactsService(db_connection=object(), user_context=_ctx())
    service.contacts_repo = _FakeContactsRepo()
    service.companies_repo = _FakeCompaniesRepo()
    service.org_repo = _FakeOrgRepo()
    service.companies_repo.by_name = {"appscrip": "co-existing"}

    async def _fake_provision_identity(**_kwargs: Any) -> tuple[str, str | None, str | None]:
        return ("u-1", None, None)

    async def _fake_validate_custom_fields_for_create(_payload: Any) -> list[dict[str, Any]]:
        return []

    service._provision_identity = _fake_provision_identity  # type: ignore[assignment]
    service._validate_custom_fields_for_create = (  # type: ignore[assignment]
        _fake_validate_custom_fields_for_create
    )

    body = CreateContactRequest(email="rohitmarthak@appscrip.co")
    _ = await service.create_contact(body)

    captured = service.contacts_repo.calls["create_contact_with_optional_company_link"]
    assert captured["company_id"] == "co-existing"
    assert captured["company_data"] is None


@pytest.mark.asyncio
async def test_create_contact_creates_company_when_missing() -> None:
    """Test creating a contact creates a company when inferred name is not found."""
    service = ContactsService(db_connection=object(), user_context=_ctx())
    service.contacts_repo = _FakeContactsRepo()
    service.companies_repo = _FakeCompaniesRepo()
    service.org_repo = _FakeOrgRepo()
    service.companies_repo.by_name = {}

    async def _fake_provision_identity(**_kwargs: Any) -> tuple[str, str | None, str | None]:
        return ("u-1", None, None)

    async def _fake_validate_custom_fields_for_create(_payload: Any) -> list[dict[str, Any]]:
        return []

    service._provision_identity = _fake_provision_identity  # type: ignore[assignment]
    service._validate_custom_fields_for_create = (  # type: ignore[assignment]
        _fake_validate_custom_fields_for_create
    )

    body = CreateContactRequest(email="rohit@appscrip.co")
    _ = await service.create_contact(body)

    captured = service.contacts_repo.calls["create_contact_with_optional_company_link"]
    assert captured["company_id"] is None
    assert isinstance(captured["company_data"], dict)
    assert captured["company_data"]["name"] == "appscrip"
