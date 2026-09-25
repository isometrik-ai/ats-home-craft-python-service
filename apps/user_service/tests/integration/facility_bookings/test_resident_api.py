"""Integration tests for resident facility booking routes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.utils.common_utils import UserContext

_API = "apps.user_service.app.api.facility_booking_resident"
PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
CONTACT_ID = "880e8400-e29b-41d4-a716-446655440001"
ORG = "org-123"


def _patch_resident_context(monkeypatch) -> None:
    user_context = UserContext(
        user_id="test-user-id",
        email="test@example.com",
        organization_id=ORG,
        user_type="contact",
    )
    contact = {"id": CONTACT_ID, "organization_id": ORG}

    async def fake_access(current_user, db_connection, project_id, request=None):
        del current_user, db_connection, project_id, request
        return user_context, contact

    monkeypatch.setattr(f"{_API}.ensure_resident_booking_access", fake_access)


@pytest.mark.asyncio
async def test_list_resident_facilities(client, monkeypatch) -> None:
    _patch_resident_context(monkeypatch)
    monkeypatch.setattr(
        f"{_API}.FacilityBookingConfigService.list_bookable_facilities",
        AsyncMock(return_value=[{"id": "f1", "name": "Tennis"}]),
    )
    response = await client.get(f"/v1/projects/{PROJECT_ID}/resident/facility-bookings/facilities")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["data"][0]["name"] == "Tennis"


@pytest.mark.asyncio
async def test_list_my_reservations(client, monkeypatch) -> None:
    _patch_resident_context(monkeypatch)
    monkeypatch.setattr(
        f"{_API}.FacilityReservationService.list_mine",
        AsyncMock(return_value=([{"id": "r1", "status": "confirmed"}], 1)),
    )
    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/resident/facility-bookings/reservations/mine"
    )
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "r1"


@pytest.mark.asyncio
async def test_get_my_ledger(client, monkeypatch) -> None:
    _patch_resident_context(monkeypatch)
    monkeypatch.setattr(
        f"{_API}.FacilityBookingLedgerService.statement",
        AsyncMock(return_value={"entries": [{"id": "l1", "amount": 500}], "balance": 500}),
    )
    response = await client.get(f"/v1/projects/{PROJECT_ID}/resident/facility-bookings/ledger")
    assert response.status_code == 200
    assert response.json()["data"]["balance"] == 500


@pytest.mark.asyncio
async def test_get_my_wallet(client, monkeypatch) -> None:
    _patch_resident_context(monkeypatch)
    monkeypatch.setattr(
        f"{_API}.FacilityBookingBillingService.get_wallet",
        AsyncMock(return_value={"contact_id": CONTACT_ID, "balance": 250, "credit_limit": 10000}),
    )
    response = await client.get(f"/v1/projects/{PROJECT_ID}/resident/facility-bookings/wallet")
    assert response.status_code == 200
    assert response.json()["data"]["balance"] == 250
