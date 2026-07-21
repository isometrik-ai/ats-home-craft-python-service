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
