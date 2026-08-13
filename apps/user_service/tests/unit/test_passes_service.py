"""Unit tests for PassesService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    PassDisplayStatus,
    PassStatus,
    PassType,
    PassValidityType,
)
from apps.user_service.app.schemas.passes import (
    AdminUnitPassListQuery,
    CreatePassRequest,
    UpdatePassRequest,
)
from apps.user_service.app.services.passes_service import PassesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    InternalServerErrorException,
    NotFoundException,
    ValidationException,
)


def _user_context() -> UserContext:
    """Build a resident user context for service tests."""
    return UserContext(
        user_id="user-1",
        email="owner@example.com",
        organization_id="org-1",
    )


def _pass_row(**overrides: Any) -> dict[str, Any]:
    """Build a pass row dict with sensible defaults."""
    now = datetime.now(timezone.utc)
    row = {
        "id": "pass-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": "unit-1",
        "host_contact_id": "contact-1",
        "pass_type": "guest",
        "guest_name": "Ravi Kumar",
        "guest_phone_isd_code": "+91",
        "guest_phone_number": "9876543210",
        "visitor_count": 1,
        "vehicle_number": None,
        "purpose": "Visit",
        "valid_from": now - timedelta(hours=1),
        "valid_until": now + timedelta(hours=5),
        "validity_type": PassValidityType.ONE_TIME.value,
        "allow_multiple_entries": False,
        "is_private": False,
        "max_entries": None,
        "entry_count": 0,
        "status": PassStatus.ACTIVE.value,
        "code": "4821",
        "pass_image_path": None,
        "notes": None,
        "unit_code": "A-1203",
        "unit_label": "A-1203",
        "tower_name": "Tower A",
        "floor_name": "12",
        "config_label": "3 BHK",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


class _FakePassesRepo:
    """In-memory fake for PassesRepository."""

    def __init__(self):
        self.insert_calls: list[dict[str, Any]] = []
        self.code_exists = False
        self.row = _pass_row()
        self.list_result: tuple[list[dict[str, Any]], int] = ([], 0)
        self.cancel_result: dict[str, Any] | None = {"id": "pass-1", "status": "cancelled"}
        self.update_result: dict[str, Any] | None = None

    async def insert(self, data):
        """Record insert payload and return a pass id."""
        self.insert_calls.append(data)
        return {"id": "pass-1"}

    async def get_owned_by_contact(self, **_kwargs):
        """Return configured owned pass row."""
        return self.row

    async def list_by_contact(self, **_kwargs):
        """Return configured list result."""
        return self.list_result

    async def code_exists_active(self, **_kwargs):
        """Return configured active-code collision flag."""
        return self.code_exists

    async def update(self, **_kwargs):
        """Return configured update result or default row."""
        if self.update_result is None:
            return self.row
        return self.update_result

    async def cancel(self, **_kwargs):
        """Return configured cancel result and mirror cancelled status on the row."""
        if self.row is not None:
            self.row = {**self.row, "status": PassStatus.CANCELLED.value}
        return self.cancel_result


class _FakeEventsRepo:
    """In-memory fake for PassEventsRepository."""

    def __init__(self):
        self.insert_calls: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def insert_event(self, data):
        """Record timeline event insert."""
        self.insert_calls.append(data)
        return {"id": "event-1", **data}

    async def list_by_pass(self, **_kwargs):
        """Return configured pass events."""
        return self.events


class _FakeContactUnitsRepo:
    """In-memory fake for ContactUnitsRepository ownership checks."""

    def __init__(self, *, has_unit: bool = True):
        self.has_unit = has_unit

    async def contact_has_active_unit(self, **_kwargs):
        """Return whether the contact owns an active unit."""
        return self.has_unit

    async def get_unit_project(self, **_kwargs):
        """Return unit project row when ownership is configured."""
        if not self.has_unit:
            return None
        return {"project_id": "project-1"}


def _service(
    passes_repo: _FakePassesRepo | None = None,
    events_repo: _FakeEventsRepo | None = None,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
) -> PassesService:
    """Build PassesService with injected fake repositories."""
    svc = PassesService(db_connection=MagicMock(), user_context=_user_context())
    svc.passes_repo = passes_repo or _FakePassesRepo()
    svc.events_repo = events_repo or _FakeEventsRepo()
    svc.contact_units_repo = contact_units_repo or _FakeContactUnitsRepo()
    return svc


def test_derive_display_status_cancelled():
    """Cancelled passes map to the cancelled display bucket."""
    row = _pass_row(status=PassStatus.CANCELLED.value)
    assert PassesService.derive_display_status(row) == PassDisplayStatus.CANCELLED.value


def test_derive_display_status_upcoming():
    """Future validity window maps to upcoming display status."""
    now = datetime.now(timezone.utc)
    row = _pass_row(
        valid_from=now + timedelta(hours=2),
        valid_until=now + timedelta(hours=5),
    )
    assert PassesService.derive_display_status(row, now=now) == PassDisplayStatus.UPCOMING.value


def test_derive_display_status_expired():
    """Past validity window maps to expired display status."""
    now = datetime.now(timezone.utc)
    row = _pass_row(
        valid_from=now - timedelta(hours=5),
        valid_until=now - timedelta(hours=1),
    )
    assert PassesService.derive_display_status(row, now=now) == PassDisplayStatus.EXPIRED.value


def test_derive_display_status_used_one_time():
    """One-time pass with entry maps to used display status."""
    now = datetime.now(timezone.utc)
    row = _pass_row(entry_count=1)
    assert PassesService.derive_display_status(row, now=now) == PassDisplayStatus.USED.value


def test_normalize_pass_preserves_null_foreign_keys():
    """Daily help and other passes may omit unit_id and host_contact_id."""
    svc = PassesService(db_connection=MagicMock(), user_context=_user_context())
    normalized = svc._normalize_pass(_pass_row(unit_id=None, host_contact_id=None))
    assert normalized["unit_id"] is None
    assert normalized["host_contact_id"] is None


@pytest.mark.asyncio
async def test_create_pass_rejects_unowned_unit():
    """Create rejects units the host contact does not actively own."""
    svc = _service(contact_units_repo=_FakeContactUnitsRepo(has_unit=False))
    now = datetime.now(timezone.utc)
    body = CreatePassRequest(
        unit_id="unit-1",
        guest_name="Guest",
        valid_from=now + timedelta(hours=1),
        valid_until=now + timedelta(hours=3),
    )
    with pytest.raises(ValidationException):
        await svc.create_pass(contact_id="contact-1", body=body)


@pytest.mark.asyncio
async def test_validate_validity_window_rejects_invalid_range():
    """Validity end must be after validity start."""
    svc = _service()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationException):
        svc._validate_validity_window(now + timedelta(hours=3), now + timedelta(hours=1))


@pytest.mark.asyncio
async def test_create_pass_writes_created_event():
    """Successful create persists pass row and created timeline event."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    now = datetime.now(timezone.utc)
    body = CreatePassRequest(
        unit_id="unit-1",
        guest_name="Guest",
        valid_from=now + timedelta(hours=1),
        valid_until=now + timedelta(hours=3),
    )
    result = await svc.create_pass(contact_id="contact-1", body=body)
    assert result["code"] == "4821"
    assert len(passes_repo.insert_calls) == 1
    assert passes_repo.insert_calls[0]["guest_name"] == "Guest"
    assert events_repo.insert_calls[0]["event_type"] == "created"
    assert events_repo.insert_calls[0]["actor_type"] == "resident"


@pytest.mark.asyncio
async def test_create_pass_persists_is_private():
    """Create persists is_private flag on the pass row."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    now = datetime.now(timezone.utc)
    body = CreatePassRequest(
        unit_id="unit-1",
        guest_name="Guest",
        valid_from=now + timedelta(hours=1),
        valid_until=now + timedelta(hours=3),
        is_private=True,
    )
    await svc.create_pass(contact_id="contact-1", body=body)
    assert passes_repo.insert_calls[0]["is_private"] is True


@pytest.mark.asyncio
async def test_create_pass_code_collision_raises():
    """Create fails when active code generation collides repeatedly."""
    passes_repo = _FakePassesRepo()
    passes_repo.code_exists = True
    svc = _service(passes_repo=passes_repo)
    now = datetime.now(timezone.utc)
    body = CreatePassRequest(
        unit_id="unit-1",
        guest_name="Guest",
        valid_from=now + timedelta(hours=1),
        valid_until=now + timedelta(hours=3),
    )
    with pytest.raises(InternalServerErrorException):
        await svc.create_pass(contact_id="contact-1", body=body)


@pytest.mark.asyncio
async def test_cancel_pass_rejects_used_pass():
    """Cancel rejects passes that are already used/completed."""
    passes_repo = _FakePassesRepo()
    passes_repo.row = _pass_row(
        status=PassStatus.COMPLETED.value,
        entry_count=1,
    )
    svc = _service(passes_repo=passes_repo)
    with pytest.raises(ValidationException):
        await svc.cancel_pass(contact_id="contact-1", pass_id="pass-1")


@pytest.mark.asyncio
async def test_cancel_pass_records_event():
    """Successful cancel records a cancelled timeline event."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo()
    passes_repo.row = _pass_row(status=PassStatus.ACTIVE.value)
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    result = await svc.cancel_pass(contact_id="contact-1", pass_id="pass-1")
    assert result["status"] == PassStatus.CANCELLED.value
    assert events_repo.insert_calls[-1]["event_type"] == "cancelled"


@pytest.mark.asyncio
async def test_get_pass_not_found():
    """Get raises not found when pass is missing or not owned."""
    passes_repo = _FakePassesRepo()
    passes_repo.row = None

    async def _none(**_kwargs):
        return None

    passes_repo.get_owned_by_contact = _none
    svc = _service(passes_repo=passes_repo)
    with pytest.raises(NotFoundException):
        await svc.get_pass(contact_id="contact-1", pass_id="missing")


@pytest.mark.asyncio
async def test_update_pass_rejects_non_editable():
    """Update rejects passes that are no longer editable."""
    passes_repo = _FakePassesRepo()
    passes_repo.row = _pass_row(status=PassStatus.CANCELLED.value)
    svc = _service(passes_repo=passes_repo)
    with pytest.raises(ValidationException):
        await svc.update_pass(
            contact_id="contact-1",
            pass_id="pass-1",
            body=UpdatePassRequest(guest_name="New Name"),
        )


@pytest.mark.asyncio
async def test_list_unit_passes_for_admin():
    """Admin unit pass list validates unit and maps created_by."""
    passes_repo = _FakePassesRepo()
    passes_repo.list_result = (
        [
            {
                **_pass_row(),
                "created_by": "Priya Verma",
            }
        ],
        1,
    )

    async def _list_by_unit(**_kwargs):
        return passes_repo.list_result

    passes_repo.list_by_unit = _list_by_unit  # type: ignore[attr-defined]

    contact_units_repo = _FakeContactUnitsRepo()
    svc = _service(passes_repo=passes_repo, contact_units_repo=contact_units_repo)
    items, total = await svc.list_unit_passes_for_admin(
        project_id="project-1",
        unit_id="unit-1",
        query=AdminUnitPassListQuery(),
    )
    assert total == 1
    assert items[0]["created_by"] == "Priya Verma"


@pytest.mark.asyncio
async def test_list_passes_returns_normalized_items():
    """List passes maps repository rows to list items."""
    passes_repo = _FakePassesRepo()
    passes_repo.list_result = ([_pass_row()], 1)
    svc = _service(passes_repo=passes_repo)

    items, total = await svc.list_passes(
        contact_id="contact-1",
        bucket="active",
        page=1,
        page_size=20,
    )

    assert total == 1
    assert items[0]["id"] == "pass-1"
    assert items[0]["display_status"] == PassDisplayStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_get_pass_includes_events():
    """Get pass loads timeline events when requested."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo()
    events_repo.events = [
        {
            "id": "event-1",
            "event_type": "created",
            "actor_type": "resident",
            "occurred_at": datetime.now(timezone.utc),
        }
    ]
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)

    result = await svc.get_pass(contact_id="contact-1", pass_id="pass-1", include_events=True)

    assert result["id"] == "pass-1"
    assert len(result["events"]) == 1
    assert result["events"][0]["event_type"] == "created"


@pytest.mark.asyncio
async def test_get_pass_without_events():
    """Get pass skips event lookup when include_events is false."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)

    result = await svc.get_pass(contact_id="contact-1", pass_id="pass-1", include_events=False)

    assert result["id"] == "pass-1"
    assert "events" not in result


@pytest.mark.asyncio
async def test_update_pass_empty_body_returns_current():
    """Update with no fields returns the existing pass unchanged."""
    passes_repo = _FakePassesRepo()
    svc = _service(passes_repo=passes_repo)

    result = await svc.update_pass(
        contact_id="contact-1",
        pass_id="pass-1",
        body=UpdatePassRequest(),
    )

    assert result["guest_name"] == "Ravi Kumar"


@pytest.mark.asyncio
async def test_update_pass_success():
    """Update persists changes and returns normalized pass."""
    passes_repo = _FakePassesRepo()
    passes_repo.update_result = _pass_row(guest_name="Updated Guest")
    svc = _service(passes_repo=passes_repo)

    result = await svc.update_pass(
        contact_id="contact-1",
        pass_id="pass-1",
        body=UpdatePassRequest(guest_name="Updated Guest", pass_type=PassType.DELIVERY),
    )

    assert result["guest_name"] == "Updated Guest"


@pytest.mark.asyncio
async def test_update_pass_not_found_after_update():
    """Update raises not found when repository returns no row."""
    passes_repo = _FakePassesRepo()

    async def _update_missing(**_kwargs):
        return None

    passes_repo.update = _update_missing  # type: ignore[method-assign]
    svc = _service(passes_repo=passes_repo)

    with pytest.raises(NotFoundException):
        await svc.update_pass(
            contact_id="contact-1",
            pass_id="pass-1",
            body=UpdatePassRequest(guest_name="Updated Guest"),
        )


@pytest.mark.asyncio
async def test_update_pass_validates_validity_window():
    """Update validates combined validity window."""
    passes_repo = _FakePassesRepo()
    svc = _service(passes_repo=passes_repo)
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationException):
        await svc.update_pass(
            contact_id="contact-1",
            pass_id="pass-1",
            body=UpdatePassRequest(
                valid_from=now + timedelta(hours=5),
                valid_until=now + timedelta(hours=1),
            ),
        )


@pytest.mark.asyncio
async def test_cancel_pass_repo_failure():
    """Cancel raises when repository cannot cancel the pass."""
    passes_repo = _FakePassesRepo()
    passes_repo.cancel_result = None
    svc = _service(passes_repo=passes_repo)

    with pytest.raises(ValidationException):
        await svc.cancel_pass(contact_id="contact-1", pass_id="pass-1")


@pytest.mark.asyncio
async def test_list_events_returns_normalized_timeline():
    """List events returns normalized pass timeline entries."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo()
    events_repo.events = [
        {
            "id": "event-1",
            "event_type": "check_in",
            "actor_type": "gate",
            "occurred_at": datetime.now(timezone.utc),
        }
    ]
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)

    events = await svc.list_events(contact_id="contact-1", pass_id="pass-1")

    assert len(events) == 1
    assert events[0]["event_type"] == "check_in"
