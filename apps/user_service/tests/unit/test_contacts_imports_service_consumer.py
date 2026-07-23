"""Unit tests for ContactsImportService consumer/batch processing."""

from __future__ import annotations

import io
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.contacts_imports import ContactsImportEventPayload
from apps.user_service.app.schemas.enums import (
    ContactsImportEventAction,
    ContactsImportJobStatus,
    EntityType,
)
from apps.user_service.app.services.contacts_imports_service import (
    ContactsImportService,
    _ContactsImportTotals,
)
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
FILE_URL = "https://cdn.example.com/contacts.csv"
JOB_KEY = "imp_test"


# ---------------------------------------------------------------------------
# Shared fakes (extended from test_contacts_imports_service.py patterns)
# ---------------------------------------------------------------------------


class _FakeJobsRepo:
    """Configurable fake ImportJobsRepository."""

    configured_job: dict[str, Any] | None = None
    last_instance: "_FakeJobsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.set_status_ts_calls: list[dict[str, Any]] = []
        self.increment_calls: list[dict[str, Any]] = []
        self._job = _FakeJobsRepo.configured_job
        _FakeJobsRepo.last_instance = self

    async def get_job(self, *, job_id: str, organization_id: str) -> dict[str, Any] | None:
        del job_id, organization_id
        return self._job

    async def set_status_and_timestamps(self, **kwargs) -> None:
        self.set_status_ts_calls.append(kwargs)

    async def increment_counters(self, **kwargs) -> None:
        self.increment_calls.append(kwargs)


class _FakeRowsRepo:
    """Configurable fake ImportJobRowsRepository."""

    configured_items: list[dict[str, Any]] = []
    configured_total: int = 0
    last_instance: "_FakeRowsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.list_error_rows_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.mark_errors_calls: list[dict[str, Any]] = []
        self.mark_success_calls: list[dict[str, Any]] = []
        self._items = list(_FakeRowsRepo.configured_items)
        self._total = _FakeRowsRepo.configured_total
        _FakeRowsRepo.last_instance = self

    async def list_error_rows(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        self.list_error_rows_calls.append(kwargs)
        return self._items, self._total

    async def claim_rows_processing(self, **kwargs) -> dict[int, str]:
        self.claim_calls.append(kwargs)
        rows = kwargs.get("rows") or []
        return {int(row_number): "pending" for row_number, _ in rows}

    async def mark_errors_bulk(self, **kwargs) -> None:
        self.mark_errors_calls.append(kwargs)

    async def mark_success_bulk(self, **kwargs) -> None:
        self.mark_success_calls.append(kwargs)


class _FakeLogsRepo:
    """Configurable fake ImportJobLogsRepository."""

    last_instance: "_FakeLogsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.upsert_calls: list[dict[str, Any]] = []
        _FakeLogsRepo.last_instance = self

    async def upsert_payload(self, **kwargs) -> None:
        self.upsert_calls.append(kwargs)


class _FakeContactsRepo:
    """Extended ContactsRepository fake."""

    existing_emails: dict[str, str] = {}
    contact_ids_by_user: dict[str, str] = {}
    last_instance: "_FakeContactsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.create_contacts_calls: list[list[dict[str, Any]]] = []
        self.create_addresses_calls: list[list[dict[str, Any]]] = []
        self.delete_addresses_calls: list[str] = []
        self.soft_delete_calls: list[dict[str, Any]] = []
        _FakeContactsRepo.last_instance = self

    async def get_contact_ids_by_emails(self, **kwargs) -> dict[str, str]:
        del kwargs
        return dict(_FakeContactsRepo.existing_emails)

    async def create_contacts(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.create_contacts_calls.append(rows)
        return rows

    async def get_contact_ids_by_user_ids(self, **kwargs) -> dict[str, str]:
        del kwargs
        return dict(_FakeContactsRepo.contact_ids_by_user)

    async def create_contact_addresses(self, rows: list[dict[str, Any]]) -> None:
        self.create_addresses_calls.append(rows)

    async def delete_all_contact_addresses(self, *, contact_id: str) -> None:
        self.delete_addresses_calls.append(contact_id)

    async def soft_delete_contact(self, *, contact_id: str, organization_id: str) -> None:
        self.soft_delete_calls.append(
            {"contact_id": contact_id, "organization_id": organization_id}
        )


class _FakeCompaniesRepo:
    """Fake CompaniesRepository for company cache tests."""

    existing_by_name: dict[str, str] = {}
    created_rows: list[dict[str, Any]] = []
    last_instance: "_FakeCompaniesRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.get_ids_calls: list[dict[str, Any]] = []
        self.create_calls: list[list[dict[str, Any]]] = []
        _FakeCompaniesRepo.last_instance = self

    async def get_company_ids_by_names(self, **kwargs) -> dict[str, str]:
        self.get_ids_calls.append(kwargs)
        return dict(_FakeCompaniesRepo.existing_by_name)

    async def create_companies(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.create_calls.append(rows)
        if _FakeCompaniesRepo.created_rows:
            return list(_FakeCompaniesRepo.created_rows)
        return [{"id": f"co-{i}", "name": r["name"]} for i, r in enumerate(rows)]


class _FakeEntityListsRepo:
    """Fake EntityListsRepository."""

    existing_list_id: str | None = None
    create_raises_unique: bool = False
    last_instance: "_FakeEntityListsRepo | None" = None
    all_create_calls: list[dict[str, Any]] = []
    all_update_calls: list[dict[str, Any]] = []
    lookup_results: list[str | None] = []

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.get_id_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        _FakeEntityListsRepo.last_instance = self

    async def get_active_list_id_by_name(self, **kwargs) -> str | None:
        self.get_id_calls.append(kwargs)
        if _FakeEntityListsRepo.lookup_results:
            return _FakeEntityListsRepo.lookup_results.pop(0)
        return _FakeEntityListsRepo.existing_list_id

    async def create_list(self, **kwargs) -> dict[str, Any]:
        self.create_calls.append(kwargs)
        _FakeEntityListsRepo.all_create_calls.append(kwargs)
        if _FakeEntityListsRepo.create_raises_unique:
            raise UniqueViolationError("duplicate")
        return {"list": {"id": "list-new-1"}}

    async def update_list(self, **kwargs) -> None:
        self.update_calls.append(kwargs)
        _FakeEntityListsRepo.all_update_calls.append(kwargs)


class _FakeOrgRepo:
    """Minimal org lookup fake."""

    async def get_organization_by_id(self, organization_id: str) -> dict[str, str]:
        del organization_id
        return {"name": "Acme Corp"}


class _FakeContactsService:
    """ContactsService fake with provision + email hooks."""

    def __init__(self, **kwargs) -> None:
        del kwargs
        self.org_repo = _FakeOrgRepo()
        self.provision_calls: list[dict[str, Any]] = []
        self.email_calls: list[dict[str, Any]] = []

    async def _provision_contact_auth_identity(self, **kwargs) -> tuple[str, str, str | None]:
        self.provision_calls.append(kwargs)
        return ("user-1", "iso-1", "temp-pass")

    def _maybe_send_contact_creation_email(self, **kwargs) -> None:
        self.email_calls.append(kwargs)


def _reset_fakes() -> None:
    """Reset all configurable fake state."""
    _FakeJobsRepo.configured_job = None
    _FakeRowsRepo.configured_items = []
    _FakeRowsRepo.configured_total = 0
    _FakeContactsRepo.existing_emails = {}
    _FakeContactsRepo.contact_ids_by_user = {}
    _FakeCompaniesRepo.existing_by_name = {}
    _FakeCompaniesRepo.created_rows = []
    _FakeEntityListsRepo.existing_list_id = None
    _FakeEntityListsRepo.create_raises_unique = False
    _FakeEntityListsRepo.all_create_calls = []
    _FakeEntityListsRepo.all_update_calls = []
    _FakeEntityListsRepo.lookup_results = []


def _patch_repos_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch import repositories without replacing ContactsService."""
    _reset_fakes()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ImportJobsRepository",
        _FakeJobsRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ImportJobRowsRepository",
        _FakeRowsRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ImportJobLogsRepository",
        _FakeLogsRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsRepository",
        _FakeContactsRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.CompaniesRepository",
        _FakeCompaniesRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.EntityListsRepository",
        _FakeEntityListsRepo,
    )


def _patch_consumer_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch repositories and external deps for process_job_event tests."""
    _patch_repos_only(monkeypatch)

    async def _fake_supabase() -> MagicMock:
        return MagicMock()

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.get_supabase_service_client",
        _fake_supabase,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsService",
        _FakeContactsService,
    )


def _queued_job(**overrides: Any) -> dict[str, Any]:
    """Build a minimal queued import job row."""
    job = {
        "id": "internal-job-1",
        "job_key": JOB_KEY,
        "job_id": JOB_KEY,
        "status": ContactsImportJobStatus.QUEUED.value,
        "file_url": FILE_URL,
        "file_type": "csv",
        "schema_version": 1,
        "mapping": {"email": "Email", "first_name": "First"},
        "options": {"has_header": True},
    }
    job.update(overrides)
    return job


def _import_event(**overrides: Any) -> ContactsImportEventPayload:
    """Build a contacts import Kafka event payload."""
    payload = {
        "event_id": "evt-1",
        "schema_version": 1,
        "job_key": JOB_KEY,
        "organization_id": ORG_ID,
        "file_url": FILE_URL,
        "requested_by": "user-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "action": ContactsImportEventAction.CREATE,
    }
    payload.update(overrides)
    return ContactsImportEventPayload.model_validate(payload)


def _contact_model(email: str, **extra: Any) -> CreateContactRequest:
    """Build a minimal valid CreateContactRequest."""
    body: dict[str, Any] = {"email": email}
    body.update(extra)
    return CreateContactRequest.model_validate(body)


def _claimed_row(
    row_number: int,
    email: str,
    *,
    company_name: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a claimed batch row item."""
    return {
        "row_number": row_number,
        "raw_row": {"email": email},
        "error": error,
        "contact_model": None if error else _contact_model(email),
        "company_name": company_name,
    }


# ---------------------------------------------------------------------------
# Static helper coverage
# ---------------------------------------------------------------------------


def test_resolve_org_name_from_organization() -> None:
    """Organization dict name is preferred."""
    name = ContactsImportService._resolve_org_name(organization={"name": "Widget Co"})
    assert name == "Widget Co"


def test_resolve_org_name_fallback_settings() -> None:
    """Missing org name falls back to shared settings company_name."""
    with patch(
        "apps.user_service.app.config.app_settings.shared_settings",
        MagicMock(company_name="Default Org"),
    ):
        name = ContactsImportService._resolve_org_name(organization=None)
    assert name == "Default Org"


def test_parse_portal_access_truthy() -> None:
    """Truthy CSV values parse as True."""
    assert ContactsImportService._parse_portal_access("true") is True
    assert ContactsImportService._parse_portal_access("1") is True
    assert ContactsImportService._parse_portal_access("YES") is True


def test_parse_portal_access_falsy() -> None:
    """Other values parse as False."""
    assert ContactsImportService._parse_portal_access("false") is False
    assert ContactsImportService._parse_portal_access(None) is False


def test_parse_json_object_dict() -> None:
    """JSON object string parses to dict."""
    result = ContactsImportService._parse_json_object('{"k": "v"}')
    assert result == {"k": "v"}


def test_parse_json_object_non_dict() -> None:
    """Non-dict JSON returns empty dict."""
    assert ContactsImportService._parse_json_object("[1, 2]") == {}


def test_validate_row_body_valid() -> None:
    """Valid body dict yields contact model work item."""
    item = ContactsImportService._validate_row_body(
        row_number=1,
        canonical={"email": "a@x.com"},
        company_name="Co",
        body_dict={"email": "a@x.com"},
    )
    assert item["error"] is None
    assert item["contact_model"] is not None
    assert item["company_name"] == "Co"


def test_validate_row_body_invalid() -> None:
    """Invalid body dict yields validation error work item."""
    item = ContactsImportService._validate_row_body(
        row_number=2,
        canonical={},
        company_name=None,
        body_dict={"email": "a@x.com", "unexpected_field": "nope"},
    )
    assert item["error"]["code"] == "validation_error"
    assert item["contact_model"] is None


def test_build_contact_body_dict_full() -> None:
    """Canonical row maps into CreateContactRequest body dict."""
    svc = ContactsImportService(db_connection=MagicMock())
    body = svc._build_contact_body_dict(
        canonical={
            "email": "jane@example.com",
            "first_name": "Jane",
            "portal_access": "true",
            "date_of_birth": "1990-01-01",
            "tags_json": '["vip"]',
            "lead_json": '{"stage_id": "550e8400-e29b-41d4-a716-446655440001"}',
        },
        email_raw="jane@example.com",
        phones=[{"phone_number": "555", "phone_isd_code": "+1", "is_primary": True}],
    )
    assert body["email"] == "jane@example.com"
    assert body["portal_access"] is True
    assert body["tags"] == ["vip"]
    assert body["lead"]["stage_id"] == "550e8400-e29b-41d4-a716-446655440001"


def test_build_csv_reader_with_header() -> None:
    """Headered CSV uses DictReader default fieldnames."""
    f = io.StringIO("Email\na@x.com\n")
    reader = ContactsImportService._build_csv_reader(f=f, has_header=True, mapping={})
    row = next(reader)
    assert row["Email"] == "a@x.com"


def test_build_csv_reader_without_header() -> None:
    """Headerless CSV uses mapping values as fieldnames."""
    f = io.StringIO("a@x.com,Jane\n")
    reader = ContactsImportService._build_csv_reader(
        f=f,
        has_header=False,
        mapping={"email": "Email", "first_name": "First"},
    )
    row = next(reader)
    assert row["Email"] == "a@x.com"
    assert row["First"] == "Jane"


def test_validate_file_url_private_ip_branch() -> None:
    """Private IP literal branch executes (ValueError is swallowed by broad except)."""
    ContactsImportService._validate_file_url("https://127.0.0.1/file.csv")


def test_primary_phone_empty_number() -> None:
    """Primary candidate with blank phone_number returns None."""
    phones = [{"phone_number": "  ", "is_primary": True}]
    assert ContactsImportService._primary_phone_fields(phones) is None


def test_attach_identity_and_company() -> None:
    """Identity and company_id are attached to provisioned rows."""
    rows, nums = ContactsImportService._attach_identity_and_company_to_rows(
        provisioned_claimed=[
            {"row_number": 1, "company_name": "Acme"},
            {"row_number": 2, "company_name": "Unknown"},
        ],
        identity_results={1: ("u1", "i1", None), 2: ("u2", "i2", "pw")},
        company_cache={"acme": "co-1"},
    )
    assert rows[0]["identity"] == ("u1", "i1", None)
    assert rows[0]["company_id"] == "co-1"
    assert rows[1].get("company_id") is None
    assert nums == [1, 2]


# ---------------------------------------------------------------------------
# list_job_error_rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_job_error_rows_delegates(monkeypatch) -> None:
    """List error rows forwards pagination to rows repo."""
    _patch_consumer_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    _FakeRowsRepo.configured_items = [{"row_number": 3, "status": "error"}]
    _FakeRowsRepo.configured_total = 1
    svc = ContactsImportService(db_connection=MagicMock())

    items, total = await svc.list_job_error_rows(
        job_id=JOB_KEY,
        organization_id=ORG_ID,
        page=1,
        page_size=10,
    )

    assert total == 1
    assert items[0]["row_number"] == 3
    rows_repo = _FakeRowsRepo.last_instance
    assert rows_repo is not None
    assert rows_repo.list_error_rows_calls[0]["job_id"] == "internal-job-1"


@pytest.mark.asyncio
async def test_list_job_error_rows_no_job(monkeypatch) -> None:
    """Missing job returns empty page without rows lookup."""
    _patch_consumer_deps(monkeypatch)
    _FakeJobsRepo.configured_job = None
    svc = ContactsImportService(db_connection=MagicMock())

    items, total = await svc.list_job_error_rows(job_id=JOB_KEY, organization_id=ORG_ID)

    assert items == []
    assert total == 0
    rows_repo = _FakeRowsRepo.last_instance
    assert rows_repo is not None
    assert not rows_repo.list_error_rows_calls


# ---------------------------------------------------------------------------
# Duplicate email helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_duplicate_email_errors(monkeypatch) -> None:
    """Existing DB emails are marked as duplicate errors."""
    _patch_consumer_deps(monkeypatch)
    _FakeContactsRepo.existing_emails = {"exists@example.com": "client-99"}
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    claimed = [
        _claimed_row(1, "exists@example.com"),
        _claimed_row(2, "new@example.com"),
    ]
    remaining = await svc._apply_duplicate_email_errors(
        organization_id=ORG_ID,
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        claimed=claimed,
        totals=totals,
    )

    assert len(remaining) == 1
    assert remaining[0]["row_number"] == 2
    assert totals.errors_total == 1
    assert rows_repo.mark_errors_calls[0]["errors"][0][1] == "email_already_exists"


@pytest.mark.asyncio
async def test_apply_in_file_duplicate_email_errors_async(monkeypatch) -> None:
    """In-file dupes are persisted and filtered from claimed batch."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()
    model = _contact_model("dup@example.com")

    claimed = [
        {"row_number": 1, "contact_model": model, "error": None},
        {"row_number": 2, "contact_model": model, "error": None},
    ]
    remaining = await svc._apply_in_file_duplicate_email_errors(
        organization_id=ORG_ID,
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        claimed=claimed,
        totals=totals,
    )

    assert len(remaining) == 1
    assert remaining[0]["row_number"] == 1
    assert totals.errors_total == 1


@pytest.mark.asyncio
async def test_mark_validation_errors_and_collect_valid(monkeypatch) -> None:
    """Validation errors are bulk-marked; valid rows are returned."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    claimed = [
        {
            "row_number": 1,
            "error": {"code": "bad_field", "message": "invalid"},
            "raw_row": {"x": 1},
        },
        _claimed_row(2, "ok@example.com"),
    ]
    valid_rows, valid_nums = await svc._mark_validation_errors_and_collect_valid(
        organization_id=ORG_ID,
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        claimed=claimed,
        totals=totals,
    )

    assert valid_nums == [2]
    assert len(valid_rows) == 1
    assert totals.errors_total == 1
    assert rows_repo.mark_errors_calls[0]["errors"][0][1] == "bad_field"


# ---------------------------------------------------------------------------
# Identity provisioning & companies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_identities_sequential_success(monkeypatch) -> None:
    """Successful identity provisioning returns row_number -> identity map."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    contacts_service = _FakeContactsService()
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    results = await svc._provision_identities_sequential(
        organization_id=ORG_ID,
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        contacts_service=contacts_service,
        claimed=[_claimed_row(1, "a@example.com")],
        totals=totals,
    )

    assert 1 in results
    assert results[1][0] == "user-1"
    assert len(contacts_service.provision_calls) == 1


@pytest.mark.asyncio
async def test_provision_identities_sequential_failure(monkeypatch) -> None:
    """Provision failures mark row errors and update totals."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    contacts_service = _FakeContactsService()

    async def _boom(**kwargs):
        del kwargs
        raise RuntimeError("auth down")

    contacts_service._provision_contact_auth_identity = _boom  # type: ignore[method-assign]
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    results = await svc._provision_identities_sequential(
        organization_id=ORG_ID,
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        contacts_service=contacts_service,
        claimed=[_claimed_row(1, "fail@example.com")],
        totals=totals,
    )

    assert results == {}
    assert totals.errors_total == 1
    assert rows_repo.mark_errors_calls[0]["errors"][0][1] == "external_service_error"


@pytest.mark.asyncio
async def test_ensure_companies_cached_lookup_and_create(monkeypatch) -> None:
    """Company cache loads existing names and creates missing ones."""
    _patch_consumer_deps(monkeypatch)
    _FakeCompaniesRepo.existing_by_name = {"acme": "co-existing"}
    svc = ContactsImportService(db_connection=MagicMock())
    company_repo = _FakeCompaniesRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    await svc._ensure_companies_cached(
        organization_id=ORG_ID,
        company_repo=company_repo,
        desired_names=["Acme", "NewCo", "NewCo"],
        totals=totals,
    )

    assert totals.company_cache["acme"] == "co-existing"
    assert "newco" in totals.company_cache
    assert len(company_repo.create_calls) == 1


# ---------------------------------------------------------------------------
# Persist contacts pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_custom_fields_for_rows(monkeypatch) -> None:
    """Custom field validation returns per-row payloads and errors."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())

    class _FakeCFService:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def validate_for_create(self, custom_fields, entity_type):
            del entity_type
            if custom_fields and custom_fields[0].get("bad"):
                raise ValueError("cf invalid")
            return [{"field": "ok"}]

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.CustomFieldService",
        _FakeCFService,
    )

    valid_rows = [
        _claimed_row(1, "a@example.com"),
        {
            "row_number": 2,
            "contact_model": _contact_model("b@example.com", custom_fields=[{"bad": True}]),
        },
    ]
    by_row, errors = await svc._validate_custom_fields_for_rows(
        valid_rows=valid_rows,
        user_context=UserContext(user_id="u1", email="", organization_id=ORG_ID),
    )

    assert 1 in by_row
    assert len(errors) == 1
    assert errors[0][0] == 2


@pytest.mark.asyncio
async def test_build_contacts_insert_payloads(monkeypatch) -> None:
    """Insert payloads include identity, jsonb fields, and bookkeeping maps."""
    _patch_repos_only(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    model = _contact_model(
        "lead@example.com",
        first_name="Lead",
        portal_access=True,
        lead={"stage_id": "550e8400-e29b-41d4-a716-446655440001", "intake_stage": "web"},
    )
    valid_rows = [
        {
            "row_number": 1,
            "contact_model": model,
            "identity": ("uid-1", "iso-1", "pw-1"),
        }
    ]

    (
        rows_to_insert,
        user_id_by_row,
        password_by_row,
        portal_by_row,
        email_by_row,
    ) = await svc._build_contacts_insert_payloads(
        event=_import_event(),
        valid_rows=valid_rows,
        custom_fields_by_row={1: []},
    )

    assert len(rows_to_insert) == 1
    assert rows_to_insert[0]["user_id"] == "uid-1"
    assert user_id_by_row[1] == "uid-1"
    assert password_by_row[1] == "pw-1"
    assert portal_by_row[1] is True
    assert email_by_row[1] == "lead@example.com"


@pytest.mark.asyncio
async def test_insert_addresses_for_valid_rows(monkeypatch) -> None:
    """Addresses are bulk-inserted and contact_id is annotated on rows."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    contacts_repo = _FakeContactsRepo(db_connection=MagicMock())
    model = _contact_model(
        "addr@example.com",
        addresses=[{"address_line1": "123 Main", "city": "Town"}],
    )
    valid_rows = [{"row_number": 1, "contact_model": model}]

    await svc._insert_addresses_for_valid_rows(
        contacts_repo=contacts_repo,
        valid_rows=valid_rows,
        user_id_by_row={1: "uid-1"},
        contact_ids_by_user={"uid-1": "contact-1"},
    )

    assert valid_rows[0]["contact_id"] == "contact-1"
    assert len(contacts_repo.create_addresses_calls) == 1
    assert contacts_repo.create_addresses_calls[0][0]["contact_id"] == "contact-1"


@pytest.mark.asyncio
async def test_persist_contacts_for_rows_impl_happy_path(monkeypatch) -> None:
    """Full persist impl creates contacts, addresses, leads, and marks success."""
    _patch_repos_only(monkeypatch)
    _FakeContactsRepo.contact_ids_by_user = {"uid-1": "contact-1"}
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    contacts_service = _FakeContactsService()
    totals = _ContactsImportTotals()

    class _FakeCFService:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def validate_for_create(self, custom_fields, entity_type):
            del custom_fields, entity_type
            return []

    class _FakeBulkLeads:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def create_leads_for_rows(self, **kwargs):
            del kwargs
            return ({1: "lead-1"}, [])

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.CustomFieldService",
        _FakeCFService,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.BulkLeadCreator",
        _FakeBulkLeads,
    )

    model = _contact_model(
        "persist@example.com",
        portal_access=True,
        lead={"stage_id": "550e8400-e29b-41d4-a716-446655440001"},
    )
    valid_rows = [
        {
            "row_number": 1,
            "contact_model": model,
            "identity": ("uid-1", "iso-1", "pw-1"),
        }
    ]

    await svc._persist_contacts_for_rows_impl(
        event=_import_event(),
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        contacts_service=contacts_service,
        user_context=UserContext(user_id="u1", email="", organization_id=ORG_ID),
        org_name="Acme",
        valid_rows=valid_rows,
        valid_row_numbers=[1],
        totals=totals,
    )

    assert totals.success_total == 1
    assert totals.created_contact_ids == ["contact-1"]
    assert rows_repo.mark_success_calls
    assert contacts_service.email_calls


@pytest.mark.asyncio
async def test_persist_contacts_for_rows_impl_lead_errors(monkeypatch) -> None:
    """Lead creation errors roll back contact and mark row errors."""
    _patch_repos_only(monkeypatch)
    _FakeContactsRepo.contact_ids_by_user = {"uid-1": "contact-1"}
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    contacts_service = _FakeContactsService()
    totals = _ContactsImportTotals()

    class _FakeCFService:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def validate_for_create(self, custom_fields, entity_type):
            del custom_fields, entity_type
            return []

    class _FakeBulkLeads:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def create_leads_for_rows(self, **kwargs):
            del kwargs
            return ({}, [(1, "lead failed")])

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.CustomFieldService",
        _FakeCFService,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.BulkLeadCreator",
        _FakeBulkLeads,
    )

    model = _contact_model(
        "leadfail@example.com",
        lead={"stage_id": "550e8400-e29b-41d4-a716-446655440001"},
    )
    valid_rows = [
        {
            "row_number": 1,
            "contact_model": model,
            "identity": ("uid-1", "iso-1", None),
            "contact_id": "contact-1",
        }
    ]

    await svc._persist_contacts_for_rows_impl(
        event=_import_event(),
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        contacts_service=contacts_service,
        user_context=UserContext(user_id="u1", email="", organization_id=ORG_ID),
        org_name="Acme",
        valid_rows=valid_rows,
        valid_row_numbers=[1],
        totals=totals,
    )

    contacts_repo = _FakeContactsRepo.last_instance
    assert contacts_repo is not None
    assert contacts_repo.soft_delete_calls
    assert rows_repo.mark_errors_calls
    assert totals.errors_total >= 1


@pytest.mark.asyncio
async def test_persist_contacts_for_rows_handles_db_error(monkeypatch) -> None:
    """Outer persist wrapper marks all valid rows error on impl failure."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    logs_repo = _FakeLogsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    async def _boom(**kwargs) -> None:
        del kwargs
        raise RuntimeError("db insert failed")

    monkeypatch.setattr(svc, "_persist_contacts_for_rows_impl", _boom)

    await svc._persist_contacts_for_rows(
        event=_import_event(),
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        logs_repo=logs_repo,
        contacts_service=_FakeContactsService(),
        user_context=UserContext(user_id="u1", email="", organization_id=ORG_ID),
        org_name="Acme",
        valid_rows=[_claimed_row(1, "a@example.com")],
        valid_row_numbers=[1],
        totals=totals,
    )

    assert totals.errors_total == 1
    assert rows_repo.mark_errors_calls[0]["errors"][0][1] == "db_error"
    jobs_repo = _FakeJobsRepo.last_instance
    assert jobs_repo is not None
    assert jobs_repo.increment_calls
    assert logs_repo.upsert_calls[-1]["payload"]["phase"] == "warning"


@pytest.mark.asyncio
async def test_build_contact_row_from_model(monkeypatch) -> None:
    """Validated model converts to DB row payload with custom fields."""
    _patch_repos_only(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())

    class _FakeCFService:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def validate_for_create(self, custom_fields, entity_type):
            del custom_fields, entity_type
            return [{"validated": True}]

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.CustomFieldService",
        _FakeCFService,
    )

    model = _contact_model("row@example.com", first_name="Row")
    row = await svc._build_contact_row_from_model(row_model=model)

    assert row["email"] == "row@example.com"
    assert row["first_name"] == "Row"
    assert row["status"] == "active"


# ---------------------------------------------------------------------------
# Entity list / customer list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_entity_list_id_by_name(monkeypatch) -> None:
    """Entity list lookup delegates to repository."""
    _patch_consumer_deps(monkeypatch)
    _FakeEntityListsRepo.existing_list_id = "list-42"
    svc = ContactsImportService(db_connection=MagicMock())

    list_id = await svc._get_entity_list_id_by_name(
        organization_id=ORG_ID,
        name="My List",
        entity_type=EntityType.CONTACT,
    )

    assert list_id == "list-42"
    repo = _FakeEntityListsRepo.last_instance
    assert repo is not None
    assert repo.get_id_calls[0]["name"] == "My List"


@pytest.mark.asyncio
async def test_add_entity_ids_to_list_in_chunks(monkeypatch) -> None:
    """Entity ids are added in bounded update_list chunks."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    repo = _FakeEntityListsRepo(db_connection=MagicMock())

    await svc._add_entity_ids_to_list_in_chunks(
        repo=repo,
        organization_id=ORG_ID,
        list_id="list-1",
        entity_ids=["a", "b"],
        chunk_size=1,
    )

    assert len(repo.update_calls) == 2
    assert repo.update_calls[0]["update_data"]["add_entity_ids"] == ["a"]


@pytest.mark.asyncio
async def test_maybe_create_customer_list_disabled(monkeypatch) -> None:
    """Customer list is skipped when option is false."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    totals = _ContactsImportTotals(created_contact_ids=["c1"])

    result = await svc._maybe_create_customer_list(
        event=_import_event(),
        options={"create_customer_list": False},
        totals=totals,
    )

    assert result is None


@pytest.mark.asyncio
async def test_maybe_create_customer_list_creates(monkeypatch) -> None:
    """Customer list is created and contacts are added in chunks."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    totals = _ContactsImportTotals(created_contact_ids=["c1", "c2"])

    list_id = await svc._maybe_create_customer_list(
        event=_import_event(),
        options={"create_customer_list": True},
        totals=totals,
    )

    assert list_id == "list-new-1"
    assert _FakeEntityListsRepo.all_create_calls
    assert _FakeEntityListsRepo.all_update_calls


@pytest.mark.asyncio
async def test_maybe_create_customer_list_unique_retry(monkeypatch) -> None:
    """UniqueViolation on create falls back to lookup."""
    _patch_repos_only(monkeypatch)
    _FakeEntityListsRepo.create_raises_unique = True
    _FakeEntityListsRepo.lookup_results = [None, "list-existing"]
    svc = ContactsImportService(db_connection=MagicMock())
    totals = _ContactsImportTotals(created_contact_ids=["c1"])

    list_id = await svc._maybe_create_customer_list(
        event=_import_event(),
        options={"create_customer_list": True},
        totals=totals,
    )

    assert list_id == "list-existing"
    assert _FakeEntityListsRepo.all_create_calls


@pytest.mark.asyncio
async def test_maybe_create_customer_list_no_contacts(monkeypatch) -> None:
    """No created contacts skips list creation."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    result = await svc._maybe_create_customer_list(
        event=_import_event(),
        options={"create_customer_list": True},
        totals=totals,
    )

    assert result is None


# ---------------------------------------------------------------------------
# CSV iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_row_batches_yields_sized_batches() -> None:
    """Row batches respect batch_size boundary."""
    svc = ContactsImportService(db_connection=MagicMock())

    async def _fake_items(**kwargs):
        del kwargs
        for n in range(1, 4):
            yield {"row_number": n}

    svc._iter_row_items = _fake_items  # type: ignore[method-assign]
    batches = []
    async for batch in svc._iter_row_batches(
        reader=None,
        reverse_mapping={},
        batch_size=2,
    ):
        batches.append(batch)

    assert len(batches) == 2
    assert len(batches[0]) == 2
    assert len(batches[1]) == 1


@pytest.mark.asyncio
async def test_iter_row_items_valid_and_invalid() -> None:
    """Row items include validated models and validation errors."""
    svc = ContactsImportService(db_connection=MagicMock())
    csv_io = io.StringIO("Email,First\njane@example.com,Jane\n,bad\n")
    reader = ContactsImportService._build_csv_reader(
        f=csv_io,
        has_header=True,
        mapping={"email": "Email", "first_name": "First"},
    )
    reverse = ContactsImportService._build_reverse_mapping(
        mapping={"email": "Email", "first_name": "First"}
    )

    items = []
    async for item in svc._iter_row_items(reader=reader, reverse_mapping=reverse):
        items.append(item)

    assert items[0]["contact_model"] is not None
    assert items[0]["error"] is None
    assert items[1]["error"] is not None


@pytest.mark.asyncio
async def test_iter_validated_rows_for_ledger(monkeypatch, tmp_path) -> None:
    """Ledger iterator downloads CSV, validates rows, and cleans temp file."""
    svc = ContactsImportService(db_connection=MagicMock())
    csv_path = tmp_path / "import.csv"
    csv_path.write_text("Email\na@example.com\n", encoding="utf-8")

    async def _fake_download(**kwargs) -> str:
        del kwargs
        return str(csv_path)

    monkeypatch.setattr(svc, "_download_csv_to_tmp", _fake_download)

    batches = []
    async for batch in svc._iter_validated_rows_for_ledger(
        file_url=FILE_URL,
        mapping={"email": "Email"},
        options={"has_header": True},
        batch_size=10,
    ):
        batches.append(batch)

    assert len(batches) == 1
    assert batches[0][0]["contact_model"] is not None


@pytest.mark.asyncio
async def test_iter_validated_rows_empty_url() -> None:
    """Empty file_url yields nothing."""
    svc = ContactsImportService(db_connection=MagicMock())
    batches = []
    async for batch in svc._iter_validated_rows_for_ledger(
        file_url="",
        mapping={},
        options={},
        batch_size=10,
    ):
        batches.append(batch)
    assert batches == []


@pytest.mark.asyncio
async def test_download_csv_empty_returns_none(monkeypatch) -> None:
    """Empty downloaded file returns None path."""
    svc = ContactsImportService(db_connection=MagicMock())

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            return
            yield  # pragma: no cover

    class _FakeStream:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *args):
            del args
            return False

    class _FakeClient:
        def stream(self, method, url):
            del method, url
            return _FakeStream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args
            return False

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.httpx.AsyncClient",
        lambda **kwargs: _FakeClient(),
    )

    path = await svc._download_csv_to_tmp(file_url=FILE_URL)
    assert path is None


# ---------------------------------------------------------------------------
# process_contacts_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_contacts_batch_empty() -> None:
    """Empty batch returns zero counters."""
    svc = ContactsImportService(db_connection=MagicMock())
    result = await svc.process_contacts_batch(
        job_id=JOB_KEY,
        organization_id=ORG_ID,
        contacts=[],
    )
    assert result == {"total": 0, "processed": 0, "success": 0, "errors": 0}


@pytest.mark.asyncio
async def test_process_contacts_batch_inserts(monkeypatch) -> None:
    """Batch insert sets org_id and increments job counters."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())

    result = await svc.process_contacts_batch(
        job_id=JOB_KEY,
        organization_id=ORG_ID,
        contacts=[{"email": "a@example.com"}],
    )

    assert result["success"] == 1
    contacts_repo = _FakeContactsRepo.last_instance
    assert contacts_repo is not None
    assert contacts_repo.create_contacts_calls[0][0]["organization_id"] == ORG_ID
    jobs_repo = _FakeJobsRepo.last_instance
    assert jobs_repo is not None
    assert jobs_repo.increment_calls[0]["success_rows_delta"] == 1


# ---------------------------------------------------------------------------
# process_job_event integration paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_event_db_duplicate_emails(monkeypatch) -> None:
    """DB duplicate emails are marked during full event processing."""
    _patch_consumer_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    _FakeContactsRepo.existing_emails = {"exists@example.com": "cid-1"}
    svc = ContactsImportService(db_connection=MagicMock())

    async def _fake_iter(self, **kwargs):
        del kwargs
        yield [
            _claimed_row(1, "exists@example.com"),
            _claimed_row(2, "new@example.com"),
        ]

    monkeypatch.setattr(ContactsImportService, "_iter_validated_rows_for_ledger", _fake_iter)
    monkeypatch.setattr(
        svc,
        "_provision_identities_sequential",
        AsyncMock(return_value={2: ("u2", "i2", None)}),
    )
    monkeypatch.setattr(svc, "_persist_contacts_for_rows", AsyncMock())

    await svc.process_job_event(event=_import_event())

    rows_repo = _FakeRowsRepo.last_instance
    assert rows_repo is not None
    dup_calls = [
        c for c in rows_repo.mark_errors_calls if c["errors"][0][1] == "email_already_exists"
    ]
    assert dup_calls


@pytest.mark.asyncio
async def test_process_event_creates_customer_list(monkeypatch) -> None:
    """Completed job with create_customer_list option includes entity_list_id in log."""
    _patch_consumer_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job(
        options={"create_customer_list": True, "has_header": True}
    )
    svc = ContactsImportService(db_connection=MagicMock())

    async def _fake_batches(**kwargs) -> None:
        kwargs["totals"].created_contact_ids.append("contact-99")

    monkeypatch.setattr(svc, "_process_event_batches", _fake_batches)
    monkeypatch.setattr(
        svc,
        "_maybe_create_customer_list",
        AsyncMock(return_value="list-99"),
    )

    await svc.process_job_event(event=_import_event())

    logs_repo = _FakeLogsRepo.last_instance
    assert logs_repo is not None
    finished = logs_repo.upsert_calls[-1]["payload"]
    assert finished["phase"] == "finished"
    assert finished["entity_list_id"] == "list-99"


@pytest.mark.asyncio
async def test_process_event_customer_list_failure_is_best_effort(monkeypatch) -> None:
    """Customer list failure does not fail the import job."""
    _patch_consumer_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job(options={"create_customer_list": True})
    svc = ContactsImportService(db_connection=MagicMock())

    async def _fake_batches(**kwargs) -> None:
        kwargs["totals"].created_contact_ids.append("c1")

    monkeypatch.setattr(svc, "_process_event_batches", _fake_batches)
    monkeypatch.setattr(
        svc,
        "_maybe_create_customer_list",
        AsyncMock(side_effect=RuntimeError("list boom")),
    )

    await svc.process_job_event(event=_import_event())

    jobs_repo = _FakeJobsRepo.last_instance
    assert jobs_repo is not None
    assert jobs_repo.set_status_ts_calls[-1]["status"] == ContactsImportJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_process_event_failed_status_persistence_errors(monkeypatch) -> None:
    """Failure path tolerates status/log persistence errors."""
    _patch_consumer_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    svc = ContactsImportService(db_connection=MagicMock())

    async def _boom(**kwargs) -> None:
        del kwargs
        raise RuntimeError("processing failed")

    monkeypatch.setattr(svc, "_process_event_batches", _boom)

    class _FailingJobsRepo(_FakeJobsRepo):
        async def set_status_and_timestamps(self, **kwargs) -> None:
            if kwargs.get("status") == ContactsImportJobStatus.FAILED.value:
                raise RuntimeError("status write failed")
            await super().set_status_and_timestamps(**kwargs)

    class _FailingLogsRepo(_FakeLogsRepo):
        async def upsert_payload(self, **kwargs) -> None:
            if kwargs.get("payload", {}).get("phase") == "failed":
                raise RuntimeError("log write failed")
            await super().upsert_payload(**kwargs)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ImportJobsRepository",
        _FailingJobsRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ImportJobLogsRepository",
        _FailingLogsRepo,
    )

    with pytest.raises(RuntimeError, match="processing failed"):
        await svc.process_job_event(event=_import_event())


@pytest.mark.asyncio
async def test_process_event_batches_running_log(monkeypatch) -> None:
    """Running-phase log is upserted when batch processing spans >= 2 seconds."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    logs_repo = _FakeLogsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()
    model = _contact_model("batch@example.com")

    async def _fake_iter(**kwargs):
        del kwargs
        yield [
            {
                "row_number": 1,
                "raw_row": {"email": "batch@example.com"},
                "error": None,
                "contact_model": model,
            }
        ]

    monkeypatch.setattr(svc, "_iter_validated_rows_for_ledger", _fake_iter)
    monkeypatch.setattr(
        svc,
        "_provision_identities_sequential",
        AsyncMock(return_value={1: ("u1", "i1", None)}),
    )
    monkeypatch.setattr(svc, "_persist_contacts_for_rows", AsyncMock())

    monkeypatch.setattr(time, "time", lambda: 3.0)

    await svc._process_event_batches(
        event=_import_event(),
        batch_size=10,
        mapping={"email": "Email"},
        options={},
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        logs_repo=logs_repo,
        contacts_service=_FakeContactsService(),
        user_context=UserContext(user_id="u1", email="", organization_id=ORG_ID),
        org_name="Acme",
        totals=totals,
    )

    running_logs = [c for c in logs_repo.upsert_calls if c["payload"].get("phase") == "running"]
    assert running_logs


@pytest.mark.asyncio
async def test_process_event_batches_skips_empty_and_success(monkeypatch) -> None:
    """Empty batches and already-successful rows are skipped."""
    _patch_consumer_deps(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())
    rows_repo = _FakeRowsRepo(db_connection=MagicMock())
    logs_repo = _FakeLogsRepo(db_connection=MagicMock())
    totals = _ContactsImportTotals()

    call_count = 0

    async def _fake_iter(**kwargs):
        del kwargs
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield []
            return
        yield [_claimed_row(1, "skip@example.com")]

    async def _claim_all_success(**kwargs):
        del kwargs
        return {1: "success"}

    rows_repo.claim_rows_processing = _claim_all_success  # type: ignore[method-assign]
    monkeypatch.setattr(svc, "_iter_validated_rows_for_ledger", _fake_iter)
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_contacts_for_rows", persist)

    await svc._process_event_batches(
        event=_import_event(),
        batch_size=10,
        mapping={},
        options={},
        job_internal_id="internal-1",
        rows_repo=rows_repo,
        logs_repo=logs_repo,
        contacts_service=_FakeContactsService(),
        user_context=UserContext(user_id="u1", email="", organization_id=ORG_ID),
        org_name="Acme",
        totals=totals,
    )

    persist.assert_not_called()
