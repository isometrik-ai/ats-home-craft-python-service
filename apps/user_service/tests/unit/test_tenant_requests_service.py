"""Unit tests for tenant request status derivation."""

from apps.user_service.app.schemas.enums import (
    TenantRequestDocumentStatus,
    TenantRequestStatus,
)
from apps.user_service.app.services.tenant_requests_service import TenantRequestsService


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
