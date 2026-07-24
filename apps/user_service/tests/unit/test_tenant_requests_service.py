"""Unit tests for tenant request status derivation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.mark.asyncio
async def test_serialize_detail_parses_json_string_fields() -> None:
    """JSONB columns returned as strings must not break response serialization."""
    service = TenantRequestsService(db_connection=MagicMock(), user_context=MagicMock())
    service.user_context.organization_id = "org-1"
    service.repo = AsyncMock()
    service.repo.list_documents.return_value = []
    service.repo.list_events.return_value = [
        {
            "id": "event-1",
            "event_type": "created",
            "occurred_at": "2026-07-24T05:30:00+00:00",
            "payload": "{}",
        }
    ]

    result = await service._serialize_detail(
        {
            "id": "req-1",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "unit_id": "unit-1",
            "submitted_by_contact_id": "owner-1",
            "tenant_first_name": "Vatsal",
            "tenant_last_name": "Savaliya",
            "tenant_phones": (
                '[{"phone_number": "9967887657", "phone_isd_code": "+91", "is_primary": true}]'
            ),
            "tenant_emails": '[{"email": "vatsal@example.com", "is_primary": true}]',
            "status": TenantRequestStatus.SUBMITTED.value,
            "portal_access": False,
            "submitted_at": "2026-07-24T05:30:00+00:00",
            "created_at": "2026-07-24T05:30:00+00:00",
            "updated_at": "2026-07-24T05:30:00+00:00",
        }
    )

    assert result.events[0].payload == {}
    assert result.tenant_phones[0]["phone_number"] == "9967887657"
    assert result.tenant_emails[0]["email"] == "vatsal@example.com"


def test_serialize_list_item_includes_owner_and_unit() -> None:
    """Admin list rows include nested owner and unit summaries."""
    service = TenantRequestsService(db_connection=MagicMock(), user_context=MagicMock())

    item = service._serialize_list_item(
        {
            "id": "req-1",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "unit_id": "unit-1",
            "submitted_by_contact_id": "owner-1",
            "tenant_first_name": "Vatsal",
            "tenant_last_name": "Savaliya",
            "tenant_phones": [],
            "tenant_emails": [],
            "status": TenantRequestStatus.SUBMITTED.value,
            "portal_access": False,
            "submitted_at": "2026-07-24T05:30:00+00:00",
            "created_at": "2026-07-24T05:30:00+00:00",
            "updated_at": "2026-07-24T05:30:00+00:00",
            "documents_verified_count": 1,
            "documents_total_count": 3,
            "owner_contact_id": "owner-1",
            "owner_prefix": "Mr.",
            "owner_first_name": "Raj",
            "owner_last_name": "Kumar",
            "owner_phones": [
                {
                    "phone_isd_code": "+91",
                    "phone_number": "9876543210",
                    "is_primary": True,
                }
            ],
            "owner_emails": [{"email": "raj@example.com", "is_primary": True}],
            "owner_profile_photo_url": "https://cdn.example.com/raj.jpg",
            "unit_code": "A-1802",
            "unit_label": None,
            "unit_status": "occupied",
            "unit_tower_id": "tower-1",
            "unit_config_id": "cfg-1",
            "unit_plot_item_id": None,
            "unit_sort_order": 1,
            "unit_tower_name": "Tower A",
            "unit_tower_type": "residential",
            "unit_floor_display_name": "F18",
            "unit_floor_level_number": 18,
            "unit_config_kind": "apartment",
            "unit_config_display_label": "2BHK Standard",
            "unit_config_name": "2BHK Standard",
            "unit_plot_description": None,
            "unit_resolved_property_type": "residential",
            "unit_resolved_config_kind": "apartment",
        }
    )

    assert item.owner is not None
    assert item.owner.contact_id == "owner-1"
    assert item.owner.display_name == "Mr. Raj Kumar"
    assert item.owner.phone == "+919876543210"
    assert item.unit is not None
    assert item.unit.code == "A-1802"
    assert item.unit.location_label == "Tower A · F18"
    assert item.documents_verified_count == 1
