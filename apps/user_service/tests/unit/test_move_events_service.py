"""Unit tests for MoveEventsService."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.enums import MoveEventType
from apps.user_service.app.schemas.move_events import (
    CreateMoveEventRequest,
    UpdateMoveEventRequest,
)
from apps.user_service.app.services.move_events_service import MoveEventsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _user_context() -> UserContext:
    """Build an admin user context for service tests."""
    return UserContext(
        user_id="admin-1",
        email="admin@example.com",
        organization_id="org-1",
    )


def _move_row(**overrides: Any) -> dict[str, Any]:
    """Build a move event row dict with sensible defaults."""
    row = {
        "id": "move-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": "unit-1",
        "contact_id": "contact-1",
        "contact_unit_id": "cu-1",
        "move_type": MoveEventType.MOVE_IN.value,
        "event_date": date(2026, 5, 25),
        "fee_amount": Decimal("5000.00"),
        "fee_currency": "INR",
        "notes": None,
        "document_paths": [],
        "recorded_by_user_id": "admin-1",
        "created_at": None,
        "updated_at": None,
        "unit_code": "A-0101",
        "unit_label": "A-0101",
        "unit_tower_name": "Tower A",
        "unit_type": "apartment",
        "contact_first_name": "Arjun",
        "contact_last_name": "Babu",
        "contact_prefix": None,
        "contact_role": "Tenant",
    }
    row.update(overrides)
    return row


class _FakeMoveEventsRepo:
    """In-memory fake for MoveEventsRepository."""

    def __init__(self):
        self.insert_calls: list[dict[str, Any]] = []
        self.row = _move_row()
        self.latest_row: dict[str, Any] | None = None
        self.contact_exists_flag = True

    async def insert(self, data: dict[str, Any]) -> dict[str, Any]:
        """Record insert call and return new move event id."""
        self.insert_calls.append(data)
        return {"id": "move-1"}

    async def get_by_id(self, *, organization_id: str, move_event_id: str):
        """Return configured row or None when id is missing."""
        del organization_id
        if move_event_id == "missing":
            return None
        return self.row

    async def list(self, **_kwargs):
        """Return configured row as a single-item page."""
        return [self.row], 1

    async def update(self, *, organization_id: str, move_event_id: str, update_data: dict):
        """Apply update_data to configured row."""
        del organization_id
        if move_event_id == "missing":
            return None
        self.row = {**self.row, **update_data}
        return self.row

    async def soft_delete(self, *, organization_id: str, move_event_id: str):
        """Return deleted move snapshot or None when id is missing."""
        del organization_id
        if move_event_id == "missing":
            return None
        return {
            "id": move_event_id,
            "unit_id": self.row["unit_id"],
            "contact_id": self.row["contact_id"],
            "contact_unit_id": self.row.get("contact_unit_id"),
            "move_type": self.row["move_type"],
            "event_date": self.row["event_date"],
        }

    async def get_latest_for_unit_contact(self, **_kwargs):
        """Return configured latest move row for unit/contact."""
        return self.latest_row

    async def contact_exists(self, *, organization_id: str, contact_id: str) -> bool:
        """Return whether contact exists in org."""
        del organization_id
        return self.contact_exists_flag and contact_id != "missing-contact"


class _FakeContactUnitsRepo:
    """In-memory fake for ContactUnitsRepository."""

    def __init__(self):
        self.link: dict[str, Any] | None = {"id": "cu-1", "status": "active"}
        self.has_active = True
        self.sync_move_in_calls: list[dict[str, Any]] = []
        self.sync_move_out_calls: list[dict[str, Any]] = []
        self.insert_allotment_calls: list[dict[str, Any]] = []

    async def get_unit_project(self, *, organization_id: str, unit_id: str):
        """Return unit project row or None when unit is missing."""
        if unit_id == "missing-unit":
            return None
        return {"id": unit_id, "organization_id": organization_id, "project_id": "project-1"}

    async def get_by_unit_and_contact(self, **_kwargs):
        """Return configured contact_units link."""
        return self.link

    async def contact_has_active_unit(self, **_kwargs) -> bool:
        """Return configured active-occupancy flag."""
        return self.has_active

    async def insert_allotment(self, **kwargs):
        """Record allotment insert and return new link."""
        self.insert_allotment_calls.append(kwargs)
        return {"id": "cu-new", "status": "pending"}

    async def sync_move_in(self, **kwargs):
        """Record move-in sync and return active link."""
        self.sync_move_in_calls.append(kwargs)
        return {"id": kwargs["contact_unit_id"], "status": "active"}

    async def sync_move_out(self, **kwargs):
        """Record move-out sync and return moved-out link."""
        self.sync_move_out_calls.append(kwargs)
        return {"id": kwargs["contact_unit_id"], "status": "moved_out"}


def _service(
    move_repo: _FakeMoveEventsRepo | None = None,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
) -> MoveEventsService:
    """Build MoveEventsService with fakes."""
    return MoveEventsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        move_events_repository=move_repo or _FakeMoveEventsRepo(),
        contact_units_repository=contact_units_repo or _FakeContactUnitsRepo(),
    )


@pytest.mark.asyncio
async def test_create_move_in_syncs_active_link():
    """Move-in inserts event and activates contact_units."""
    move_repo = _FakeMoveEventsRepo()
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    result = await service.create_move_event(
        CreateMoveEventRequest(
            unit_id="unit-1",
            contact_id="contact-1",
            move_type=MoveEventType.MOVE_IN,
            event_date=date(2026, 5, 25),
            fee_amount=Decimal("5000"),
        )
    )

    assert result.move_type == MoveEventType.MOVE_IN.value
    assert len(move_repo.insert_calls) == 1
    assert len(contact_units_repo.sync_move_in_calls) == 1


@pytest.mark.asyncio
async def test_create_move_in_without_link_creates_allotment():
    """Move-in auto-creates contact_units when no link exists."""
    move_repo = _FakeMoveEventsRepo()
    contact_units_repo = _FakeContactUnitsRepo()
    contact_units_repo.link = None
    service = _service(move_repo, contact_units_repo)

    await service.create_move_event(
        CreateMoveEventRequest(
            unit_id="unit-1",
            contact_id="contact-1",
            move_type=MoveEventType.MOVE_IN,
            event_date=date(2026, 5, 25),
        )
    )

    assert len(contact_units_repo.insert_allotment_calls) == 1
    assert len(contact_units_repo.sync_move_in_calls) == 1


@pytest.mark.asyncio
async def test_create_move_out_requires_active_occupancy():
    """Move-out rejected when contact is not actively occupying the unit."""
    contact_units_repo = _FakeContactUnitsRepo()
    contact_units_repo.has_active = False
    service = _service(_FakeMoveEventsRepo(), contact_units_repo)

    with pytest.raises(ValidationException) as exc_info:
        await service.create_move_event(
            CreateMoveEventRequest(
                unit_id="unit-1",
                contact_id="contact-1",
                move_type=MoveEventType.MOVE_OUT,
                event_date=date(2026, 5, 8),
            )
        )
    assert exc_info.value.message_key == "move_events.errors.not_currently_occupying"


@pytest.mark.asyncio
async def test_create_move_out_without_link_rejected():
    """Move-out rejected when no contact_units link exists."""
    contact_units_repo = _FakeContactUnitsRepo()
    contact_units_repo.link = None
    service = _service(_FakeMoveEventsRepo(), contact_units_repo)

    with pytest.raises(ValidationException) as exc_info:
        await service.create_move_event(
            CreateMoveEventRequest(
                unit_id="unit-1",
                contact_id="contact-1",
                move_type=MoveEventType.MOVE_OUT,
                event_date=date(2026, 5, 8),
            )
        )
    assert exc_info.value.message_key == "move_events.errors.not_currently_occupying"


@pytest.mark.asyncio
async def test_create_rejects_missing_unit():
    """Unknown unit raises not found."""
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(_FakeMoveEventsRepo(), contact_units_repo)

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_move_event(
            CreateMoveEventRequest(
                unit_id="missing-unit",
                contact_id="contact-1",
                move_type=MoveEventType.MOVE_IN,
                event_date=date(2026, 5, 25),
            )
        )
    assert exc_info.value.message_key == "move_events.errors.unit_not_found"


@pytest.mark.asyncio
async def test_update_event_date_resyncs_contact_units():
    """Patching event_date re-syncs occupancy timestamp."""
    move_repo = _FakeMoveEventsRepo()
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    await service.update_move_event(
        "move-1",
        UpdateMoveEventRequest(event_date=date(2026, 5, 26)),
    )

    assert len(contact_units_repo.sync_move_in_calls) == 1


@pytest.mark.asyncio
async def test_delete_rederives_occupancy_from_latest_move():
    """Voiding a move re-applies the prior move's occupancy state."""
    move_repo = _FakeMoveEventsRepo()
    move_repo.latest_row = {
        "id": "move-0",
        "contact_unit_id": "cu-1",
        "move_type": MoveEventType.MOVE_IN.value,
        "event_date": date(2026, 5, 13),
    }
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    await service.delete_move_event("move-1")

    assert len(contact_units_repo.sync_move_in_calls) == 1


def test_format_date_and_decimal_helpers():
    """Static formatters handle None, native types, and string fallbacks."""
    assert MoveEventsService._format_date(None) == ""
    assert MoveEventsService._format_date(date(2026, 1, 2)) == "2026-01-02"
    assert MoveEventsService._format_date("2026-01-02") == "2026-01-02"
    assert MoveEventsService._format_decimal(None) is None
    assert MoveEventsService._format_decimal(Decimal("10.50")) == "10.50"
    assert MoveEventsService._format_decimal(42) == "42"


@pytest.mark.asyncio
async def test_create_move_out_success_syncs_move_out():
    """Move-out inserts event and syncs move-out on contact_units."""
    move_repo = _FakeMoveEventsRepo()
    move_repo.row = _move_row(move_type=MoveEventType.MOVE_OUT.value)
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    result = await service.create_move_event(
        CreateMoveEventRequest(
            unit_id="unit-1",
            contact_id="contact-1",
            move_type=MoveEventType.MOVE_OUT,
            event_date=date(2026, 5, 8),
        )
    )

    assert result.move_type == MoveEventType.MOVE_OUT.value
    assert len(contact_units_repo.sync_move_out_calls) == 1


@pytest.mark.asyncio
async def test_create_rejects_missing_contact():
    """Unknown contact raises not found."""
    move_repo = _FakeMoveEventsRepo()
    move_repo.contact_exists_flag = False
    service = _service(move_repo, _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_move_event(
            CreateMoveEventRequest(
                unit_id="unit-1",
                contact_id="missing-contact",
                move_type=MoveEventType.MOVE_IN,
                event_date=date(2026, 5, 25),
            )
        )
    assert exc_info.value.message_key == "move_events.errors.contact_not_found"


@pytest.mark.asyncio
async def test_create_raises_when_inserted_row_missing():
    """Insert succeeds but follow-up fetch missing raises not found."""
    move_repo = _FakeMoveEventsRepo()

    async def _missing_after_insert(**kwargs):
        del kwargs
        return None

    move_repo.get_by_id = _missing_after_insert
    service = _service(move_repo, _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_move_event(
            CreateMoveEventRequest(
                unit_id="unit-1",
                contact_id="contact-1",
                move_type=MoveEventType.MOVE_IN,
                event_date=date(2026, 5, 25),
            )
        )
    assert exc_info.value.message_key == "move_events.errors.move_event_not_found"


@pytest.mark.asyncio
async def test_list_and_get_move_events():
    """List and get serialize repository rows."""
    move_repo = _FakeMoveEventsRepo()
    service = _service(move_repo, _FakeContactUnitsRepo())

    rows, total = await service.list_move_events(search="A-0101", page=1, page_size=10)
    assert total == 1
    assert rows[0].unit_code == "A-0101"

    fetched = await service.get_move_event("move-1")
    assert fetched.id == "move-1"


@pytest.mark.asyncio
async def test_get_move_event_not_found():
    """Missing move event raises not found."""
    service = _service(_FakeMoveEventsRepo(), _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_move_event("missing")
    assert exc_info.value.message_key == "move_events.errors.move_event_not_found"


@pytest.mark.asyncio
async def test_update_empty_payload_returns_existing():
    """Empty patch returns existing row without repository update."""
    move_repo = _FakeMoveEventsRepo()
    service = _service(move_repo, _FakeContactUnitsRepo())

    result = await service.update_move_event("move-1", UpdateMoveEventRequest())

    assert result.id == "move-1"
    assert move_repo.row["event_date"] == date(2026, 5, 25)


@pytest.mark.asyncio
async def test_update_rejects_negative_fee():
    """Negative fee amount is rejected by service validation."""
    service = _service(_FakeMoveEventsRepo(), _FakeContactUnitsRepo())
    body = UpdateMoveEventRequest.model_construct(fee_amount=Decimal("-1"))

    with pytest.raises(ValidationException) as exc_info:
        await service.update_move_event("move-1", body)
    assert exc_info.value.message_key == "move_events.errors.invalid_fee"


@pytest.mark.asyncio
async def test_update_not_found_paths():
    """Update raises when existing or updated row is missing."""
    service = _service(_FakeMoveEventsRepo(), _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException):
        await service.update_move_event("missing", UpdateMoveEventRequest(notes="x"))

    move_repo = _FakeMoveEventsRepo()

    async def _update_returns_none(**kwargs):
        del kwargs
        return None

    move_repo.update = _update_returns_none
    service = _service(move_repo, _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException):
        await service.update_move_event("move-1", UpdateMoveEventRequest(notes="x"))


@pytest.mark.asyncio
async def test_update_event_date_resyncs_move_out():
    """Patching event_date on move-out re-syncs move-out occupancy."""
    move_repo = _FakeMoveEventsRepo()
    move_repo.row = _move_row(move_type=MoveEventType.MOVE_OUT.value)
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    await service.update_move_event(
        "move-1",
        UpdateMoveEventRequest(event_date=date(2026, 5, 27)),
    )

    assert len(contact_units_repo.sync_move_out_calls) == 1


@pytest.mark.asyncio
async def test_delete_not_found_paths():
    """Delete raises when existing or soft-delete result is missing."""
    service = _service(_FakeMoveEventsRepo(), _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException):
        await service.delete_move_event("missing")

    move_repo = _FakeMoveEventsRepo()

    async def _soft_delete_none(**kwargs):
        del kwargs
        return None

    move_repo.soft_delete = _soft_delete_none
    service = _service(move_repo, _FakeContactUnitsRepo())

    with pytest.raises(NotFoundException):
        await service.delete_move_event("move-1")


@pytest.mark.asyncio
async def test_delete_without_latest_skips_resync():
    """Voiding a move skips occupancy sync when no prior move exists."""
    move_repo = _FakeMoveEventsRepo()
    move_repo.latest_row = None
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    await service.delete_move_event("move-1")

    assert not contact_units_repo.sync_move_in_calls
    assert not contact_units_repo.sync_move_out_calls


@pytest.mark.asyncio
async def test_update_event_date_skips_resync_without_contact_unit():
    """Patching event_date skips occupancy sync when contact_unit_id is missing."""
    move_repo = _FakeMoveEventsRepo()
    move_repo.row = _move_row(contact_unit_id=None)
    contact_units_repo = _FakeContactUnitsRepo()
    service = _service(move_repo, contact_units_repo)

    await service.update_move_event(
        "move-1",
        UpdateMoveEventRequest(event_date=date(2026, 5, 27)),
    )

    assert not contact_units_repo.sync_move_in_calls
    assert not contact_units_repo.sync_move_out_calls


@pytest.mark.asyncio
async def test_update_with_null_fee_amount_skips_fee_validation():
    """Explicit null fee_amount bypasses negative-fee service check."""
    move_repo = _FakeMoveEventsRepo()
    service = _service(move_repo, _FakeContactUnitsRepo())

    result = await service.update_move_event(
        "move-1",
        UpdateMoveEventRequest(fee_amount=None, notes="cleared"),
    )

    assert result.notes == "cleared"
