"""Unit tests for ContactsImportService producer methods."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.contacts_imports import ContactsImportEventPayload
from apps.user_service.app.schemas.enums import (
    ContactsImportEventAction,
    ContactsImportJobStatus,
    ContactsImportType,
)
from apps.user_service.app.services.contacts_imports_service import (
    ContactsImportService,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
FILE_URL = "https://cdn.example.com/contacts.csv"


class _FakeJobsRepo:
    """Configurable fake ImportJobsRepository."""

    configured_job: dict[str, Any] | None = None
    configured_updated_job: dict[str, Any] | None = None
    last_instance: "_FakeJobsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.create_job_calls: list[dict[str, Any]] = []
        self.get_job_calls: list[dict[str, Any]] = []
        self.set_status_calls: list[dict[str, Any]] = []
        self.set_status_ts_calls: list[dict[str, Any]] = []
        self.increment_calls: list[dict[str, Any]] = []
        self._job = _FakeJobsRepo.configured_job
        self._updated_job = _FakeJobsRepo.configured_updated_job
        _FakeJobsRepo.last_instance = self

    async def create_job(self, **kwargs) -> dict[str, Any]:
        """Record create and return configured job."""
        self.create_job_calls.append(kwargs)
        return self._job or {
            "id": "job-internal-1",
            "job_key": kwargs["job_id"],
            "job_id": kwargs["job_id"],
            "organization_id": kwargs["organization_id"],
            "status": kwargs["status"],
            "file_url": kwargs["file_url"],
            "file_type": kwargs["file_type"],
            "schema_version": kwargs["schema_version"],
            "mapping": kwargs.get("mapping") or {},
            "options": kwargs.get("options") or {},
        }

    async def get_job(self, *, job_id: str, organization_id: str) -> dict[str, Any] | None:
        """Return configured job or updated job after set_status."""
        self.get_job_calls.append({"job_id": job_id, "organization_id": organization_id})
        if self._updated_job is not None and len(self.get_job_calls) > 1:
            return self._updated_job
        return self._job

    async def set_status(self, *, job_id: str, organization_id: str, status: str) -> None:
        """Record status update and prepare updated job snapshot."""
        self.set_status_calls.append(
            {"job_id": job_id, "organization_id": organization_id, "status": status}
        )
        if self._job:
            self._updated_job = {**self._job, "status": status}

    async def set_status_and_timestamps(self, **kwargs) -> None:
        """Record running/completed/failed status updates."""
        self.set_status_ts_calls.append(kwargs)
        if self._job:
            self._updated_job = {**self._job, "status": kwargs["status"]}

    async def increment_counters(self, **kwargs) -> None:
        """Record counter increments."""
        self.increment_calls.append(kwargs)


class _FakeRowsRepo:
    """Configurable fake ImportJobRowsRepository."""

    configured_items: list[dict[str, Any]] = []
    configured_total: int = 0
    last_instance: "_FakeRowsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.list_rows_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.mark_errors_calls: list[dict[str, Any]] = []
        self.mark_success_calls: list[dict[str, Any]] = []
        self._items = list(_FakeRowsRepo.configured_items)
        self._total = _FakeRowsRepo.configured_total
        _FakeRowsRepo.last_instance = self

    async def list_rows(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        """Return configured row page."""
        self.list_rows_calls.append(kwargs)
        return self._items, self._total

    async def claim_rows_processing(self, **kwargs) -> dict[int, str]:
        """Mark claimed rows as pending."""
        self.claim_calls.append(kwargs)
        rows = kwargs.get("rows") or []
        return {int(row_number): "pending" for row_number, _ in rows}

    async def mark_errors_bulk(self, **kwargs) -> None:
        """Record bulk error marks."""
        self.mark_errors_calls.append(kwargs)

    async def mark_success_bulk(self, **kwargs) -> None:
        """Record bulk success marks."""
        self.mark_success_calls.append(kwargs)


class _FakeLogsRepo:
    """Configurable fake ImportJobLogsRepository."""

    configured_items: list[dict[str, Any]] = []
    configured_total: int = 0
    last_instance: "_FakeLogsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.list_logs_calls: list[dict[str, Any]] = []
        self.upsert_calls: list[dict[str, Any]] = []
        self._items = list(_FakeLogsRepo.configured_items)
        self._total = _FakeLogsRepo.configured_total
        _FakeLogsRepo.last_instance = self

    async def list_logs(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        """Return configured log page."""
        self.list_logs_calls.append(kwargs)
        return self._items, self._total

    async def upsert_payload(self, **kwargs) -> None:
        """Record log payload upserts."""
        self.upsert_calls.append(kwargs)


class _FakeContactsRepo:
    """Minimal ContactsRepository fake for import batch tests."""

    last_instance: "_FakeContactsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        _FakeContactsRepo.last_instance = self

    async def get_contact_ids_by_emails(self, **kwargs) -> dict[str, str]:
        """Return no existing emails by default."""
        del kwargs
        return {}


class _FakeOrgRepo:
    """Minimal org lookup fake for ContactsService."""

    async def get_organization_by_id(self, organization_id: str) -> dict[str, str]:
        """Return a fixed organization name."""
        del organization_id
        return {"name": "Acme Corp"}


class _FakeContactsService:
    """Minimal ContactsService fake for consumer processing."""

    def __init__(self, **kwargs) -> None:
        del kwargs
        self.org_repo = _FakeOrgRepo()


def _patch_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch import repositories used by ContactsImportService."""
    _FakeJobsRepo.configured_job = None
    _FakeJobsRepo.configured_updated_job = None
    _FakeRowsRepo.configured_items = []
    _FakeRowsRepo.configured_total = 0
    _FakeLogsRepo.configured_items = []
    _FakeLogsRepo.configured_total = 0
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
        MagicMock,
    )


def _patch_process_event_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch repos and external deps for process_job_event tests."""
    _patch_repos(monkeypatch)

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
        "job_key": "imp_test",
        "job_id": "imp_test",
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
        "job_key": "imp_test",
        "organization_id": ORG_ID,
        "file_url": FILE_URL,
        "requested_by": "user-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "action": ContactsImportEventAction.CREATE,
    }
    payload.update(overrides)
    return ContactsImportEventPayload.model_validate(payload)


def _contact_model(email: str) -> CreateContactRequest:
    """Build a minimal valid CreateContactRequest."""
    return CreateContactRequest.model_validate({"email": email})


@pytest.mark.asyncio
async def test_create_job_enqueues_payload(monkeypatch):
    """Create job persists row and builds CREATE payload."""
    _patch_repos(monkeypatch)
    svc = ContactsImportService(db_connection=MagicMock())

    job, payload = await svc.create_job_and_enqueue(
        organization_id=ORG_ID,
        requested_by="user-1",
        file_url=FILE_URL,
        file_type="csv",
        schema_version=1,
        mapping={"email": "Email"},
        options={"has_header": True},
    )

    repo = _FakeJobsRepo.last_instance
    assert repo is not None
    assert repo.create_job_calls[0]["organization_id"] == ORG_ID
    assert repo.create_job_calls[0]["status"] == ContactsImportJobStatus.QUEUED.value
    assert job["file_url"] == FILE_URL
    assert payload["action"] == ContactsImportEventAction.CREATE.value
    assert payload["import_type"] == ContactsImportType.CONTACTS.value
    assert payload["organization_id"] == ORG_ID
    assert payload["file_url"] == FILE_URL
    assert payload["requested_by"] == "user-1"
    assert payload["job_key"].startswith("imp_")


@pytest.mark.asyncio
async def test_create_job_rejects_bad_url():
    """Invalid file_url raises before repository access."""
    svc = ContactsImportService(db_connection=MagicMock())
    with pytest.raises(ValueError, match="file_url"):
        await svc.create_job_and_enqueue(
            organization_id=ORG_ID,
            requested_by=None,
            file_url="http://localhost/secret.csv",
            file_type="csv",
            schema_version=1,
            mapping=None,
            options=None,
        )


@pytest.mark.asyncio
async def test_get_job_delegates_to_repo(monkeypatch):
    """Get job forwards org-scoped lookup to repository."""
    _patch_repos(monkeypatch)
    _FakeJobsRepo.configured_job = {"job_key": "imp_abc", "job_id": "imp_abc", "status": "queued"}
    svc = ContactsImportService(db_connection=MagicMock())

    result = await svc.get_job(job_id="imp_abc", organization_id=ORG_ID)

    assert result["job_id"] == "imp_abc"
    repo = _FakeJobsRepo.last_instance
    assert repo is not None
    assert repo.get_job_calls[0]["job_id"] == "imp_abc"
    assert repo.get_job_calls[0]["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_list_job_logs_paginated(monkeypatch):
    """List job logs delegates pagination to logs repository."""
    _patch_repos(monkeypatch)
    _FakeLogsRepo.configured_items = [{"job_id": "imp_1", "payload": {"phase": "running"}}]
    _FakeLogsRepo.configured_total = 1
    svc = ContactsImportService(db_connection=MagicMock())

    items, total = await svc.list_job_logs(
        organization_id=ORG_ID,
        page=2,
        page_size=10,
    )

    assert total == 1
    assert items[0]["job_id"] == "imp_1"
    repo = _FakeLogsRepo.last_instance
    assert repo is not None
    assert repo.list_logs_calls[0]["organization_id"] == ORG_ID
    assert repo.list_logs_calls[0]["page"] == 2
    assert repo.list_logs_calls[0]["page_size"] == 10


@pytest.mark.asyncio
async def test_retry_job_none_when_missing(monkeypatch):
    """Retry returns None when job is not found."""
    _patch_repos(monkeypatch)
    _FakeJobsRepo.configured_job = None
    svc = ContactsImportService(db_connection=MagicMock())

    result = await svc.retry_job_and_enqueue(
        job_id="imp_missing",
        organization_id=ORG_ID,
        requested_by="user-1",
    )

    assert result is None
    repo = _FakeJobsRepo.last_instance
    assert repo is not None
    assert not repo.set_status_calls


@pytest.mark.asyncio
async def test_retry_job_requeues_and_payload(monkeypatch):
    """Retry sets queued status and builds RETRY payload."""
    _patch_repos(monkeypatch)
    _FakeJobsRepo.configured_job = {
        "id": "internal-1",
        "job_key": "imp_retry",
        "job_id": "imp_retry",
        "status": "failed",
        "file_url": FILE_URL,
        "schema_version": 2,
    }
    svc = ContactsImportService(db_connection=MagicMock())

    result = await svc.retry_job_and_enqueue(
        job_id="imp_retry",
        organization_id=ORG_ID,
        requested_by="user-2",
    )

    assert result is not None
    updated, payload = result
    repo = _FakeJobsRepo.last_instance
    assert repo is not None
    assert repo.set_status_calls[0]["status"] == ContactsImportJobStatus.QUEUED.value
    assert updated["status"] == ContactsImportJobStatus.QUEUED.value
    assert payload["action"] == ContactsImportEventAction.RETRY.value
    assert payload["schema_version"] == 2
    assert payload["requested_by"] == "user-2"


# ---------------------------------------------------------------------------
# Static / pure helper tests
# ---------------------------------------------------------------------------


def test_safe_parse_json_dict_valid() -> None:
    """Parse JSON string into dict."""
    result = ContactsImportService._safe_parse_json_dict('{"a": 1}')
    assert result == {"a": 1}


def test_safe_parse_json_dict_invalid() -> None:
    """Invalid JSON returns empty dict."""
    assert ContactsImportService._safe_parse_json_dict("{bad") == {}


def test_safe_parse_json_dict_not_dict() -> None:
    """Non-dict JSON returns empty dict."""
    assert ContactsImportService._safe_parse_json_dict("[1, 2]") == {}


def test_safe_parse_json_dict_none() -> None:
    """None input returns empty dict."""
    assert ContactsImportService._safe_parse_json_dict(None) == {}


def test_normalize_phone_strips_chars() -> None:
    """Phone normalization keeps digits only."""
    digits = ContactsImportService._normalize_phone_digits(phone_number="(415) 555-0100")
    assert digits == "4155550100"


def test_normalize_phone_with_isd() -> None:
    """ISD code is included in digit key."""
    digits = ContactsImportService._normalize_phone_digits(
        phone_number="5550100",
        phone_isd_code="+1",
    )
    assert digits == "15550100"


def test_normalize_phone_empty_string() -> None:
    """Empty phone yields empty digit string."""
    assert ContactsImportService._normalize_phone_digits(phone_number="") == ""


def test_synthetic_email_builds() -> None:
    """Synthetic email uses digits and fixed domain."""
    email = ContactsImportService._synthetic_email_from_phone(
        phone_number="4155550100",
        phone_isd_code="+1",
    )
    assert email == "14155550100@email.com"


def test_synthetic_email_none_when_empty() -> None:
    """Empty phone yields no synthetic email."""
    assert ContactsImportService._synthetic_email_from_phone(phone_number="***") is None


def test_in_file_dupes_marks_second() -> None:
    """Later duplicate email rows become error tuples."""
    claimed = [
        {"row_number": 1, "contact_model": _contact_model("dup@example.com"), "error": None},
        {"row_number": 2, "contact_model": _contact_model("dup@example.com"), "error": None},
    ]
    errors = ContactsImportService._collect_in_file_duplicate_email_errors(claimed=claimed)
    assert len(errors) == 1
    assert errors[0][0] == 2
    assert errors[0][1] == "duplicate_email_in_file"


def test_in_file_dupes_ignores_errors() -> None:
    """Rows with existing errors are skipped."""
    claimed = [
        {"row_number": 1, "contact_model": None, "error": {"code": "x"}},
        {"row_number": 2, "contact_model": _contact_model("a@example.com"), "error": None},
    ]
    assert ContactsImportService._collect_in_file_duplicate_email_errors(claimed=claimed) == []


def test_in_file_dupes_no_email() -> None:
    """Rows without email are not checked for dupes."""
    empty_email_model = MagicMock()
    empty_email_model.email = ""
    claimed = [
        {"row_number": 1, "contact_model": empty_email_model, "error": None},
    ]
    assert ContactsImportService._collect_in_file_duplicate_email_errors(claimed=claimed) == []


def test_canonicalize_row_maps_keys() -> None:
    """CSV headers map to canonical schema keys."""
    row = {"Email": "a@x.com", "First": "Ann"}
    mapping = {"email": "Email", "first_name": "First"}
    reverse = ContactsImportService._build_reverse_mapping(mapping=mapping)
    canonical = ContactsImportService._canonicalize_row(row=row, reverse_mapping=reverse)
    assert canonical["email"] == "a@x.com"
    assert canonical["first_name"] == "Ann"


def test_canonicalize_row_unknown_header() -> None:
    """Unmapped headers pass through unchanged."""
    canonical = ContactsImportService._canonicalize_row(
        row={"Extra": "value"},
        reverse_mapping={},
    )
    assert canonical["Extra"] == "value"


def test_row_item_missing_id_shape() -> None:
    """Missing identifier row item has expected shape."""
    item = ContactsImportService._row_item_missing_contact_identifier(
        row_number=3,
        raw_row={"first_name": "Bob"},
        message="either email or phone number is required",
    )
    assert item["row_number"] == 3
    assert item["error"]["code"] == "missing_email_or_phone"
    assert item["contact_model"] is None


def test_extract_phones_from_json() -> None:
    """phones_json list is parsed into phone dicts."""
    phones = ContactsImportService._extract_phones_from_canonical(
        {"phones_json": '[{"phone_number":"2125550199","phone_isd_code":"+1"}]'}
    )
    assert len(phones) == 1
    assert phones[0]["phone_number"] == "2125550199"


def test_extract_phones_flat_default_isd() -> None:
    """Flat phone columns default ISD to +1."""
    phones = ContactsImportService._extract_phones_from_canonical({"phone_number": "5551234"})
    assert phones[0]["phone_isd_code"] == "+1"
    assert phones[0]["is_primary"] is True


def test_extract_phones_empty() -> None:
    """No phone columns yields empty list."""
    assert ContactsImportService._extract_phones_from_canonical({}) == []


def test_primary_phone_prefers_flag() -> None:
    """Primary flag selects the correct phone entry."""
    phones = [
        {"phone_number": "111", "is_primary": False},
        {"phone_number": "222", "is_primary": True},
    ]
    result = ContactsImportService._primary_phone_fields(phones)
    assert result == ("222", None)


def test_primary_phone_first_fallback() -> None:
    """First phone is used when none is primary."""
    phones = [{"phone_number": "333", "phone_isd_code": "+44"}]
    result = ContactsImportService._primary_phone_fields(phones)
    assert result == ("333", "+44")


def test_primary_phone_none_when_empty() -> None:
    """Empty phone list returns None."""
    assert ContactsImportService._primary_phone_fields([]) is None


def test_resolve_row_trims_email() -> None:
    """Whitespace around email is stripped."""
    email, _, err = ContactsImportService._resolve_row_email_and_phones(
        canonical={"email": "  jane@example.com  "}
    )
    assert err is None
    assert email == "jane@example.com"


def test_build_reverse_mapping_inverts() -> None:
    """Canonical-to-header mapping is inverted."""
    reverse = ContactsImportService._build_reverse_mapping(
        mapping={"email": "Email", "first_name": "First Name"}
    )
    assert reverse["Email"] == "email"
    assert reverse["First Name"] == "first_name"


def test_build_reverse_mapping_empty_header() -> None:
    """Empty header values are omitted from reverse map."""
    reverse = ContactsImportService._build_reverse_mapping(mapping={"email": "", "first_name": "F"})
    assert reverse == {"F": "first_name"}


def test_validate_url_https_ok() -> None:
    """Valid HTTPS URL passes validation."""
    ContactsImportService._validate_file_url("https://cdn.example.com/file.csv")


@pytest.mark.parametrize(
    "url,match",
    [
        ("", "invalid"),
        ("ftp://cdn.example.com/x.csv", "http"),
        ("https://", "host"),
        ("http://localhost/x.csv", "not allowed"),
        ("https://app.localhost/x.csv", "not allowed"),
    ],
)
def test_validate_url_rejects(url: str, match: str) -> None:
    """Unsafe or malformed URLs are rejected."""
    with pytest.raises(ValueError, match=match):
        ContactsImportService._validate_file_url(url)


def test_validate_url_rejects_long() -> None:
    """Overly long URLs are rejected."""
    with pytest.raises(ValueError, match="invalid"):
        ContactsImportService._validate_file_url("https://example.com/" + ("a" * 5000))


def test_customer_list_custom_name() -> None:
    """Custom customer list name from options is used."""
    name = ContactsImportService._resolve_customer_list_name(
        job_key="imp_1",
        options={"customer_list_name": " VIP List "},
    )
    assert name == "VIP List"


def test_customer_list_default_name() -> None:
    """Default list name includes job key."""
    name = ContactsImportService._resolve_customer_list_name(job_key="imp_abc", options={})
    assert name == "Contacts Import imp_abc"


def test_success_rows_without_lead() -> None:
    """Contacts without leads mark success when contact_id exists."""
    rows = [{"row_number": 1, "contact_model": _contact_model("a@x.com"), "contact_id": "c1"}]
    assert ContactsImportService._success_row_numbers_for_rows(
        valid_rows=rows, lead_ids_by_row={}
    ) == [1]


def test_success_rows_requires_lead_id() -> None:
    """Lead rows require a lead id to count as success."""
    model = CreateContactRequest.model_validate(
        {
            "email": "lead@x.com",
            "lead": {"stage_id": "550e8400-e29b-41d4-a716-446655440001"},
        }
    )
    rows = [{"row_number": 2, "contact_model": model, "contact_id": "c2"}]
    assert (
        ContactsImportService._success_row_numbers_for_rows(valid_rows=rows, lead_ids_by_row={})
        == []
    )


def test_success_rows_skips_no_contact() -> None:
    """Rows without contact_id are not marked success."""
    rows = [{"row_number": 3, "contact_model": _contact_model("b@x.com")}]
    assert (
        ContactsImportService._success_row_numbers_for_rows(valid_rows=rows, lead_ids_by_row={})
        == []
    )


def test_build_lead_items_basic() -> None:
    """Lead payload produces BulkLeadCreator row item."""
    model = CreateContactRequest.model_validate(
        {
            "email": "lead@x.com",
            "first_name": "Lead",
            "last_name": "User",
            "lead": {"stage_id": "550e8400-e29b-41d4-a716-446655440001"},
        }
    )
    rows = [{"row_number": 1, "contact_model": model, "contact_id": "cid-1"}]
    items = ContactsImportService._build_lead_items_for_rows(valid_rows=rows)
    assert len(items) == 1
    assert items[0]["name"] == "Lead User"
    assert items[0]["contact_id"] == "cid-1"


def test_build_lead_items_skips_no_lead() -> None:
    """Contacts without lead payload are omitted."""
    rows = [{"row_number": 1, "contact_model": _contact_model("a@x.com"), "contact_id": "c1"}]
    assert ContactsImportService._build_lead_items_for_rows(valid_rows=rows) == []


def test_build_lead_items_email_name() -> None:
    """Lead name falls back to email when names missing."""
    model = CreateContactRequest.model_validate(
        {
            "email": "only@x.com",
            "lead": {"stage_id": "550e8400-e29b-41d4-a716-446655440001"},
        }
    )
    rows = [{"row_number": 1, "contact_model": model, "contact_id": "c1"}]
    items = ContactsImportService._build_lead_items_for_rows(valid_rows=rows)
    assert items[0]["name"] == "only@x.com"


def test_remove_rows_filters_bad() -> None:
    """Bad row numbers are removed from both lists."""
    rows = [{"row_number": 1}, {"row_number": 2}]
    nums = [1, 2]
    filtered_rows, filtered_nums = ContactsImportService._remove_rows_by_row_numbers(
        valid_rows=rows,
        valid_row_numbers=nums,
        bad_rows={2},
    )
    assert [r["row_number"] for r in filtered_rows] == [1]
    assert filtered_nums == [1]


def test_remove_rows_keeps_all() -> None:
    """Empty bad set leaves rows unchanged."""
    rows = [{"row_number": 5}]
    nums = [5]
    out_rows, out_nums = ContactsImportService._remove_rows_by_row_numbers(
        valid_rows=rows,
        valid_row_numbers=nums,
        bad_rows=set(),
    )
    assert out_rows == rows
    assert out_nums == nums


# ---------------------------------------------------------------------------
# list_job_rows delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_job_rows_delegates(monkeypatch) -> None:
    """List job rows forwards pagination to rows repo."""
    _patch_repos(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    _FakeRowsRepo.configured_items = [{"row_number": 1, "status": "success"}]
    _FakeRowsRepo.configured_total = 1
    svc = ContactsImportService(db_connection=MagicMock())

    items, total = await svc.list_job_rows(
        job_id="imp_test",
        organization_id=ORG_ID,
        page=2,
        page_size=25,
    )

    assert total == 1
    assert items[0]["row_number"] == 1
    rows_repo = _FakeRowsRepo.last_instance
    assert rows_repo is not None
    assert rows_repo.list_rows_calls[0]["job_id"] == "internal-job-1"
    assert rows_repo.list_rows_calls[0]["page"] == 2


@pytest.mark.asyncio
async def test_list_job_rows_no_job(monkeypatch) -> None:
    """Missing job returns empty page without rows lookup."""
    _patch_repos(monkeypatch)
    _FakeJobsRepo.configured_job = None
    svc = ContactsImportService(db_connection=MagicMock())

    items, total = await svc.list_job_rows(job_id="imp_missing", organization_id=ORG_ID)

    assert items == []
    assert total == 0
    rows_repo = _FakeRowsRepo.last_instance
    assert rows_repo is not None
    assert not rows_repo.list_rows_calls


# ---------------------------------------------------------------------------
# process_job_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_event_skips_missing(monkeypatch) -> None:
    """Event for unknown job exits without status changes."""
    _patch_process_event_deps(monkeypatch)
    _FakeJobsRepo.configured_job = None
    svc = ContactsImportService(db_connection=MagicMock())

    await svc.process_job_event(event=_import_event())

    jobs_repo = _FakeJobsRepo.last_instance
    assert jobs_repo is not None
    assert not jobs_repo.set_status_ts_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        ContactsImportJobStatus.RUNNING.value,
        ContactsImportJobStatus.COMPLETED.value,
        ContactsImportJobStatus.FAILED.value,
    ],
)
async def test_process_event_skips_not_queued(monkeypatch, status: str) -> None:
    """Non-queued jobs are skipped by the consumer."""
    _patch_process_event_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job(status=status)
    svc = ContactsImportService(db_connection=MagicMock())

    await svc.process_job_event(event=_import_event())

    jobs_repo = _FakeJobsRepo.last_instance
    assert jobs_repo is not None
    assert not jobs_repo.set_status_ts_calls


@pytest.mark.asyncio
async def test_process_event_happy_path(monkeypatch) -> None:
    """Queued job runs batches and completes successfully."""
    _patch_process_event_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    svc = ContactsImportService(db_connection=MagicMock())

    async def _fake_batches(**kwargs) -> None:
        totals = kwargs["totals"]
        totals.processed_total = 3
        totals.success_total = 3

    monkeypatch.setattr(svc, "_process_event_batches", _fake_batches)

    await svc.process_job_event(event=_import_event())

    jobs_repo = _FakeJobsRepo.last_instance
    logs_repo = _FakeLogsRepo.last_instance
    assert jobs_repo is not None
    assert logs_repo is not None
    assert jobs_repo.set_status_ts_calls[0]["status"] == ContactsImportJobStatus.RUNNING.value
    assert jobs_repo.set_status_ts_calls[-1]["status"] == ContactsImportJobStatus.COMPLETED.value
    assert logs_repo.upsert_calls[0]["payload"]["phase"] == "started"
    assert logs_repo.upsert_calls[-1]["payload"]["phase"] == "finished"
    assert logs_repo.upsert_calls[-1]["payload"]["stats"]["success"] == 3


@pytest.mark.asyncio
async def test_process_event_marks_failed(monkeypatch) -> None:
    """Unhandled batch exception marks job failed and re-raises."""
    _patch_process_event_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    svc = ContactsImportService(db_connection=MagicMock())

    async def _boom(**kwargs) -> None:
        del kwargs
        raise RuntimeError("batch exploded")

    monkeypatch.setattr(svc, "_process_event_batches", _boom)

    with pytest.raises(RuntimeError, match="batch exploded"):
        await svc.process_job_event(event=_import_event())

    jobs_repo = _FakeJobsRepo.last_instance
    logs_repo = _FakeLogsRepo.last_instance
    assert jobs_repo is not None
    assert logs_repo is not None
    assert jobs_repo.set_status_ts_calls[-1]["status"] == ContactsImportJobStatus.FAILED.value
    assert logs_repo.upsert_calls[-1]["payload"]["phase"] == "failed"


@pytest.mark.asyncio
async def test_process_event_validation_errors(monkeypatch) -> None:
    """Validation error rows are persisted via row ledger."""
    _patch_process_event_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()

    async def _fake_iter(self, **kwargs):
        del kwargs
        yield [
            {
                "row_number": 1,
                "raw_row": {"email": ""},
                "error": {
                    "code": "missing_email_or_phone",
                    "message": "either email or phone number is required",
                },
                "contact_model": None,
            }
        ]

    monkeypatch.setattr(ContactsImportService, "_iter_validated_rows_for_ledger", _fake_iter)
    svc = ContactsImportService(db_connection=MagicMock())

    await svc.process_job_event(event=_import_event())

    rows_repo = _FakeRowsRepo.last_instance
    jobs_repo = _FakeJobsRepo.last_instance
    assert rows_repo is not None
    assert jobs_repo is not None
    assert rows_repo.mark_errors_calls
    assert rows_repo.mark_errors_calls[0]["errors"][0][1] == "missing_email_or_phone"
    assert jobs_repo.set_status_ts_calls[-1]["status"] == ContactsImportJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_process_event_in_file_dupes(monkeypatch) -> None:
    """Duplicate emails in one batch are marked as row errors."""
    _patch_process_event_deps(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job()
    model = _contact_model("dup@example.com")

    async def _fake_iter(self, **kwargs):
        del kwargs
        yield [
            {
                "row_number": 1,
                "raw_row": {"email": "dup@example.com"},
                "error": None,
                "contact_model": model,
            },
            {
                "row_number": 2,
                "raw_row": {"email": "dup@example.com"},
                "error": None,
                "contact_model": model,
            },
        ]

    monkeypatch.setattr(ContactsImportService, "_iter_validated_rows_for_ledger", _fake_iter)
    svc = ContactsImportService(db_connection=MagicMock())

    async def _noop_persist(**kwargs) -> None:
        del kwargs

    monkeypatch.setattr(svc, "_persist_contacts_for_rows", _noop_persist)
    monkeypatch.setattr(
        svc, "_provision_identities_sequential", AsyncMock(return_value={1: ("u", "i", None)})
    )

    await svc.process_job_event(event=_import_event())

    rows_repo = _FakeRowsRepo.last_instance
    assert rows_repo is not None
    dup_calls = [
        c for c in rows_repo.mark_errors_calls if c["errors"][0][1] == "duplicate_email_in_file"
    ]
    assert dup_calls


@pytest.mark.asyncio
async def test_download_csv_writes_tmp(monkeypatch) -> None:
    """CSV download streams bytes into a temp file."""
    csv_bytes = b"email\na@example.com\n"

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield csv_bytes

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

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())
    svc = ContactsImportService(db_connection=MagicMock())

    path = await svc._download_csv_to_tmp(file_url=FILE_URL)
    assert path is not None
    try:
        with open(path, "rb") as handle:
            assert handle.read() == csv_bytes
    finally:
        os.remove(path)


@pytest.mark.asyncio
async def test_retry_from_completed_requeues(monkeypatch) -> None:
    """Retry requeues even when job is already completed."""
    _patch_repos(monkeypatch)
    _FakeJobsRepo.configured_job = _queued_job(status=ContactsImportJobStatus.COMPLETED.value)
    svc = ContactsImportService(db_connection=MagicMock())

    result = await svc.retry_job_and_enqueue(
        job_id="imp_test",
        organization_id=ORG_ID,
        requested_by="user-1",
    )

    assert result is not None
    updated, payload = result
    assert updated["status"] == ContactsImportJobStatus.QUEUED.value
    assert payload["action"] == ContactsImportEventAction.RETRY.value
