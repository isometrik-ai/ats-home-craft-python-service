"""End-to-end tests for vehicle soft-remove and removed_by attribution."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import VehicleStatus
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.integration.helpers import (
    admin_context,
    patch_staff_project_access_wrapper,
)
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

PROJECT_ID = "880e8400-e29b-41d4-a716-446655440003"
VEHICLE_ID = "660e8400-e29b-41d4-a716-446655440001"
CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
UNIT_ID = "110e8400-e29b-41d4-a716-446655440001"
ADMIN_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
ORG_ID = "org-123"

_PROJECTS_API = "apps.user_service.app.api.projects"
_ONBOARDING_API = "apps.user_service.app.api.contact_onboarding"

_APPROVED_VEHICLE = {
    "id": VEHICLE_ID,
    "organization_id": ORG_ID,
    "project_id": PROJECT_ID,
    "contact_id": CONTACT_ID,
    "unit_id": UNIT_ID,
    "vehicle_type": "four_wheeler",
    "registration_number": "MH56AS8636",
    "photo_paths": [],
    "status": VehicleStatus.APPROVED.value,
    "parking_slot_id": "slot-1",
    "status_updated_at": "2026-09-11T05:20:41.734698+00:00",
    "created_at": "2026-09-02T11:16:38.512064+00:00",
    "updated_at": "2026-09-11T05:20:41.734698+00:00",
    "sort_order": 0,
}

_REMOVED_BY_ADMIN_ROW = {
    **_APPROVED_VEHICLE,
    "status": VehicleStatus.REMOVED.value,
    "parking_slot_id": None,
    "rejection_reason": "No longer associated with unit",
    "removed_by_user_id": ADMIN_USER_ID,
    "removed_by_contact_id": None,
    "removed_by_salutation": "Mr.",
    "removed_by_first_name": "ATS",
    "removed_by_last_name": "HomeKraft",
    "removed_by_email": "tech@homecraft.app",
    "removed_by_phone_isd_code": "+91",
    "removed_by_phone_number": "9876543212",
    "removed_by_avatar_url": "https://cdn.example.com/admins/ats.jpg",
}

_REMOVED_BY_CONTACT_ROW = {
    **_APPROVED_VEHICLE,
    "status": VehicleStatus.REMOVED.value,
    "parking_slot_id": None,
    "rejection_reason": "Sold vehicle",
    "removed_by_user_id": None,
    "removed_by_contact_id": CONTACT_ID,
    "removed_by_contact_prefix": "Ms.",
    "removed_by_contact_first_name": "Jiva",
    "removed_by_contact_last_name": "T",
    "removed_by_contact_emails": [{"email": "jiva@yopmail.com", "is_primary": True}],
    "removed_by_contact_phones": [
        {"phone_isd_code": "+91", "phone_number": "9867895540", "is_primary": True}
    ],
    "removed_by_contact_profile_photo_url": "https://cdn.example.com/residents/jiva.jpg",
}


def _vehicles_service(*, user_id: str = ADMIN_USER_ID) -> VehiclesService:
    """Build VehiclesService with mocked repositories."""
    user_context = UserContext(
        user_id=user_id,
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )
    svc = VehiclesService(db_connection=MagicMock(), user_context=user_context)
    svc.repo = AsyncMock()
    svc.parking_slots_repo = AsyncMock()
    svc.parking_allotment_repo = AsyncMock()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.owner_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(
        return_value={"project_id": PROJECT_ID, "unit_id": UNIT_ID}
    )
    return svc


def _patch_projects_access(monkeypatch) -> None:
    patch_staff_project_access_wrapper(monkeypatch, _PROJECTS_API, org_id=ORG_ID)


def _patch_contact_context(monkeypatch) -> None:
    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id=ORG_ID), {
            "id": CONTACT_ID,
            "roles": [{"role_type": "Owner", "status": "active"}],
        }

    monkeypatch.setattr(
        f"{_ONBOARDING_API}.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


# ---------------------------------------------------------------------------
# Service-layer E2E (real VehiclesService, mocked persistence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_admin_remove_records_removed_by_user():
    """Admin soft-remove persists removed_by_user_id and returns normalized row."""
    svc = _vehicles_service()
    svc.repo.get_by_id.return_value = _APPROVED_VEHICLE
    svc.repo.soft_remove.return_value = {
        **_APPROVED_VEHICLE,
        "status": VehicleStatus.REMOVED.value,
        "removed_by_user_id": ADMIN_USER_ID,
        "rejection_reason": "Invalid registration",
    }

    result = await svc.admin_delete_vehicle(
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        rejection_reason="Invalid registration",
    )

    svc.repo.soft_remove.assert_awaited_once_with(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        rejection_reason="Invalid registration",
        removed_by_user_id=ADMIN_USER_ID,
        removed_by_contact_id=None,
    )
    assert result["status"] == VehicleStatus.REMOVED.value
    assert result["removed_by_user_id"] == ADMIN_USER_ID


@pytest.mark.asyncio
async def test_service_resident_remove_records_removed_by_contact():
    """Resident soft-remove persists removed_by_contact_id."""
    svc = _vehicles_service(user_id=CONTACT_ID)
    svc.repo.get_by_id.return_value = _APPROVED_VEHICLE
    svc.repo.soft_remove.return_value = {
        **_APPROVED_VEHICLE,
        "status": VehicleStatus.REMOVED.value,
        "removed_by_contact_id": CONTACT_ID,
    }

    await svc.remove_vehicle(
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        removed_by_contact_id=CONTACT_ID,
    )

    svc.repo.soft_remove.assert_awaited_once_with(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        vehicle_id=VEHICLE_ID,
        rejection_reason=None,
        removed_by_user_id=None,
        removed_by_contact_id=CONTACT_ID,
    )


@pytest.mark.asyncio
async def test_service_list_removed_includes_removed_by_admin_summary():
    """Admin list serializes nested removed_by for org-member removals."""
    svc = _vehicles_service()
    svc.repo.list_by_project.return_value = [_REMOVED_BY_ADMIN_ROW]

    items = await svc.list_project_vehicles(
        project_id=PROJECT_ID,
        status=VehicleStatus.REMOVED,
    )

    assert items[0]["status"] == VehicleStatus.REMOVED.value
    assert items[0]["removed_by"]["user_id"] == ADMIN_USER_ID
    assert items[0]["removed_by"]["display_name"] == "Mr. ATS HomeKraft"
    assert items[0]["removed_by"]["email"] == "tech@homecraft.app"
    assert items[0]["removed_by"]["phone"] == "+919876543212"
    assert items[0]["rejected_by"] is None


@pytest.mark.asyncio
async def test_service_list_removed_includes_removed_by_contact_summary():
    """Admin list serializes nested removed_by for resident removals."""
    svc = _vehicles_service()
    svc.repo.list_by_project.return_value = [_REMOVED_BY_CONTACT_ROW]

    items = await svc.list_project_vehicles(
        project_id=PROJECT_ID,
        status=VehicleStatus.REMOVED,
    )

    assert items[0]["removed_by"]["contact_id"] == CONTACT_ID
    assert items[0]["removed_by"]["display_name"] == "Ms. Jiva T"
    assert items[0]["removed_by"]["email"] == "jiva@yopmail.com"
    assert items[0]["removed_by"]["phone"] == "+919867895540"
    assert items[0]["removed_by"]["avatar_url"] == "https://cdn.example.com/residents/jiva.jpg"


@pytest.mark.asyncio
async def test_service_list_removed_without_actor_has_null_removed_by():
    """Legacy removed rows without attribution return removed_by null."""
    svc = _vehicles_service()
    svc.repo.list_by_project.return_value = [
        {
            **_APPROVED_VEHICLE,
            "status": VehicleStatus.REMOVED.value,
            "removed_by_user_id": None,
            "removed_by_contact_id": None,
        }
    ]

    items = await svc.list_project_vehicles(
        project_id=PROJECT_ID,
        status=VehicleStatus.REMOVED,
    )

    assert items[0]["removed_by"] is None


@pytest.mark.asyncio
async def test_service_remove_pending_vehicle_fails():
    """Pending vehicles cannot be soft-removed."""
    svc = _vehicles_service()
    svc.repo.get_by_id.return_value = {
        **_APPROVED_VEHICLE,
        "status": VehicleStatus.PENDING.value,
    }

    with pytest.raises(ValidationException):
        await svc.remove_vehicle(contact_id=CONTACT_ID, vehicle_id=VEHICLE_ID)


@pytest.mark.asyncio
async def test_service_remove_rejected_vehicle_fails():
    """Rejected vehicles cannot be soft-removed."""
    svc = _vehicles_service()
    svc.repo.get_by_id.return_value = {
        **_APPROVED_VEHICLE,
        "status": VehicleStatus.REJECTED.value,
    }

    with pytest.raises(ValidationException):
        await svc.remove_vehicle(contact_id=CONTACT_ID, vehicle_id=VEHICLE_ID)


@pytest.mark.asyncio
async def test_service_admin_remove_missing_vehicle_fails():
    """Admin remove returns not found when vehicle row is missing."""
    svc = _vehicles_service()
    svc.repo.get_by_id.return_value = None

    with pytest.raises(NotFoundException):
        await svc.admin_delete_vehicle(
            contact_id=CONTACT_ID,
            vehicle_id=VEHICLE_ID,
            rejection_reason="Invalid registration",
        )


# ---------------------------------------------------------------------------
# API-layer E2E (HTTP client → route → service)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_admin_delete_vehicle_success_returns_removed_by(monkeypatch, client):
    """DELETE project vehicle returns removed status with removed_by."""
    _patch_projects_access(monkeypatch)

    async def fake_admin_delete_project_vehicle(
        _self, *, project_id: str, vehicle_id: str, rejection_reason: str
    ):
        del _self
        assert project_id == PROJECT_ID
        assert vehicle_id == VEHICLE_ID
        return {
            **_APPROVED_VEHICLE,
            "status": VehicleStatus.REMOVED.value,
            "rejection_reason": rejection_reason,
            "removed_by_user_id": ADMIN_USER_ID,
            "removed_by_contact_id": None,
            "removed_by": {
                "user_id": ADMIN_USER_ID,
                "contact_id": None,
                "display_name": "Mr. ATS HomeKraft",
                "email": "tech@homecraft.app",
                "phone": "+919876543212",
                "avatar_url": "https://cdn.example.com/admins/ats.jpg",
            },
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.admin_delete_project_vehicle",
        fake_admin_delete_project_vehicle,
    )

    res = await client.request(
        "DELETE",
        f"/v1/projects/{PROJECT_ID}/vehicles/{VEHICLE_ID}",
        json={"rejection_reason": "No longer associated with unit"},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == VehicleStatus.REMOVED.value
    assert body["data"]["removed_by"]["user_id"] == ADMIN_USER_ID
    assert body["data"]["removed_by"]["display_name"] == "Mr. ATS HomeKraft"


@pytest.mark.asyncio
async def test_api_list_removed_vehicle_requests_includes_removed_by(monkeypatch, client):
    """GET vehicle-requests?status=removed includes removed_by summary."""
    _patch_projects_access(monkeypatch)

    async def fake_list_project_vehicles(_self, *, project_id: str, status=None, **kwargs):
        del _self, kwargs
        assert project_id == PROJECT_ID
        assert status == VehicleStatus.REMOVED
        return [
            {
                **_APPROVED_VEHICLE,
                "status": VehicleStatus.REMOVED.value,
                "rejection_reason": "Duplicate registration",
                "removed_by_user_id": ADMIN_USER_ID,
                "removed_by": {
                    "user_id": ADMIN_USER_ID,
                    "contact_id": None,
                    "display_name": "Mr. ATS HomeKraft",
                    "email": "tech@homecraft.app",
                    "phone": "+919876543212",
                    "avatar_url": None,
                },
            }
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.list_project_vehicles",
        fake_list_project_vehicles,
    )

    res = await client.get(
        f"/v1/projects/{PROJECT_ID}/vehicle-requests",
        params={"status": "removed"},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["status"] == VehicleStatus.REMOVED.value
    assert body["data"][0]["removed_by"]["display_name"] == "Mr. ATS HomeKraft"
    assert body["data"][0].get("rejected_by") is None


@pytest.mark.asyncio
async def test_api_resident_remove_vehicle_passes_removed_by_contact(monkeypatch, client):
    """DELETE contact vehicle passes removed_by_contact_id to service."""
    _patch_contact_context(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_remove_vehicle(
        _self,
        *,
        contact_id: str,
        vehicle_id: str,
        removed_by_contact_id: str | None = None,
        **kwargs,
    ):
        del _self, kwargs
        captured["contact_id"] = contact_id
        captured["vehicle_id"] = vehicle_id
        captured["removed_by_contact_id"] = removed_by_contact_id
        return {
            **_APPROVED_VEHICLE,
            "status": VehicleStatus.REMOVED.value,
            "removed_by_contact_id": CONTACT_ID,
            "removed_by_user_id": None,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.remove_vehicle",
        fake_remove_vehicle,
    )

    res = await client.delete(f"/v1/contact-onboarding/vehicles/{VEHICLE_ID}")
    body = assert_success(res, 200)
    assert captured["contact_id"] == CONTACT_ID
    assert captured["vehicle_id"] == VEHICLE_ID
    assert captured["removed_by_contact_id"] == CONTACT_ID
    assert body["data"]["status"] == VehicleStatus.REMOVED.value


@pytest.mark.asyncio
async def test_api_admin_delete_vehicle_missing_reason_fails(client):
    """DELETE project vehicle requires rejection_reason."""
    res = await client.request(
        "DELETE",
        f"/v1/projects/{PROJECT_ID}/vehicles/{VEHICLE_ID}",
        json={},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_api_admin_delete_vehicle_empty_reason_fails(client):
    """DELETE project vehicle rejects empty rejection_reason."""
    res = await client.request(
        "DELETE",
        f"/v1/projects/{PROJECT_ID}/vehicles/{VEHICLE_ID}",
        json={"rejection_reason": ""},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_api_resident_remove_pending_vehicle_fails(monkeypatch, client):
    """DELETE contact vehicle returns 422 when vehicle is pending."""
    _patch_contact_context(monkeypatch)

    async def fake_remove_vehicle(_self, *, contact_id: str, vehicle_id: str, **kwargs):
        del _self, contact_id, vehicle_id, kwargs
        raise ValidationException(
            message_key="contact_onboarding.errors.vehicle_use_withdraw",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.remove_vehicle",
        fake_remove_vehicle,
    )

    res = await client.delete(f"/v1/contact-onboarding/vehicles/{VEHICLE_ID}")
    assert_error(res, status_code=422)


@pytest.mark.asyncio
async def test_api_resident_remove_not_found_fails(monkeypatch, client):
    """DELETE contact vehicle returns 404 when vehicle is missing."""
    _patch_contact_context(monkeypatch)

    async def fake_remove_vehicle(_self, *, contact_id: str, vehicle_id: str, **kwargs):
        del _self, contact_id, vehicle_id, kwargs
        raise NotFoundException(
            message_key="contact_onboarding.errors.vehicle_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.remove_vehicle",
        fake_remove_vehicle,
    )

    res = await client.delete(f"/v1/contact-onboarding/vehicles/{VEHICLE_ID}")
    assert_error(res, status_code=404)


@pytest.mark.asyncio
async def test_api_admin_delete_vehicle_not_found_fails(monkeypatch, client):
    """DELETE project vehicle returns 404 when vehicle is missing."""
    _patch_projects_access(monkeypatch)

    async def fake_admin_delete_project_vehicle(
        _self, *, project_id: str, vehicle_id: str, rejection_reason: str
    ):
        del _self, project_id, vehicle_id, rejection_reason
        raise NotFoundException(
            message_key="contact_onboarding.errors.vehicle_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.admin_delete_project_vehicle",
        fake_admin_delete_project_vehicle,
    )

    res = await client.request(
        "DELETE",
        f"/v1/projects/{PROJECT_ID}/vehicles/{VEHICLE_ID}",
        json={"rejection_reason": "Invalid registration"},
    )
    assert_error(res, status_code=404)
