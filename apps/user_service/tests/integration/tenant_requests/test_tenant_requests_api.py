"""Integration tests for admin /v1/projects/{project_id}/tenant-requests endpoints."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.tenant_requests import (
    TenantRequestListItemResponse,
    TenantRequestResponse,
    TenantRequestSummaryResponse,
)
from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.tenant_requests"
_SERVICE = "apps.user_service.app.services.tenant_requests_service.TenantRequestsService"

PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
REQUEST_ID = "660e8400-e29b-41d4-a716-446655440001"
DOC_ID = "aa0e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
ORG = "org-123"


def _patch_admin_access(monkeypatch) -> None:
    """Bypass RBAC for admin tenant request routes."""
    patch_check_permissions(monkeypatch, _API, org_id=ORG)


def _fake_summary(**overrides) -> TenantRequestSummaryResponse:
    """Build admin dashboard summary response."""
    data = {
        "pending_review": 2,
        "awaiting_resubmission": 1,
        "ready_to_approve": 0,
        "approved_this_month": 3,
    }
    data.update(overrides)
    return TenantRequestSummaryResponse(**data)


def _fake_list_item(**overrides) -> TenantRequestListItemResponse:
    """Build admin list row response."""
    data = {
        "id": REQUEST_ID,
        "organization_id": ORG,
        "project_id": PROJECT_ID,
        "unit_id": UNIT_ID,
        "submitted_by_contact_id": "owner-1",
        "tenant_first_name": "Tenant",
        "tenant_last_name": "User",
        "status": "submitted",
    }
    data.update(overrides)
    return TenantRequestListItemResponse(**data)


def _fake_detail(**overrides) -> TenantRequestResponse:
    """Build tenant request detail response."""
    data = {
        "id": REQUEST_ID,
        "organization_id": ORG,
        "project_id": PROJECT_ID,
        "unit_id": UNIT_ID,
        "submitted_by_contact_id": "owner-1",
        "tenant_first_name": "Tenant",
        "tenant_last_name": "User",
        "status": "submitted",
    }
    data.update(overrides)
    return TenantRequestResponse(**data)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_tenant_request_summary(monkeypatch, client):
    """GET summary returns dashboard card counts."""

    _patch_admin_access(monkeypatch)

    async def fake_get_admin_summary(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return _fake_summary()

    monkeypatch.setattr(f"{_SERVICE}.get_admin_summary", fake_get_admin_summary)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/tenant-requests/summary")
    body = assert_success(res, 200)
    assert body["data"]["pending_review"] == 2
    assert body["data"]["approved_this_month"] == 3


@pytest.mark.asyncio
async def test_get_project_tenant_request_summary_not_found(monkeypatch, client):
    """GET summary returns 404 when project is missing."""

    _patch_admin_access(monkeypatch)

    async def fake_get_admin_summary(_self, *, project_id: str):
        del _self, project_id
        raise NotFoundException(
            message_key="project_setup.errors.project_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.get_admin_summary", fake_get_admin_summary)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/tenant-requests/summary")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_project_tenant_requests(monkeypatch, client):
    """GET list returns paginated tenant requests."""

    _patch_admin_access(monkeypatch)

    async def fake_list_admin_requests(_self, *, project_id: str, query):
        del _self
        assert project_id == PROJECT_ID
        assert query.page == 1
        assert query.page_size == 20
        return [_fake_list_item()], 1

    monkeypatch.setattr(f"{_SERVICE}.list_admin_requests", fake_list_admin_requests)

    res = await client.get(
        f"/v1/projects/{PROJECT_ID}/tenant-requests",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == REQUEST_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_project_tenant_requests_empty(monkeypatch, client):
    """GET list returns empty collection."""

    _patch_admin_access(monkeypatch)

    async def fake_list_admin_requests(_self, *, project_id: str, query):
        del _self, project_id, query
        return [], 0

    monkeypatch.setattr(f"{_SERVICE}.list_admin_requests", fake_list_admin_requests)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/tenant-requests")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_tenant_request(monkeypatch, client):
    """GET detail returns one tenant request."""

    _patch_admin_access(monkeypatch)

    async def fake_get_admin_request(_self, *, project_id: str, tenant_request_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert tenant_request_id == REQUEST_ID
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.get_admin_request", fake_get_admin_request)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == REQUEST_ID
    assert body["data"]["tenant_first_name"] == "Tenant"


@pytest.mark.asyncio
async def test_get_project_tenant_request_not_found(monkeypatch, client):
    """GET detail returns 404 when request is missing."""

    _patch_admin_access(monkeypatch)

    async def fake_get_admin_request(_self, *, project_id: str, tenant_request_id: str):
        del _self, project_id, tenant_request_id
        raise NotFoundException(
            message_key="tenant_requests.errors.request_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.get_admin_request", fake_get_admin_request)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Verify document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_tenant_document(monkeypatch, client):
    """POST verify marks a document as verified."""

    _patch_admin_access(monkeypatch)

    async def fake_verify_document(
        _self,
        *,
        project_id: str,
        tenant_request_id: str,
        document_id: str,
    ):
        del _self
        assert project_id == PROJECT_ID
        assert tenant_request_id == REQUEST_ID
        assert document_id == DOC_ID
        return _fake_detail(status="documents_under_review")

    monkeypatch.setattr(f"{_SERVICE}.verify_document", fake_verify_document)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}/documents/{DOC_ID}/verify",
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "documents_under_review"


@pytest.mark.asyncio
async def test_verify_tenant_document_not_found(monkeypatch, client):
    """POST verify returns 404 when document is missing."""

    _patch_admin_access(monkeypatch)

    async def fake_verify_document(_self, **kwargs):
        del _self, kwargs
        raise NotFoundException(
            message_key="tenant_requests.errors.document_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.verify_document", fake_verify_document)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}/documents/{DOC_ID}/verify",
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Reject document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_tenant_document(monkeypatch, client):
    """POST reject marks a document as rejected with reason."""

    _patch_admin_access(monkeypatch)

    async def fake_reject_document(
        _self,
        *,
        project_id: str,
        tenant_request_id: str,
        document_id: str,
        body,
    ):
        del _self
        assert project_id == PROJECT_ID
        assert tenant_request_id == REQUEST_ID
        assert document_id == DOC_ID
        assert body.rejection_reason == "Blurry scan"
        return _fake_detail(status="awaiting_resubmission")

    monkeypatch.setattr(f"{_SERVICE}.reject_document", fake_reject_document)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}/documents/{DOC_ID}/reject",
        json={"rejection_reason": "Blurry scan"},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "awaiting_resubmission"


@pytest.mark.asyncio
async def test_reject_tenant_document_validation_error(monkeypatch, client):
    """POST reject returns 422 when rejection reason is missing."""

    _patch_admin_access(monkeypatch)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}/documents/{DOC_ID}/reject",
        json={},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Approve request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_tenant_request(monkeypatch, client):
    """POST approve provisions tenant and updates request status."""

    _patch_admin_access(monkeypatch)

    async def fake_approve_request(
        _self,
        *,
        project_id: str,
        tenant_request_id: str,
        body,
    ):
        del _self
        assert project_id == PROJECT_ID
        assert tenant_request_id == REQUEST_ID
        assert body.move_in_date.isoformat() == "2026-08-01"
        return _fake_detail(status="approved", admin_notes="Approved")

    monkeypatch.setattr(f"{_SERVICE}.approve_request", fake_approve_request)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}/approve",
        json={"move_in_date": "2026-08-01", "admin_notes": "Approved"},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_tenant_request_not_ready(monkeypatch, client):
    """POST approve returns 422 when request is not ready."""

    _patch_admin_access(monkeypatch)

    async def fake_approve_request(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="tenant_requests.errors.not_ready_to_approve",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.approve_request", fake_approve_request)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/tenant-requests/{REQUEST_ID}/approve",
        json={"move_in_date": "2026-08-01"},
    )
    assert res.status_code == 422
