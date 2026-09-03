"""Success and failure tests for contact email sourced from contacts.emails JSONB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.schemas.common import Email
from apps.user_service.app.schemas.contacts import (
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import ClientStatus, ContactType
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.unit_list_serialization import (
    format_primary_contact_email,
)
from libs.shared_utils.http_exceptions import ConflictException, NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
OTHER_CONTACT_ID = "990e8400-e29b-41d4-a716-446655440099"
USER_ID = "770e8400-e29b-41d4-a716-446655440002"

CONTACT_PRIMARY_EMAIL = "nikunjresident@yopmail.com"
AUTH_ONLY_EMAIL = "auth-login@yopmail.com"


def _async_mock_conn(*, rows=None, row=None, val=None, execute_result=None):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


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


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


def _ctx() -> UserContext:
    """Build user context for contact email tests."""
    return UserContext(user_id="admin-1", email="admin@example.com", organization_id=ORG_ID)


class _EmailLookupContactsRepo:
    """Fake repo that resolves contact ids by normalized contact email."""

    def __init__(self, *, email_to_contact_id: dict[str, str] | None = None) -> None:
        self.email_to_contact_id = {
            key.strip().lower(): value for key, value in (email_to_contact_id or {}).items()
        }
        self.last_lookup_email: str | None = None

    async def get_contact_id_by_email(self, *, organization_id: str, email: str):
        """Return configured contact id for normalized email."""
        del organization_id
        self.last_lookup_email = (email or "").strip().lower()
        return self.email_to_contact_id.get(self.last_lookup_email)

    async def get_contact_ids_by_emails(self, *, organization_id: str, emails: list[str]):
        """Return configured contact ids keyed by normalized email."""
        del organization_id
        out: dict[str, str] = {}
        for raw_email in emails:
            email_norm = (raw_email or "").strip().lower()
            contact_id = self.email_to_contact_id.get(email_norm)
            if email_norm and contact_id:
                out[email_norm] = contact_id
        return out


class _ContactsServiceRepo(_EmailLookupContactsRepo):
    """Fake repo for list/detail/get-by-ids contact email flows."""

    def __init__(
        self,
        *,
        email_to_contact_id: dict[str, str] | None = None,
        contact_details: dict[str, Any] | None = None,
        list_contacts: list[dict[str, Any]] | None = None,
        by_ids: list[dict[str, Any]] | None = None,
        contact_for_update: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(email_to_contact_id=email_to_contact_id)
        self.contact_details = contact_details
        self.list_contacts_rows = list_contacts or []
        self.by_ids = by_ids or []
        self.contact_for_update = contact_for_update
        self.last_list_kwargs: dict[str, Any] | None = None
        self.last_by_ids_kwargs: dict[str, Any] | None = None

    async def get_contact_details(self, *, contact_id: str, organization_id: str):
        """Return configured contact details."""
        del contact_id, organization_id
        return self.contact_details

    async def list_contacts(self, **kwargs):
        """Return configured list rows."""
        self.last_list_kwargs = kwargs
        return self.list_contacts_rows, len(self.list_contacts_rows)

    async def get_contacts_by_ids(self, **kwargs):
        """Return configured minimal contact rows."""
        self.last_by_ids_kwargs = kwargs
        return self.by_ids

    async def get_contact_for_update(self, *, contact_id: str, organization_id: str):
        """Return configured contact row for update."""
        del contact_id, organization_id
        return self.contact_for_update

    async def update_contact(self, *, contact_id: str, organization_id: str, update_data: dict):
        """Record update attempt."""
        del contact_id, organization_id, update_data
        return None


def _service(*, contacts_repo: _ContactsServiceRepo | _EmailLookupContactsRepo) -> ContactsService:
    """Build ContactsService with a fake contacts repo."""
    svc = ContactsService(db_connection=MagicMock(), user_context=_ctx())
    svc.contacts_repo = contacts_repo  # type: ignore[assignment]
    return svc


def _contact_detail(**overrides) -> dict[str, Any]:
    """Build contact detail row with CRM email distinct from auth login email."""
    row = {
        "id": CONTACT_ID,
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "first_name": "Nikunj",
        "last_name": "Resident",
        "email": CONTACT_PRIMARY_EMAIL,
        "emails": [
            {"email": CONTACT_PRIMARY_EMAIL, "is_primary": True},
            {"email": "secondary@yopmail.com", "is_primary": False},
        ],
        "phones": [],
        "tags": [],
        "companies": [],
        "leads": [],
        "addresses": [],
        "notes": [],
        "created_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
        "created_by": USER_ID,
        "created_by_name": "Admin User",
    }
    row.update(overrides)
    return row


def _patch_custom_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch CustomFieldService validation used by list/update flows."""

    class _FakeCFS:
        """No-op CustomFieldService fake."""

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def validate_dropdown_filters_for_entity(self, entity_type, parsed_filters):
            del entity_type, parsed_filters

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.CustomFieldService",
        _FakeCFS,
    )


# ---------------------------------------------------------------------------
# Repository — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contact_id_by_email_success_matches_contact_emails_jsonb():
    """Lookup succeeds when email exists in contacts.emails JSONB."""
    conn = _FakeConn(row={"id": CONTACT_ID})
    repo = ContactsRepository(db_connection=conn)

    contact_id = await repo.get_contact_id_by_email(
        organization_id=ORG_ID,
        email=CONTACT_PRIMARY_EMAIL,
    )

    assert contact_id == CONTACT_ID
    query, args = conn.fetchrow_calls[0]
    assert "jsonb_array_elements(COALESCE(ct.emails" in query
    assert "auth.users" not in query
    assert args[2] == CONTACT_PRIMARY_EMAIL


@pytest.mark.asyncio
async def test_get_contact_ids_by_emails_success_returns_mapping():
    """Bulk lookup succeeds for emails stored on contacts."""
    conn = _FakeConn(
        rows=[
            {"email_norm": CONTACT_PRIMARY_EMAIL, "id": CONTACT_ID},
            {"email_norm": "secondary@yopmail.com", "id": OTHER_CONTACT_ID},
        ]
    )
    repo = ContactsRepository(db_connection=conn)

    mapping = await repo.get_contact_ids_by_emails(
        organization_id=ORG_ID,
        emails=[CONTACT_PRIMARY_EMAIL, "secondary@yopmail.com"],
    )

    assert mapping == {
        CONTACT_PRIMARY_EMAIL: CONTACT_ID,
        "secondary@yopmail.com": OTHER_CONTACT_ID,
    }


@pytest.mark.asyncio
async def test_list_contacts_success_returns_primary_contact_email_sql():
    """List query selects primary email from contacts.emails, not auth.users."""
    conn = _FakeConn(
        rows=[
            {
                "id": CONTACT_ID,
                "first_name": "Nikunj",
                "last_name": "Resident",
                "email": CONTACT_PRIMARY_EMAIL,
                "phones": [],
                "company_names": [],
                "tags": [],
            }
        ],
        val=1,
    )
    repo = ContactsRepository(db_connection=conn)

    rows, total = await repo.list_contacts(
        organization_id=ORG_ID,
        search=None,
        status=ClientStatus.ACTIVE.value,
        contact_type=None,
        page=1,
        page_size=20,
    )

    assert total == 1
    assert rows[0]["email"] == CONTACT_PRIMARY_EMAIL
    list_query, _ = conn.fetch_calls[0]
    assert "is_primary" in list_query
    assert "au.email" not in list_query


@pytest.mark.asyncio
async def test_get_contact_details_success_uses_primary_contact_email_sql():
    """Detail query selects primary email from contacts.emails JSONB."""
    conn = _async_mock_conn(row=_contact_detail())
    repo = ContactsRepository(db_connection=conn)

    details = await repo.get_contact_details(contact_id=CONTACT_ID, organization_id=ORG_ID)

    assert details["email"] == CONTACT_PRIMARY_EMAIL
    query, _ = _sql_args(conn.fetchrow)
    assert "jsonb_array_elements(COALESCE(ct.emails" in query
    assert "LEFT JOIN auth.users au" not in query


@pytest.mark.asyncio
async def test_get_contacts_by_ids_success_uses_primary_contact_email_sql():
    """Bulk minimal lookup selects primary contact email."""
    conn = _FakeConn(
        rows=[
            {
                "id": CONTACT_ID,
                "first_name": "Nikunj",
                "last_name": "Resident",
                "email": CONTACT_PRIMARY_EMAIL,
                "external_contact_id": "LUX-B4101",
            }
        ]
    )
    repo = ContactsRepository(db_connection=conn)

    rows = await repo.get_contacts_by_ids(
        organization_id=ORG_ID,
        contact_ids=[CONTACT_ID],
    )

    assert rows[0]["email"] == CONTACT_PRIMARY_EMAIL
    query, _ = conn.fetch_calls[0]
    assert "jsonb_array_elements(COALESCE(ct.emails" in query
    assert "LEFT JOIN auth.users au" not in query


# ---------------------------------------------------------------------------
# Repository — failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contact_id_by_email_failure_returns_none_when_not_found():
    """Lookup fails when email is absent from contacts.emails JSONB."""
    conn = _FakeConn(row=None)
    repo = ContactsRepository(db_connection=conn)

    contact_id = await repo.get_contact_id_by_email(
        organization_id=ORG_ID,
        email=AUTH_ONLY_EMAIL,
    )

    assert contact_id is None


@pytest.mark.asyncio
async def test_get_contact_id_by_email_failure_skips_db_for_blank_email():
    """Blank email short-circuits without querying the database."""
    conn = _async_mock_conn()
    repo = ContactsRepository(db_connection=conn)

    assert await repo.get_contact_id_by_email(organization_id=ORG_ID, email="   ") is None
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_contact_ids_by_emails_failure_returns_empty_when_no_rows():
    """Bulk lookup returns empty mapping when no contacts match."""
    conn = _FakeConn(rows=[])
    repo = ContactsRepository(db_connection=conn)

    mapping = await repo.get_contact_ids_by_emails(
        organization_id=ORG_ID,
        emails=[AUTH_ONLY_EMAIL],
    )

    assert mapping == {}


@pytest.mark.asyncio
async def test_list_contacts_search_failure_does_not_match_auth_email_only():
    """Search predicate no longer matches auth.users.email."""
    conn = _FakeConn(rows=[], val=0)
    repo = ContactsRepository(db_connection=conn)

    await repo.list_contacts(
        organization_id=ORG_ID,
        search=AUTH_ONLY_EMAIL,
        status=None,
        contact_type=None,
        page=1,
        page_size=20,
    )

    count_query, _ = conn.fetchval_calls[0]
    assert "jsonb_array_elements(COALESCE(ct.emails" in count_query
    assert "au.email" not in count_query


# ---------------------------------------------------------------------------
# Service — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_contacts_success_exposes_contact_email(monkeypatch):
    """List API returns contact email provided by repository."""
    _patch_custom_fields(monkeypatch)
    repo = _ContactsServiceRepo(
        list_contacts=[
            {
                "id": CONTACT_ID,
                "first_name": "Nikunj",
                "last_name": "Resident",
                "email": CONTACT_PRIMARY_EMAIL,
                "phones": [],
                "company_names": [],
                "tags": [],
                "created_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
            }
        ]
    )
    svc = _service(contacts_repo=repo)

    result = await svc.list_contacts(
        search=None,
        status=ClientStatus.ACTIVE.value,
        contact_type=None,
        dropdown_filters=None,
        page=1,
        page_size=20,
    )

    assert result["items"][0]["email"] == CONTACT_PRIMARY_EMAIL


@pytest.mark.asyncio
async def test_get_contact_details_success_exposes_contact_email():
    """Detail API returns CRM contact email even when auth login differs."""
    repo = _ContactsServiceRepo(contact_details=_contact_detail())
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_details(contact_id=CONTACT_ID)

    assert result["email"] == CONTACT_PRIMARY_EMAIL
    assert result["emails"][0]["email"] == CONTACT_PRIMARY_EMAIL


@pytest.mark.asyncio
async def test_get_contact_details_by_email_success_with_contact_email():
    """Email lookup succeeds when email exists on the contact record."""
    repo = _ContactsServiceRepo(
        email_to_contact_id={CONTACT_PRIMARY_EMAIL: CONTACT_ID},
        contact_details=_contact_detail(),
    )
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_details_by_email(email=CONTACT_PRIMARY_EMAIL)

    assert result["email"] == CONTACT_PRIMARY_EMAIL
    assert repo.last_lookup_email == CONTACT_PRIMARY_EMAIL


@pytest.mark.asyncio
async def test_get_contacts_by_ids_success_maps_contact_email():
    """Batch lookup maps repository contact email into API response."""
    repo = _ContactsServiceRepo(
        by_ids=[
            {
                "id": CONTACT_ID,
                "first_name": "Nikunj",
                "last_name": "Resident",
                "email": CONTACT_PRIMARY_EMAIL,
                "external_contact_id": "LUX-B4101",
            }
        ]
    )
    svc = _service(contacts_repo=repo)

    result = await svc.get_contacts_by_ids(contact_ids=[CONTACT_ID])

    assert result[0]["email"] == CONTACT_PRIMARY_EMAIL
    assert result[0]["name"] == "Nikunj Resident"


@pytest.mark.asyncio
async def test_assert_contact_email_unique_success_when_unused():
    """Uniqueness check passes when contact email is not taken."""
    repo = _EmailLookupContactsRepo(email_to_contact_id={})
    svc = _service(contacts_repo=repo)

    await svc._assert_contact_email_unique(  # pylint: disable=protected-access
        organization_id=ORG_ID,
        email=CONTACT_PRIMARY_EMAIL,
    )

    assert repo.last_lookup_email == CONTACT_PRIMARY_EMAIL


# ---------------------------------------------------------------------------
# Service — failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contact_details_by_email_failure_when_only_auth_email_exists():
    """Email lookup fails when only auth login email exists, not contact email."""
    repo = _EmailLookupContactsRepo(email_to_contact_id={})
    svc = _service(contacts_repo=repo)

    with pytest.raises(NotFoundException) as exc_info:
        await svc.get_contact_details_by_email(email=AUTH_ONLY_EMAIL)

    assert exc_info.value.message_key == "contacts.errors.contact_not_found"
    assert repo.last_lookup_email == AUTH_ONLY_EMAIL


@pytest.mark.asyncio
async def test_assert_contact_email_unique_failure_when_taken():
    """Uniqueness check rejects email already used by another contact."""
    repo = _EmailLookupContactsRepo(
        email_to_contact_id={CONTACT_PRIMARY_EMAIL: OTHER_CONTACT_ID},
    )
    svc = _service(contacts_repo=repo)

    with pytest.raises(ConflictException) as exc_info:
        await svc._assert_contact_email_unique(  # pylint: disable=protected-access
            organization_id=ORG_ID,
            email=CONTACT_PRIMARY_EMAIL,
        )

    assert exc_info.value.message_key == "contacts.errors.email_already_exists"


@pytest.mark.asyncio
async def test_create_contact_failure_when_contact_email_already_exists():
    """Create contact rejects duplicate CRM contact email before provisioning."""
    repo = _EmailLookupContactsRepo(
        email_to_contact_id={CONTACT_PRIMARY_EMAIL: OTHER_CONTACT_ID},
    )
    svc = _service(contacts_repo=repo)

    async def _passthrough_company_assoc(**kwargs):
        return (
            kwargs.get("company_id"),
            kwargs.get("company_data"),
            kwargs.get("company_addresses"),
            kwargs.get("make_primary"),
        )

    svc._apply_inferred_company_assoc_on_create = _passthrough_company_assoc  # type: ignore[method-assign]
    svc._validate_custom_fields_for_create = AsyncMock(return_value=[])  # type: ignore[method-assign]

    with pytest.raises(ConflictException) as exc_info:
        await svc.create_contact(
            CreateContactRequest(
                contact_type=ContactType.OWNER,
                first_name="Nikunj",
                last_name="Resident",
                email=CONTACT_PRIMARY_EMAIL,
            )
        )

    assert exc_info.value.message_key == "contacts.errors.email_already_exists"


@pytest.mark.asyncio
async def test_update_contact_failure_when_contact_email_taken_by_another(monkeypatch):
    """Update contact rejects primary email already used by another contact."""
    _patch_custom_fields(monkeypatch)
    repo = _ContactsServiceRepo(
        email_to_contact_id={CONTACT_PRIMARY_EMAIL: OTHER_CONTACT_ID},
        contact_for_update=_contact_detail(
            emails=[{"email": "old@yopmail.com", "is_primary": True}],
        ),
    )
    svc = _service(contacts_repo=repo)

    with pytest.raises(ConflictException) as exc_info:
        await svc.update_contact(
            contact_id=CONTACT_ID,
            body=UpdateContactRequest(
                emails=[Email(email=CONTACT_PRIMARY_EMAIL, is_primary=True)],
            ),
        )

    assert exc_info.value.message_key == "contacts.errors.email_already_exists"
    assert repo.last_list_kwargs is None


# ---------------------------------------------------------------------------
# Utility — success / failure
# ---------------------------------------------------------------------------


def test_format_primary_contact_email_success_prefers_primary():
    """Primary email helper returns the is_primary entry."""
    emails = [
        {"email": "secondary@yopmail.com", "is_primary": False},
        {"email": CONTACT_PRIMARY_EMAIL, "is_primary": True},
    ]
    assert format_primary_contact_email(emails) == CONTACT_PRIMARY_EMAIL


def test_format_primary_contact_email_success_falls_back_to_first():
    """Primary email helper falls back to first entry when none is primary."""
    emails = [{"email": CONTACT_PRIMARY_EMAIL}]
    assert format_primary_contact_email(emails) == CONTACT_PRIMARY_EMAIL


def test_format_primary_contact_email_failure_empty_list():
    """Primary email helper returns None for empty lists."""
    assert format_primary_contact_email([]) is None


def test_format_primary_contact_email_failure_invalid_payload():
    """Primary email helper returns None for non-list payloads."""
    assert format_primary_contact_email(None) is None
    assert format_primary_contact_email("not-a-list") is None
