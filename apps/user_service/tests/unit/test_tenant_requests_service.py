"""Unit tests for TenantRequestsService."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.enums import (
    TenantRequestDocumentStatus,
    TenantRequestDocumentType,
    TenantRequestEventType,
    TenantRequestListBucket,
    TenantRequestStatus,
)
from apps.user_service.app.schemas.tenant_requests import (
    ApproveTenantRequestRequest,
    CreateTenantRequestRequest,
    OwnerTenantRequestListQuery,
    RejectTenantDocumentRequest,
    ReuploadTenantDocumentRequest,
    TenantRequestDocumentInput,
    TenantRequestListQuery,
)
from apps.user_service.app.services.tenant_requests_service import TenantRequestsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
REQUEST_ID = "660e8400-e29b-41d4-a716-446655440001"
OWNER_ID = "770e8400-e29b-41d4-a716-446655440002"
UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
DOC_ID = "aa0e8400-e29b-41d4-a716-446655440001"
USER_ID = "bb0e8400-e29b-41d4-a716-446655440001"


def _ctx(*, user_id: str = USER_ID) -> UserContext:
    """Build user context for tenant request tests."""
    return UserContext(
        user_id=user_id,
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


def _request_row(**overrides: Any) -> dict[str, Any]:
    """Build a tenant request DB row."""
    row = {
        "id": REQUEST_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": UNIT_ID,
        "unit_code": "A-101",
        "unit_label": "A-101",
        "submitted_by_contact_id": OWNER_ID,
        "owner_prefix": None,
        "owner_first_name": "Owner",
        "owner_last_name": "One",
        "tenant_first_name": "Tenant",
        "tenant_last_name": "User",
        "tenant_phones": [
            {"phone_number": "9876543210", "phone_isd_code": "+91", "is_primary": True}
        ],
        "tenant_emails": [],
        "move_in_date": date(2026, 8, 1),
        "status": TenantRequestStatus.SUBMITTED.value,
        "portal_access": False,
        "tenant_contact_id": None,
        "contact_unit_id": None,
        "submitted_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "approved_at": None,
        "superseded_at": None,
        "cancelled_at": None,
        "admin_notes": None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _documents(
    *, doc_status: str = TenantRequestDocumentStatus.PENDING.value
) -> list[dict[str, Any]]:
    """Build three required tenant request documents."""
    types = [
        TenantRequestDocumentType.ID_PROOF.value,
        TenantRequestDocumentType.RENTAL_AGREEMENT.value,
        TenantRequestDocumentType.POLICE_VERIFICATION.value,
    ]
    return [
        {
            "id": f"doc-{index}",
            "document_type": doc_type,
            "file_path": f"/files/{doc_type}.pdf",
            "file_name": f"{doc_type}.pdf",
            "status": doc_status,
            "rejection_reason": None,
            "verified_at": None,
            "uploaded_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        for index, doc_type in enumerate(types, start=1)
    ]


def _create_body() -> CreateTenantRequestRequest:
    """Build a valid create tenant request payload."""
    return CreateTenantRequestRequest(
        unit_id=UNIT_ID,
        first_name="Tenant",
        last_name="User",
        phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
        emails=[Email(email="tenant@example.com", is_primary=True)],
        move_in_date=date(2026, 8, 1),
        portal_access=False,
        documents=[
            TenantRequestDocumentInput(
                document_type=TenantRequestDocumentType.ID_PROOF,
                file_path="/id.pdf",
                file_name="id.pdf",
            ),
            TenantRequestDocumentInput(
                document_type=TenantRequestDocumentType.RENTAL_AGREEMENT,
                file_path="/rent.pdf",
                file_name="rent.pdf",
            ),
            TenantRequestDocumentInput(
                document_type=TenantRequestDocumentType.POLICE_VERIFICATION,
                file_path="/police.pdf",
                file_name="police.pdf",
            ),
        ],
    )


class _FakeTenantRequestsRepo:
    """Configurable fake tenant requests repository."""

    def __init__(self) -> None:
        self.row = _request_row()
        self.documents = _documents()
        self.events: list[dict[str, Any]] = []
        self.summary = {
            "pending_review": 1,
            "awaiting_resubmission": 0,
            "ready_to_approve": 0,
            "approved": 0,
            "cancelled": 0,
            "superseded": 0,
        }
        self.list_rows = [_request_row()]
        self.list_total = 1
        self.insert_raises_unique = False
        self.verify_returns: dict[str, Any] | None = {"document_type": "id_proof"}
        self.reject_returns: dict[str, Any] | None = {"document_type": "id_proof"}
        self.reupload_returns: dict[str, Any] | None = {"document_type": "id_proof"}
        self.active_approved: dict[str, Any] | None = None

    async def insert_request(self, **kwargs):
        """Insert tenant request header."""
        del kwargs
        if self.insert_raises_unique:
            raise UniqueViolationError("duplicate")
        return {"id": REQUEST_ID}

    async def insert_document(self, **kwargs):
        """Insert tenant request document."""
        del kwargs

    async def insert_event(self, **kwargs):
        """Record timeline event."""
        self.events.append(
            {
                "id": f"event-{len(self.events) + 1}",
                "event_type": kwargs.get("event_type"),
                "occurred_at": datetime.now(timezone.utc),
                "payload": dict(kwargs.get("payload") or {}),
            }
        )

    async def get_request_by_id(self, *, organization_id: str, tenant_request_id: str):
        """Return configured request row."""
        del organization_id
        if tenant_request_id == "missing":
            return None
        return dict(self.row)

    async def list_documents(self, *, organization_id: str, tenant_request_id: str):
        """Return configured documents."""
        del organization_id, tenant_request_id
        return list(self.documents)

    async def list_events(self, *, organization_id: str, tenant_request_id: str):
        """Return configured events."""
        del organization_id, tenant_request_id
        return list(self.events)

    async def list_for_owner(self, **kwargs):
        """Return paginated owner requests."""
        del kwargs
        return list(self.list_rows), self.list_total

    async def list_for_admin(self, **kwargs):
        """Return paginated admin requests."""
        del kwargs
        return list(self.list_rows), self.list_total

    async def update_request_status(self, **kwargs):
        """Update request header status."""
        if "status" in kwargs:
            self.row["status"] = kwargs["status"]
        for key in (
            "cancelled_at",
            "approved_at",
            "superseded_at",
            "tenant_contact_id",
            "contact_unit_id",
            "admin_notes",
        ):
            if key in kwargs:
                self.row[key] = kwargs[key]

    async def get_document_by_id(
        self, *, organization_id: str, tenant_request_id: str, document_id: str
    ):
        """Return one document row."""
        del organization_id, tenant_request_id
        for doc in self.documents:
            if doc["id"] == document_id:
                return dict(doc)
        return None

    async def update_document_reupload(self, **kwargs):
        """Replace rejected document file."""
        del kwargs
        return self.reupload_returns

    async def verify_document(self, **kwargs):
        """Mark document verified."""
        del kwargs
        if self.verify_returns is None:
            return None
        for doc in self.documents:
            if doc["id"] == DOC_ID:
                doc["status"] = TenantRequestDocumentStatus.VERIFIED.value
        return self.verify_returns

    async def reject_document(self, **kwargs):
        """Mark document rejected."""
        del kwargs
        if self.reject_returns is None:
            return None
        for doc in self.documents:
            if doc["id"] == DOC_ID:
                doc["status"] = TenantRequestDocumentStatus.REJECTED.value
        return self.reject_returns

    async def get_summary_counts(self, *, organization_id: str):
        """Return dashboard counts."""
        del organization_id
        return dict(self.summary)

    async def find_active_approved_for_unit(self, *, organization_id: str, unit_id: str):
        """Return existing approved request for unit."""
        del organization_id, unit_id
        return self.active_approved


class _FakeContactUnitsRepo:
    """Configurable fake contact-units repository."""

    def __init__(self, *, is_owner: bool = True, unit: dict[str, Any] | None | bool = True) -> None:
        self.is_owner = is_owner
        if unit is True:
            self.unit: dict[str, Any] | None = {"project_id": PROJECT_ID, "id": UNIT_ID}
        else:
            self.unit = unit  # type: ignore[assignment]

    async def owner_has_active_unit(self, **kwargs):
        """Return configured owner access flag."""
        del kwargs
        return self.is_owner

    async def get_unit_project(self, **kwargs):
        """Return configured unit metadata."""
        del kwargs
        return self.unit if self.is_owner else None

    async def sync_move_out(self, **kwargs):
        """Record move-out sync."""
        del kwargs

    async def insert_primary_occupant_link(self, **kwargs):
        """Return new contact-unit link."""
        del kwargs
        return {"id": "link-1"}


def _service(
    *,
    repo: _FakeTenantRequestsRepo | None = None,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
) -> TenantRequestsService:
    """Build service with fake repositories."""
    return TenantRequestsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
        tenant_requests_repository=repo or _FakeTenantRequestsRepo(),
        contact_units_repository=contact_units_repo or _FakeContactUnitsRepo(),
    )


def test_derive_header_status_ready_when_all_verified() -> None:
    """All verified documents should mark the request ready to approve."""
    documents = [
        {"status": TenantRequestDocumentStatus.VERIFIED.value},
        {"status": TenantRequestDocumentStatus.VERIFIED.value},
        {"status": TenantRequestDocumentStatus.VERIFIED.value},
    ]
    assert (
        TenantRequestsService._derive_header_status(documents)
        == TenantRequestStatus.READY_TO_APPROVE.value
    )


def test_header_status_awaiting_resubmission() -> None:
    """Any rejected document should mark the request awaiting resubmission."""
    documents = [
        {"status": TenantRequestDocumentStatus.VERIFIED.value},
        {"status": TenantRequestDocumentStatus.REJECTED.value},
        {"status": TenantRequestDocumentStatus.PENDING.value},
    ]
    assert (
        TenantRequestsService._derive_header_status(documents)
        == TenantRequestStatus.AWAITING_RESUBMISSION.value
    )


def test_header_status_pending_review() -> None:
    """Pending documents without rejection should stay in review."""
    documents = [
        {"status": TenantRequestDocumentStatus.VERIFIED.value},
        {"status": TenantRequestDocumentStatus.PENDING.value},
        {"status": TenantRequestDocumentStatus.PENDING.value},
    ]
    assert (
        TenantRequestsService._derive_header_status(documents)
        == TenantRequestStatus.PENDING_REVIEW.value
    )


def test_derive_header_status_empty_documents() -> None:
    """Empty document list defaults to submitted."""
    assert TenantRequestsService._derive_header_status([]) == TenantRequestStatus.SUBMITTED.value


def test_format_date_values() -> None:
    """Date formatter handles None, date, and string values."""
    assert TenantRequestsService._format_date(None) is None
    assert TenantRequestsService._format_date(date(2026, 1, 2)) == "2026-01-02"
    assert TenantRequestsService._format_date("2026-01-02") == "2026-01-02"


def test_derive_milestones_approved() -> None:
    """Approved requests mark tenant_added milestone complete."""
    milestones = TenantRequestsService._derive_milestones(
        row=_request_row(status=TenantRequestStatus.APPROVED.value),
        events=[],
    )
    assert milestones[0].completed is True
    assert milestones[2].completed is True


def test_bucket_to_statuses() -> None:
    """Admin bucket filters map to underlying statuses."""
    assert TenantRequestsService._bucket_to_statuses(None) is None
    assert TenantRequestsService._bucket_to_statuses(TenantRequestListBucket.PENDING_REVIEW) == [
        TenantRequestStatus.SUBMITTED.value,
        TenantRequestStatus.PENDING_REVIEW.value,
    ]
    assert TenantRequestsService._bucket_to_statuses(TenantRequestListBucket.READY_TO_APPROVE) == [
        TenantRequestStatus.READY_TO_APPROVE.value
    ]


@pytest.mark.asyncio
async def test_get_request_or_raise_not_found() -> None:
    """Missing tenant request raises NotFoundException."""
    svc = _service()
    with pytest.raises(NotFoundException):
        await svc._get_request_or_raise(tenant_request_id="missing")


@pytest.mark.asyncio
async def test_assert_owner_access_denied() -> None:
    """Non-owner cannot submit tenant request for unit."""
    svc = _service(contact_units_repo=_FakeContactUnitsRepo(is_owner=False))
    with pytest.raises(ValidationException):
        await svc._assert_owner_access(owner_contact_id=OWNER_ID, unit_id=UNIT_ID)


@pytest.mark.asyncio
async def test_assert_owner_access_unit_not_found() -> None:
    """Missing unit metadata raises NotFoundException."""
    repo = _FakeContactUnitsRepo(is_owner=True, unit=None)
    svc = _service(contact_units_repo=repo)
    with pytest.raises(NotFoundException):
        await svc._assert_owner_access(owner_contact_id=OWNER_ID, unit_id=UNIT_ID)


@pytest.mark.asyncio
async def test_create_request_success() -> None:
    """Owner create inserts request, documents, and events."""
    repo = _FakeTenantRequestsRepo()
    svc = _service(repo=repo)

    response = await svc.create_request(owner_contact_id=OWNER_ID, body=_create_body())

    assert response.id == REQUEST_ID
    assert response.tenant_first_name == "Tenant"
    assert len(repo.events) == 2


@pytest.mark.asyncio
async def test_create_request_conflict() -> None:
    """Duplicate in-flight request raises ConflictException."""
    repo = _FakeTenantRequestsRepo()
    repo.insert_raises_unique = True
    svc = _service(repo=repo)

    with pytest.raises(ConflictException):
        await svc.create_request(owner_contact_id=OWNER_ID, body=_create_body())


@pytest.mark.asyncio
async def test_list_owner_requests() -> None:
    """Owner list returns serialized tenant requests."""
    svc = _service()
    items, total = await svc.list_owner_requests(
        owner_contact_id=OWNER_ID,
        query=OwnerTenantRequestListQuery(page=1, page_size=20),
    )
    assert total == 1
    assert items[0].id == REQUEST_ID


@pytest.mark.asyncio
async def test_get_owner_request_wrong_owner() -> None:
    """Owner cannot fetch another owner's request."""
    svc = _service()
    with pytest.raises(NotFoundException):
        await svc.get_owner_request(
            owner_contact_id="other-owner",
            tenant_request_id=REQUEST_ID,
        )


@pytest.mark.asyncio
async def test_get_owner_request_success() -> None:
    """Owner can fetch own tenant request."""
    svc = _service()
    response = await svc.get_owner_request(
        owner_contact_id=OWNER_ID,
        tenant_request_id=REQUEST_ID,
    )
    assert response.unit_id == UNIT_ID


@pytest.mark.asyncio
async def test_cancel_request_success() -> None:
    """Owner can cancel an in-flight request."""
    svc = _service()
    response = await svc.cancel_request(
        owner_contact_id=OWNER_ID,
        tenant_request_id=REQUEST_ID,
    )
    assert response.status == TenantRequestStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_request_invalid_status() -> None:
    """Approved requests cannot be cancelled."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.APPROVED.value)
    svc = _service(repo=repo)
    with pytest.raises(ValidationException):
        await svc.cancel_request(
            owner_contact_id=OWNER_ID,
            tenant_request_id=REQUEST_ID,
        )


@pytest.mark.asyncio
async def test_reupload_document_success() -> None:
    """Owner can reupload a rejected document."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.AWAITING_RESUBMISSION.value)
    repo.documents = _documents(doc_status=TenantRequestDocumentStatus.REJECTED.value)
    repo.documents[0]["id"] = DOC_ID
    svc = _service(repo=repo)

    response = await svc.reupload_document(
        owner_contact_id=OWNER_ID,
        tenant_request_id=REQUEST_ID,
        document_id=DOC_ID,
        body=ReuploadTenantDocumentRequest(file_path="/new.pdf", file_name="new.pdf"),
    )

    assert response.id == REQUEST_ID
    assert any(
        event.get("event_type") == TenantRequestEventType.RESUBMITTED.value for event in repo.events
    )


@pytest.mark.asyncio
async def test_reupload_document_not_rejected() -> None:
    """Only rejected documents can be reuploaded."""
    repo = _FakeTenantRequestsRepo()
    repo.documents[0]["id"] = DOC_ID
    svc = _service(repo=repo)
    with pytest.raises(ValidationException):
        await svc.reupload_document(
            owner_contact_id=OWNER_ID,
            tenant_request_id=REQUEST_ID,
            document_id=DOC_ID,
            body=ReuploadTenantDocumentRequest(file_path="/new.pdf"),
        )


@pytest.mark.asyncio
async def test_get_admin_summary() -> None:
    """Admin summary returns dashboard counts."""
    svc = _service()
    summary = await svc.get_admin_summary()
    assert summary.pending_review == 1


@pytest.mark.asyncio
async def test_list_admin_requests() -> None:
    """Admin list returns paginated requests."""
    svc = _service()
    items, total = await svc.list_admin_requests(
        TenantRequestListQuery(bucket=TenantRequestListBucket.PENDING_REVIEW)
    )
    assert total == 1
    assert items[0].status == TenantRequestStatus.SUBMITTED.value


@pytest.mark.asyncio
async def test_get_admin_request() -> None:
    """Admin can fetch one tenant request."""
    svc = _service()
    response = await svc.get_admin_request(REQUEST_ID)
    assert response.documents_total_count == 3


@pytest.mark.asyncio
async def test_verify_document_success() -> None:
    """Admin verify updates document and recomputes header status."""
    repo = _FakeTenantRequestsRepo()
    repo.documents = _documents(doc_status=TenantRequestDocumentStatus.VERIFIED.value)
    repo.documents[0]["id"] = DOC_ID
    repo.documents[1]["status"] = TenantRequestDocumentStatus.VERIFIED.value
    repo.documents[2]["status"] = TenantRequestDocumentStatus.VERIFIED.value
    svc = _service(repo=repo)

    response = await svc.verify_document(
        tenant_request_id=REQUEST_ID,
        document_id=DOC_ID,
    )

    assert response.status in {
        TenantRequestStatus.READY_TO_APPROVE.value,
        TenantRequestStatus.PENDING_REVIEW.value,
    }


@pytest.mark.asyncio
async def test_verify_document_not_found() -> None:
    """Missing document raises NotFoundException."""
    repo = _FakeTenantRequestsRepo()
    repo.verify_returns = None
    svc = _service(repo=repo)
    with pytest.raises(NotFoundException):
        await svc.verify_document(tenant_request_id=REQUEST_ID, document_id=DOC_ID)


@pytest.mark.asyncio
async def test_reject_document_success() -> None:
    """Admin reject marks document rejected and updates header."""
    repo = _FakeTenantRequestsRepo()
    repo.documents[0]["id"] = DOC_ID
    svc = _service(repo=repo)

    response = await svc.reject_document(
        tenant_request_id=REQUEST_ID,
        document_id=DOC_ID,
        body=RejectTenantDocumentRequest(rejection_reason="Blurry image"),
    )

    assert response.status == TenantRequestStatus.AWAITING_RESUBMISSION.value


@pytest.mark.asyncio
async def test_reupload_document_invalid_status() -> None:
    """Reupload blocked when request is not in resubmission flow."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.APPROVED.value)
    svc = _service(repo=repo)
    with pytest.raises(ValidationException):
        await svc.reupload_document(
            owner_contact_id=OWNER_ID,
            tenant_request_id=REQUEST_ID,
            document_id=DOC_ID,
            body=ReuploadTenantDocumentRequest(file_path="/new.pdf"),
        )


@pytest.mark.asyncio
async def test_reupload_document_not_found() -> None:
    """Missing document raises NotFoundException."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.AWAITING_RESUBMISSION.value)
    svc = _service(repo=repo)
    with pytest.raises(NotFoundException):
        await svc.reupload_document(
            owner_contact_id=OWNER_ID,
            tenant_request_id=REQUEST_ID,
            document_id="missing-doc",
            body=ReuploadTenantDocumentRequest(file_path="/new.pdf"),
        )


@pytest.mark.asyncio
async def test_reupload_document_update_failed() -> None:
    """Failed reupload update raises NotFoundException."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.AWAITING_RESUBMISSION.value)
    repo.documents = _documents(doc_status=TenantRequestDocumentStatus.REJECTED.value)
    repo.documents[0]["id"] = DOC_ID
    repo.reupload_returns = None
    svc = _service(repo=repo)
    with pytest.raises(NotFoundException):
        await svc.reupload_document(
            owner_contact_id=OWNER_ID,
            tenant_request_id=REQUEST_ID,
            document_id=DOC_ID,
            body=ReuploadTenantDocumentRequest(file_path="/new.pdf"),
        )


@pytest.mark.asyncio
async def test_verify_document_invalid_status() -> None:
    """Verify blocked for non in-flight requests."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.APPROVED.value)
    svc = _service(repo=repo)
    with pytest.raises(ValidationException):
        await svc.verify_document(tenant_request_id=REQUEST_ID, document_id=DOC_ID)


@pytest.mark.asyncio
async def test_reject_document_invalid_status() -> None:
    """Reject blocked for non in-flight requests."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.CANCELLED.value)
    svc = _service(repo=repo)
    with pytest.raises(ValidationException):
        await svc.reject_document(
            tenant_request_id=REQUEST_ID,
            document_id=DOC_ID,
            body=RejectTenantDocumentRequest(rejection_reason="Bad"),
        )


@pytest.mark.asyncio
async def test_reject_document_not_found() -> None:
    """Missing document on reject raises NotFoundException."""
    repo = _FakeTenantRequestsRepo()
    repo.reject_returns = None
    svc = _service(repo=repo)
    with pytest.raises(NotFoundException):
        await svc.reject_document(
            tenant_request_id=REQUEST_ID,
            document_id=DOC_ID,
            body=RejectTenantDocumentRequest(rejection_reason="Bad"),
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.tenant_requests_service.ContactsService")
async def test_approve_request_success(mock_contacts_cls: MagicMock) -> None:
    """Admin approve provisions tenant contact and links unit."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.READY_TO_APPROVE.value)
    repo.documents = _documents(doc_status=TenantRequestDocumentStatus.VERIFIED.value)
    mock_contacts_cls.return_value.create_contact = AsyncMock(
        return_value={"contact_id": "tenant-contact-1"}
    )
    svc = _service(repo=repo)

    response = await svc.approve_request(
        tenant_request_id=REQUEST_ID,
        body=ApproveTenantRequestRequest(admin_notes="Approved"),
    )

    assert response.status == TenantRequestStatus.APPROVED.value
    mock_contacts_cls.return_value.create_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_request_not_ready() -> None:
    """Only ready-to-approve requests can be approved."""
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.approve_request(
            tenant_request_id=REQUEST_ID,
            body=ApproveTenantRequestRequest(),
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.tenant_requests_service.ContactsService")
async def test_approve_request_supersedes_existing(mock_contacts_cls: MagicMock) -> None:
    """Approving supersedes an existing approved tenant on the same unit."""
    repo = _FakeTenantRequestsRepo()
    repo.row = _request_row(status=TenantRequestStatus.READY_TO_APPROVE.value)
    repo.active_approved = {
        "id": "old-request",
        "contact_unit_id": "old-link",
    }
    mock_contacts_cls.return_value.create_contact = AsyncMock(
        return_value={"contact_id": "tenant-contact-1"}
    )
    svc = _service(repo=repo)

    response = await svc.approve_request(
        tenant_request_id=REQUEST_ID,
        body=ApproveTenantRequestRequest(),
    )

    assert response.status == TenantRequestStatus.APPROVED.value
    assert any(
        event.get("event_type") == TenantRequestEventType.SUPERSEDED.value for event in repo.events
    )
