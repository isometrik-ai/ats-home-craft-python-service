"""Unit tests for WalkInService."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    WalkInEventType,
    WalkInStatus,
    WalkInVisitUnitStatus,
)
from apps.user_service.app.schemas.walk_in import (
    CreateWalkInRequest,
    RejectWalkInVisitUnitRequest,
    ResidentWalkInVisitUnitListQuery,
    WalkInFlatInput,
    WalkInListQuery,
)
from apps.user_service.app.services.walk_in_service import WalkInService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

ORG_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
ENTRY_ID = "33333333-3333-4333-8333-333333333333"
VISIT_UNIT_ID = "44444444-4444-4444-8444-444444444444"
TOWER_ID = "55555555-5555-4555-8555-555555555555"
UNIT_ID = "66666666-6666-4666-8666-666666666666"
CONTACT_ID = "77777777-7777-4777-8777-777777777777"


def _entry_row(**overrides: Any) -> dict[str, Any]:
    """Build a walk-in entry row."""
    now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    row = {
        "id": ENTRY_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "visitor_first_name": "Sushil",
        "visitor_last_name": "Jha",
        "visitor_phone_isd_code": "+91",
        "visitor_phone_number": "9876543210",
        "visitor_photo_paths": ["org/photo.jpg"],
        "vehicle_photo_paths": [],
        "notes": "Delivery",
        "status": WalkInStatus.AWAITING.value,
        "flats_count": 1,
        "approved_flats_count": 0,
        "requested_at": now,
        "entered_at": None,
        "exited_at": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _visit_unit_row(**overrides: Any) -> dict[str, Any]:
    """Build a visit unit row."""
    row = {
        "id": VISIT_UNIT_ID,
        "organization_id": ORG_ID,
        "walk_in_entry_id": ENTRY_ID,
        "tower_id": TOWER_ID,
        "unit_id": UNIT_ID,
        "status": WalkInVisitUnitStatus.AWAITING.value,
        "tower_name": "Sunflower",
        "unit_code": "A-2102",
        "unit_label": "A-2102",
        "sort_order": 0,
        "rejection_reason": None,
        "approved_at": None,
        "rejected_at": None,
    }
    row.update(overrides)
    return row


class _FakeWalkInRepo:
    """In-memory fake for WalkInRepository."""

    def __init__(self) -> None:
        self.entry = _entry_row()
        self.visit_units = [_visit_unit_row()]
        self.events: list[dict[str, Any]] = []

    async def fetch_units_for_flats(self, **_kwargs) -> list[dict[str, Any]]:
        return [{"unit_id": UNIT_ID, "tower_id": TOWER_ID, "unit_code": "A-2102"}]

    async def insert_entry(self, **kwargs) -> dict[str, Any]:
        self.entry = _entry_row(
            visitor_first_name=kwargs.get("visitor_first_name", "Sushil"),
            visitor_last_name=kwargs.get("visitor_last_name"),
            visitor_phone_isd_code=kwargs.get("visitor_phone_isd_code", "+91"),
            visitor_phone_number=kwargs.get("visitor_phone_number", "9876543210"),
            visitor_photo_paths=kwargs.get("visitor_photo_paths", []),
            vehicle_photo_paths=kwargs.get("vehicle_photo_paths", []),
            notes=kwargs.get("notes"),
            flats_count=kwargs.get("flats_count", 1),
        )
        return self.entry

    async def insert_visit_unit(self, **kwargs) -> dict[str, Any]:
        unit = _visit_unit_row(
            tower_id=kwargs.get("tower_id", TOWER_ID),
            unit_id=kwargs.get("unit_id", UNIT_ID),
            sort_order=kwargs.get("sort_order", 0),
        )
        self.visit_units = [unit]
        return unit

    async def insert_event(self, **kwargs) -> dict[str, Any]:
        event = {"id": "event-1", "occurred_at": datetime.now(timezone.utc), **kwargs}
        self.events.append(event)
        return event

    async def get_entry(self, **_kwargs) -> dict[str, Any] | None:
        return self.entry

    async def list_entries(self, **_kwargs) -> list[dict[str, Any]]:
        return [self.entry]

    async def list_visit_units(self, **_kwargs) -> list[dict[str, Any]]:
        return self.visit_units

    async def list_events(self, **_kwargs) -> list[dict[str, Any]]:
        return self.events

    async def get_visit_unit(self, **_kwargs) -> dict[str, Any] | None:
        return self.visit_units[0]

    async def update_visit_unit_status(self, **_kwargs) -> dict[str, Any]:
        self.visit_units[0] = _visit_unit_row(status=WalkInVisitUnitStatus.APPROVED.value)
        return self.visit_units[0]

    async def count_visit_units_by_status(self, **_kwargs) -> dict[str, int]:
        return {"approved_count": 1, "awaiting_count": 0, "rejected_count": 0}

    async def update_entry_header(self, **_kwargs) -> dict[str, Any]:
        status = _kwargs.get("status")
        approved = _kwargs.get("approved_flats_count")
        entered_at = _kwargs.get("entered_at")
        updates: dict[str, Any] = {}
        if status:
            updates["status"] = status
        if approved is not None:
            updates["approved_flats_count"] = approved
        if entered_at:
            updates["entered_at"] = entered_at
        self.entry = _entry_row(**updates)
        return self.entry

    async def resident_can_act_on_unit(self, **_kwargs) -> bool:
        return True

    async def list_resident_visit_units(self, **_kwargs) -> list[dict[str, Any]]:
        return [
            {
                "visit_unit_id": VISIT_UNIT_ID,
                "walk_in_entry_id": ENTRY_ID,
                "tower_id": TOWER_ID,
                "unit_id": UNIT_ID,
                "status": WalkInVisitUnitStatus.AWAITING.value,
                "tower_name": "Sunflower",
                "unit_code": "A-2102",
                "unit_label": "A-2102",
                "visitor_first_name": "Sushil",
                "visitor_last_name": "Jha",
                "visitor_phone_isd_code": "+91",
                "visitor_phone_number": "9876543210",
                "visitor_photo_paths": ["org/photo.jpg"],
                "notes": "Delivery",
                "requested_at": datetime.now(timezone.utc),
                "flats_count": 1,
            }
        ]


def _service(*, repo: _FakeWalkInRepo | None = None) -> WalkInService:
    """Build WalkInService with injected fake repository."""
    service = WalkInService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="staff-user",
            email="guard@example.com",
            organization_id=ORG_ID,
        ),
    )
    service.repo = repo or _FakeWalkInRepo()
    service.setup_service = AsyncMock()
    service.setup_service.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    return service


@pytest.mark.asyncio
async def test_create_walk_in_inserts_entry_and_visit_units():
    """Create persists entry, visit units, and requested event."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)
    body = CreateWalkInRequest(
        visitor_first_name="Sushil",
        visitor_phone_isd_code="+91",
        visitor_phone_number="9876543210",
        visitor_photo_paths=["org/photo.jpg"],
        flats=[WalkInFlatInput(tower_id=TOWER_ID, unit_id=UNIT_ID)],
    )

    result = await service.create_walk_in(project_id=PROJECT_ID, body=body)

    assert result["visitor_first_name"] == "Sushil"
    assert len(result["visit_units"]) == 1
    assert repo.events[0]["event_type"] == WalkInEventType.REQUESTED.value
    service.setup_service.ensure_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_enter_requires_approved_visit_unit():
    """Enter fails when no flat has approved."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)

    with pytest.raises(ValidationException):
        await service.enter_walk_in(project_id=PROJECT_ID, walk_in_entry_id=ENTRY_ID)


@pytest.mark.asyncio
async def test_enter_succeeds_when_flat_approved():
    """Enter allowed when approved_flats_count >= 1."""
    repo = _FakeWalkInRepo()
    repo.entry = _entry_row(
        status=WalkInStatus.APPROVED.value,
        approved_flats_count=1,
    )
    service = _service(repo=repo)

    result = await service.enter_walk_in(project_id=PROJECT_ID, walk_in_entry_id=ENTRY_ID)

    assert result["status"] == WalkInStatus.ENTERED.value
    assert any(event.get("event_type") == WalkInEventType.ENTERED.value for event in repo.events)


@pytest.mark.asyncio
async def test_approve_visit_unit_recomputes_header():
    """Approve updates visit unit and header approved count."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)

    result = await service.approve_visit_unit(
        contact_id=CONTACT_ID,
        walk_in_entry_id=ENTRY_ID,
        visit_unit_id=VISIT_UNIT_ID,
    )

    assert result["approved_flats_count"] == 1
    assert any(
        event.get("event_type") == WalkInEventType.VISIT_UNIT_APPROVED.value
        for event in repo.events
    )


@pytest.mark.asyncio
async def test_get_entry_not_found():
    """Missing entry raises not found."""
    repo = _FakeWalkInRepo()
    repo.get_entry = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service = _service(repo=repo)

    with pytest.raises(NotFoundException):
        await service.get_project_walk_in(
            project_id=PROJECT_ID,
            walk_in_entry_id=ENTRY_ID,
        )


@pytest.mark.asyncio
async def test_reject_visit_unit():
    """Reject records visit_unit_rejected event."""
    repo = _FakeWalkInRepo()

    async def _reject(**_kwargs):
        repo.visit_units[0] = _visit_unit_row(status=WalkInVisitUnitStatus.REJECTED.value)
        return repo.visit_units[0]

    repo.update_visit_unit_status = _reject  # type: ignore[method-assign]
    repo.count_visit_units_by_status = AsyncMock(  # type: ignore[method-assign]
        return_value={"approved_count": 0, "awaiting_count": 0, "rejected_count": 1}
    )
    service = _service(repo=repo)

    await service.reject_visit_unit(
        contact_id=CONTACT_ID,
        walk_in_entry_id=ENTRY_ID,
        visit_unit_id=VISIT_UNIT_ID,
        body=RejectWalkInVisitUnitRequest(rejection_reason="Not expecting anyone"),
    )

    assert any(
        event.get("event_type") == WalkInEventType.VISIT_UNIT_REJECTED.value
        for event in repo.events
    )


@pytest.mark.asyncio
async def test_create_walk_in_rejects_invalid_units():
    """Create fails when flats do not validate against the project."""
    repo = _FakeWalkInRepo()

    async def _empty(**_kwargs):
        return []

    repo.fetch_units_for_flats = _empty  # type: ignore[method-assign]
    service = _service(repo=repo)
    body = CreateWalkInRequest(
        visitor_first_name="Sushil",
        visitor_phone_isd_code="+91",
        visitor_phone_number="9876543210",
        visitor_photo_paths=["org/photo.jpg"],
        flats=[WalkInFlatInput(tower_id=TOWER_ID, unit_id=UNIT_ID)],
    )

    with pytest.raises(ValidationException):
        await service.create_walk_in(project_id=PROJECT_ID, body=body)


@pytest.mark.asyncio
async def test_list_project_walk_ins():
    """List serializes project walk-in summaries."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)

    items = await service.list_project_walk_ins(
        project_id=PROJECT_ID,
        query=WalkInListQuery(status=WalkInStatus.AWAITING),
    )

    assert items[0]["id"] == ENTRY_ID
    assert items[0]["status"] == WalkInStatus.AWAITING.value
    service.setup_service.ensure_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_resident_visit_units():
    """Resident list maps repository rows to API items."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)

    items = await service.list_resident_visit_units(
        contact_id=CONTACT_ID,
        query=ResidentWalkInVisitUnitListQuery(status=WalkInVisitUnitStatus.AWAITING),
    )

    assert items[0]["visit_unit_id"] == VISIT_UNIT_ID
    assert items[0]["visitor_first_name"] == "Sushil"


@pytest.mark.asyncio
async def test_get_resident_walk_in_success():
    """Resident detail succeeds when contact can act on a visit unit."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)

    detail = await service.get_resident_walk_in(
        contact_id=CONTACT_ID,
        walk_in_entry_id=ENTRY_ID,
    )

    assert detail["id"] == ENTRY_ID
    assert detail["visit_units"][0]["id"] == VISIT_UNIT_ID


@pytest.mark.asyncio
async def test_get_resident_walk_in_not_accessible():
    """Resident detail is hidden when contact has no linked flat."""
    repo = _FakeWalkInRepo()
    repo.resident_can_act_on_unit = AsyncMock(return_value=False)  # type: ignore[method-assign]
    service = _service(repo=repo)

    with pytest.raises(NotFoundException):
        await service.get_resident_walk_in(
            contact_id=CONTACT_ID,
            walk_in_entry_id=ENTRY_ID,
        )


@pytest.mark.asyncio
async def test_enter_invalid_status_transition():
    """Enter fails when header status does not allow entry."""
    repo = _FakeWalkInRepo()
    repo.entry = _entry_row(status=WalkInStatus.EXITED.value, approved_flats_count=1)
    service = _service(repo=repo)

    with pytest.raises(ValidationException):
        await service.enter_walk_in(project_id=PROJECT_ID, walk_in_entry_id=ENTRY_ID)


@pytest.mark.asyncio
async def test_exit_walk_in_success():
    """Exit marks visitor exited and records event."""
    repo = _FakeWalkInRepo()
    repo.entry = _entry_row(status=WalkInStatus.ENTERED.value, approved_flats_count=1)
    service = _service(repo=repo)

    result = await service.exit_walk_in(project_id=PROJECT_ID, walk_in_entry_id=ENTRY_ID)

    assert result["status"] == WalkInStatus.EXITED.value
    assert any(event.get("event_type") == WalkInEventType.EXITED.value for event in repo.events)


@pytest.mark.asyncio
async def test_exit_walk_in_invalid_status():
    """Exit fails when visitor has not entered."""
    repo = _FakeWalkInRepo()
    service = _service(repo=repo)

    with pytest.raises(ValidationException):
        await service.exit_walk_in(project_id=PROJECT_ID, walk_in_entry_id=ENTRY_ID)


@pytest.mark.asyncio
async def test_approve_visit_unit_not_found():
    """Approve raises when visit unit is missing."""
    repo = _FakeWalkInRepo()
    repo.get_visit_unit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service = _service(repo=repo)

    with pytest.raises(NotFoundException):
        await service.approve_visit_unit(
            contact_id=CONTACT_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
        )


@pytest.mark.asyncio
async def test_approve_visit_unit_not_awaiting():
    """Approve rejects visit units that are not awaiting."""
    repo = _FakeWalkInRepo()
    repo.visit_units = [_visit_unit_row(status=WalkInVisitUnitStatus.APPROVED.value)]
    service = _service(repo=repo)

    with pytest.raises(ValidationException):
        await service.approve_visit_unit(
            contact_id=CONTACT_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
        )


@pytest.mark.asyncio
async def test_approve_visit_unit_not_accessible():
    """Approve rejects when resident cannot act on the flat."""
    repo = _FakeWalkInRepo()
    repo.resident_can_act_on_unit = AsyncMock(return_value=False)  # type: ignore[method-assign]
    service = _service(repo=repo)

    with pytest.raises(ValidationException):
        await service.approve_visit_unit(
            contact_id=CONTACT_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
        )


@pytest.mark.asyncio
async def test_approve_visit_unit_update_conflict():
    """Approve surfaces race when status update returns nothing."""
    repo = _FakeWalkInRepo()
    repo.update_visit_unit_status = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service = _service(repo=repo)

    with pytest.raises(ValidationException):
        await service.approve_visit_unit(
            contact_id=CONTACT_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
        )


@pytest.mark.asyncio
async def test_reject_visit_unit_not_found():
    """Reject raises when visit unit is missing."""
    repo = _FakeWalkInRepo()
    repo.get_visit_unit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service = _service(repo=repo)

    with pytest.raises(NotFoundException):
        await service.reject_visit_unit(
            contact_id=CONTACT_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
            body=RejectWalkInVisitUnitRequest(rejection_reason="No"),
        )


def test_serialize_event_non_dict_payload():
    """Non-dict event payloads are normalized to empty dict."""
    service = _service()
    event = service._serialize_event(
        {
            "id": "event-1",
            "event_type": WalkInEventType.REQUESTED.value,
            "occurred_at": datetime.now(timezone.utc),
            "payload": "not-a-dict",
        }
    )
    assert event["payload"] == {}


def test_derive_milestones_completed_states():
    """Milestones reflect approved, entered, and exited states."""
    service = _service()
    now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    milestones = service._derive_milestones(
        row=_entry_row(
            status=WalkInStatus.EXITED.value,
            entered_at=now,
            exited_at=now,
        ),
        events=[
            {
                "event_type": WalkInEventType.VISIT_UNIT_APPROVED.value,
                "occurred_at": now,
            }
        ],
    )

    by_key = {item["key"]: item for item in milestones}
    assert by_key["requested"]["completed"] is True
    assert by_key["approved"]["completed"] is True
    assert by_key["entered"]["completed"] is True
    assert by_key["exited"]["completed"] is True


def test_format_contact_name():
    """Contact name helper joins non-empty parts."""
    assert (
        WalkInService._format_contact_name(
            {"prefix": "Mr", "first_name": "Resident", "last_name": "Owner"}
        )
        == "Mr Resident Owner"
    )
    assert WalkInService._format_contact_name({"first_name": "", "last_name": ""}) is None
