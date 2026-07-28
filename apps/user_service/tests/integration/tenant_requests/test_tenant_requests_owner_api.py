"""Integration tests for owner /v1/contact-onboarding/tenant-requests endpoints."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.tenant_requests import (
    TenantRequestListItemResponse,
    TenantRequestResponse,
)
from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.tenant_requests_owner"
_SERVICE = "apps.user_service.app.services.tenant_requests_service.TenantRequestsService"

CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
REQUEST_ID = "660e8400-e29b-41d4-a716-446655440001"
DOC_ID = "aa0e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"

_CREATE_BODY = {
    "unit_id": UNIT_ID,
    "first_name": "Tenant",
    "last_name": "User",
    "phones": [{"phone_number": "9876543210", "phone_isd_code": "+91", "is_primary": True}],
    "emails": [{"email": "tenant@example.com", "is_primary": True}],
    "move_in_date": "2026-08-01",
    "portal_access": False,
    "documents": [
        {
            "document_type": "id_proof",
            "file_path": "/id.pdf",
            "file_name": "id.pdf",
        },
        {
            "document_type": "rental_agreement",
            "file_path": "/rent.pdf",
            "file_name": "rent.pdf",
        },
        {
            "document_type": "police_verification",
            "file_path": "/police.pdf",
            "file_name": "police.pdf",
        },
    ],
}


def _patch_owner_context(monkeypatch) -> None:
    """Patch onboarding contact context for owner tenant request routes."""

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123"), {
            "id": CONTACT_ID,
            "contact_type": "owner",
        }

    monkeypatch.setattr(
        f"{_API}.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


def _fake_list_item(**overrides) -> TenantRequestListItemResponse:
    """Build owner list row response."""
    data = {
        "id": REQUEST_ID,
        "organization_id": "org-123",
        "project_id": "proj-1",
        "unit_id": UNIT_ID,
        "submitted_by_contact_id": CONTACT_ID,
        "tenant_first_name": "Tenant",
        "tenant_last_name": "User",
        "status": "submitted",
    }
    data.update(overrides)
    return TenantRequestListItemResponse(**data)


def _fake_detail(**overrides) -> TenantRequestResponse:
    """Build owner detail response."""
    data = {
        "id": REQUEST_ID,
        "organization_id": "org-123",
        "project_id": "proj-1",
        "unit_id": UNIT_ID,
        "submitted_by_contact_id": CONTACT_ID,
        "tenant_first_name": "Tenant",
        "tenant_last_name": "User",
        "status": "submitted",
    }
    data.update(overrides)
    return TenantRequestResponse(**data)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_owner_tenant_requests(monkeypatch, client):
    """GET list returns paginated owner tenant requests."""

    _patch_owner_context(monkeypatch)

    async def fake_list_owner_requests(_self, *, owner_contact_id: str, query):
        del _self
        assert owner_contact_id == CONTACT_ID
        assert query.page == 1
        return [_fake_list_item()], 1

    monkeypatch.setattr(f"{_SERVICE}.list_owner_requests", fake_list_owner_requests)

    res = await client.get(
        "/v1/contact-onboarding/tenant-requests",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == REQUEST_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_owner_tenant_requests_empty(monkeypatch, client):
    """GET list returns empty collection for owner."""

    _patch_owner_context(monkeypatch)

    async def fake_list_owner_requests(_self, *, owner_contact_id: str, query):
        del _self, owner_contact_id, query
        return [], 0

    monkeypatch.setattr(f"{_SERVICE}.list_owner_requests", fake_list_owner_requests)

    res = await client.get("/v1/contact-onboarding/tenant-requests")
    body = assert_success(res, 200)
    assert body["data"] == []


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tenant_request(monkeypatch, client):
    """POST create submits a tenant request with documents."""

    _patch_owner_context(monkeypatch)

    async def fake_create_request(_self, *, owner_contact_id: str, body):
        del _self
        assert owner_contact_id == CONTACT_ID
        assert body.unit_id == UNIT_ID
        assert body.first_name == "Tenant"
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.create_request", fake_create_request)

    res = await client.post("/v1/contact-onboarding/tenant-requests", json=_CREATE_BODY)
    body = assert_success(res, 201)
    assert body["data"]["id"] == REQUEST_ID


@pytest.mark.asyncio
async def test_create_tenant_request_conflict(monkeypatch, client):
    """POST create returns 409 when an in-flight request exists."""

    _patch_owner_context(monkeypatch)

    async def fake_create_request(_self, *, owner_contact_id: str, body):
        del _self, owner_contact_id, body
        raise ConflictException(
            message_key="tenant_requests.errors.inflight_request_exists",
            custom_code=CustomStatusCode.CONFLICT,
        )

    monkeypatch.setattr(f"{_SERVICE}.create_request", fake_create_request)

    res = await client.post("/v1/contact-onboarding/tenant-requests", json=_CREATE_BODY)
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_owner_tenant_request(monkeypatch, client):
    """GET detail returns one owner tenant request."""

    _patch_owner_context(monkeypatch)

    async def fake_get_owner_request(_self, *, owner_contact_id: str, tenant_request_id: str):
        del _self
        assert owner_contact_id == CONTACT_ID
        assert tenant_request_id == REQUEST_ID
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.get_owner_request", fake_get_owner_request)

    res = await client.get(f"/v1/contact-onboarding/tenant-requests/{REQUEST_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == REQUEST_ID


@pytest.mark.asyncio
async def test_get_owner_tenant_request_not_found(monkeypatch, client):
    """GET detail returns 404 when request is missing."""

    _patch_owner_context(monkeypatch)

    async def fake_get_owner_request(_self, *, owner_contact_id: str, tenant_request_id: str):
        del _self, owner_contact_id, tenant_request_id
        raise NotFoundException(
            message_key="tenant_requests.errors.request_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.get_owner_request", fake_get_owner_request)

    res = await client.get(f"/v1/contact-onboarding/tenant-requests/{REQUEST_ID}")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_tenant_request(monkeypatch, client):
    """POST cancel cancels an in-flight tenant request."""

    _patch_owner_context(monkeypatch)

    async def fake_cancel_request(_self, *, owner_contact_id: str, tenant_request_id: str):
        del _self
        assert owner_contact_id == CONTACT_ID
        assert tenant_request_id == REQUEST_ID
        return _fake_detail(status="cancelled")

    monkeypatch.setattr(f"{_SERVICE}.cancel_request", fake_cancel_request)

    res = await client.post(f"/v1/contact-onboarding/tenant-requests/{REQUEST_ID}/cancel")
    body = assert_success(res, 200)
    assert body["data"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_tenant_request_invalid_status(monkeypatch, client):
    """POST cancel returns 422 for invalid status transition."""

    _patch_owner_context(monkeypatch)

    async def fake_cancel_request(_self, *, owner_contact_id: str, tenant_request_id: str):
        del _self, owner_contact_id, tenant_request_id
        raise ValidationException(
            message_key="tenant_requests.errors.invalid_status_transition",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.cancel_request", fake_cancel_request)

    res = await client.post(f"/v1/contact-onboarding/tenant-requests/{REQUEST_ID}/cancel")
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Re-upload document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reupload_tenant_document(monkeypatch, client):
    """PATCH re-upload replaces a rejected document file."""

    _patch_owner_context(monkeypatch)

    async def fake_reupload_document(
        _self,
        *,
        owner_contact_id: str,
        tenant_request_id: str,
        document_id: str,
        body,
    ):
        del _self
        assert owner_contact_id == CONTACT_ID
        assert tenant_request_id == REQUEST_ID
        assert document_id == DOC_ID
        assert body.file_path == "/new-id.pdf"
        return _fake_detail(status="submitted")

    monkeypatch.setattr(f"{_SERVICE}.reupload_document", fake_reupload_document)

    res = await client.patch(
        f"/v1/contact-onboarding/tenant-requests/{REQUEST_ID}/documents/{DOC_ID}",
        json={"file_path": "/new-id.pdf", "file_name": "new-id.pdf"},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "submitted"


@pytest.mark.asyncio
async def test_reupload_tenant_document_not_rejected(monkeypatch, client):
    """PATCH re-upload returns 422 when document is not rejected."""

    _patch_owner_context(monkeypatch)

    async def fake_reupload_document(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="tenant_requests.errors.document_not_rejected",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.reupload_document", fake_reupload_document)

    res = await client.patch(
        f"/v1/contact-onboarding/tenant-requests/{REQUEST_ID}/documents/{DOC_ID}",
        json={"file_path": "/new-id.pdf"},
    )
    assert res.status_code == 422
