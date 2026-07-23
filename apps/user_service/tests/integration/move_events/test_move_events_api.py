"""Integration tests for move events endpoints."""

import pytest

from apps.user_service.app.schemas.move_events import MoveEventResponse
from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

MOVE_EVENT_ID = "me-1"
PROJECT_ID = "proj-1"

_CREATE_PAYLOAD = {
    "unit_id": "unit-1",
    "contact_id": "contact-1",
    "move_type": "move_in",
    "event_date": "2026-07-01",
}

_UPDATE_PAYLOAD = {
    "notes": "Updated move notes",
    "fee_amount": "500.00",
}


def _fake_move_event(**overrides) -> MoveEventResponse:
    """Build a move event response for service fakes."""
    base = {
        "id": MOVE_EVENT_ID,
        "organization_id": "org-123",
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "contact_id": "contact-1",
        "contact_unit_id": "cu-1",
        "move_type": "move_in",
        "event_date": "2026-07-01",
        "fee_amount": None,
        "fee_currency": "INR",
        "notes": None,
        "document_paths": [],
        "recorded_by_user_id": "test-user-id",
        "created_at": "2026-07-01T10:00:00Z",
        "updated_at": "2026-07-01T10:00:00Z",
        "unit_code": "A-101",
        "unit_label": "Apartment 101",
        "unit_tower_name": "Tower A",
        "unit_type": "apartment",
        "contact_name": "John Doe",
        "contact_role": "owner",
    }
    base.update(overrides)
    return MoveEventResponse(**base)


@pytest.mark.asyncio
async def test_list_move_events(monkeypatch, client):
    """GET move-events returns paginated move-in and move-out records."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.move_events")

    async def fake_list_move_events(
        _self,
        *,
        bucket=None,
        search=None,
        unit_id=None,
        project_id=None,
        page=1,
        page_size=20,
    ):
        del _self
        assert bucket == "move_in"
        assert page == 1
        assert page_size == 20
        return [_fake_move_event()], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.move_events_service.MoveEventsService.list_move_events",
        fake_list_move_events,
    )

    res = await client.get(
        "/v1/move-events",
        params={"bucket": "move_in", "page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == MOVE_EVENT_ID
    assert body["total"] == 1
    assert body["data"][0]["move_type"] == "move_in"


@pytest.mark.asyncio
async def test_create_move_event(monkeypatch, client):
    """POST move-events records a move-in or move-out event."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.move_events")

    async def fake_create_move_event(_self, body):
        del _self
        assert body.unit_id == "unit-1"
        assert body.contact_id == "contact-1"
        assert body.move_type.value == "move_in"
        return _fake_move_event()

    monkeypatch.setattr(
        "apps.user_service.app.services.move_events_service.MoveEventsService.create_move_event",
        fake_create_move_event,
    )

    res = await client.post("/v1/move-events", json=_CREATE_PAYLOAD)
    body = assert_success(res, 201)
    assert body["data"]["id"] == MOVE_EVENT_ID
    assert body["data"]["unit_code"] == "A-101"


@pytest.mark.asyncio
async def test_get_move_event(monkeypatch, client):
    """GET move-events/{id} returns one move event with joined fields."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.move_events")

    async def fake_get_move_event(_self, move_event_id: str):
        del _self
        assert move_event_id == MOVE_EVENT_ID
        return _fake_move_event(notes="Move completed")

    monkeypatch.setattr(
        "apps.user_service.app.services.move_events_service.MoveEventsService.get_move_event",
        fake_get_move_event,
    )

    res = await client.get(f"/v1/move-events/{MOVE_EVENT_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == MOVE_EVENT_ID
    assert body["data"]["notes"] == "Move completed"


@pytest.mark.asyncio
async def test_update_move_event(monkeypatch, client):
    """PATCH move-events/{id} updates move event details."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.move_events")

    async def fake_update_move_event(_self, move_event_id: str, body):
        del _self
        assert move_event_id == MOVE_EVENT_ID
        assert body.notes == "Updated move notes"
        return _fake_move_event(notes="Updated move notes", fee_amount="500.00")

    monkeypatch.setattr(
        "apps.user_service.app.services.move_events_service.MoveEventsService.update_move_event",
        fake_update_move_event,
    )

    res = await client.patch(
        f"/v1/move-events/{MOVE_EVENT_ID}",
        json=_UPDATE_PAYLOAD,
    )
    body = assert_success(res, 200)
    assert body["data"]["notes"] == "Updated move notes"
    assert body["data"]["fee_amount"] == "500.00"
