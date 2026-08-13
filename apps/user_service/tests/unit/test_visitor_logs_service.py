"""Unit tests for VisitorLogsService."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.enums import (
    PassEventType,
    PassType,
    VisitorLogVisitStatus,
    VisitorType,
    WalkInEventType,
    WalkInStatus,
)
from apps.user_service.app.services.passes_service import PassesService
from apps.user_service.app.services.visitor_logs_service import VisitorLogsService
from apps.user_service.app.utils.common_utils import UserContext


def _user_context() -> UserContext:
    """Build an admin user context for visitor log tests."""
    return UserContext(
        user_id="admin-1",
        email="admin@example.com",
        organization_id="org-1",
    )


class _FakeLogsRepo:
    """In-memory fake for VisitorLogsRepository."""

    def __init__(self):
        self.list_result: tuple[list[dict[str, Any]], int] = ([], 0)
        self.overview_result = {
            "start_at": "2026-06-01T00:00:00Z",
            "end_at": "2026-06-30T00:00:00Z",
            "total_entries": 0,
            "inside_now": 0,
            "awaiting_approval": 0,
            "walk_ins": 0,
            "denied_expired": 0,
        }

    async def list_logs(self, **_kwargs):
        """Return configured list result."""
        return self.list_result

    async def get_overview(self, **_kwargs):
        """Return configured overview result."""
        return self.overview_result


class _FakePassesRepo:
    """In-memory fake for org-scoped pass fetch."""

    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row

    async def get_by_id(self, **_kwargs):
        """Return configured pass row."""
        return self.row


class _FakeEventsRepo:
    """In-memory fake for pass events."""

    def __init__(self, events: list[dict[str, Any]] | None = None):
        self.events = events or []

    async def list_by_pass(self, **_kwargs):
        """Return configured events."""
        return self.events


class _FakeMembersRepo:
    """In-memory fake for OrganizationMemberRepository."""

    def __init__(self, profile: dict[str, Any] | None = None):
        self.profile = profile or {
            "salutation": "Mr",
            "first_name": "Ajay",
            "last_name": "Guard",
        }

    async def get_user_profile_by_id(self, *, user_id: str, organization_id: str | None = None):
        del user_id, organization_id
        return self.profile


class _FakeUnitsRepo:
    """In-memory fake for UnitsRepository resident lookups."""

    def __init__(
        self,
        *,
        residents: dict[str, dict[str, Any] | None] | None = None,
        names_by_id: dict[str, str] | None = None,
        default: dict[str, Any] | None = None,
    ):
        self.residents = residents or {}
        self.names_by_id = names_by_id or {}
        self.default = default

    async def get_contact_residents_batch(
        self,
        *,
        organization_id: str,
        contact_unit_pairs: list[tuple[str, str]],
    ):
        del organization_id
        result: dict[str, dict[str, Any] | None] = {}
        for contact_id, unit_id in contact_unit_pairs:
            key = UnitsRepository._resident_pair_key(contact_id, unit_id)
            result[key] = self.residents.get(key, self.default)
        return result

    async def get_contact_person_names_batch(
        self,
        *,
        organization_id: str,
        contact_ids: list[str],
    ):
        del organization_id
        return {
            contact_id: self.names_by_id[contact_id]
            for contact_id in contact_ids
            if contact_id in self.names_by_id
        }


def _service(
    *,
    logs_repo: _FakeLogsRepo | None = None,
    passes_repo: _FakePassesRepo | None = None,
    events_repo: _FakeEventsRepo | None = None,
    members_repo: _FakeMembersRepo | None = None,
    units_repo: _FakeUnitsRepo | None = None,
) -> VisitorLogsService:
    """Build VisitorLogsService with fake repositories."""
    svc = VisitorLogsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.logs_repo = logs_repo or _FakeLogsRepo()
    svc.units_repo = units_repo or _FakeUnitsRepo()
    svc.passes_repo = passes_repo or _FakePassesRepo()
    svc.events_repo = events_repo or _FakeEventsRepo()
    svc._members_repo = members_repo or _FakeMembersRepo()
    return svc


@pytest.mark.asyncio
async def test_list_logs_shapes_time_spent():
    """List logs derives time_spent_minutes from in/out timestamps."""
    in_time = datetime(2026, 6, 9, 9, 12, tzinfo=timezone.utc)
    out_time = datetime(2026, 6, 9, 9, 18, tzinfo=timezone.utc)
    logs_repo = _FakeLogsRepo()
    logs_repo.list_result = (
        [
            {
                "source": "pass",
                "pass_id": "pass-1",
                "pass_type": "delivery",
                "unit_label": "B-1204",
                "tower_name": "Tower B",
                "created_by": "T. Nair",
                "scheduled_from": in_time,
                "scheduled_until": out_time,
                "validity_type": "one_time",
                "entry_method": "qr",
                "guard_user_id": "guard-1",
                "guard_first_name": "Ramesh",
                "guard_last_name": "Kumar",
                "access_status": "approved",
                "visit_status": VisitorLogVisitStatus.EXITED.value,
                "pass_code": "4821",
                "is_private": False,
                "in_time": in_time,
                "out_time": out_time,
                "pass_image_path": "org/passes/pass-1.png",
                "guest_phone_isd_code": "+91",
                "guest_phone_number": "9876543210",
                "resident_contact_id": "owner-1",
                "resident_person_name": "Ms. Radhi Sharma",
                "resident_role": "Owner",
            }
        ],
        1,
    )
    svc = _service(logs_repo=logs_repo)
    start_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 30, tzinfo=timezone.utc)
    items, total = await svc.list_logs(start_at=start_at, end_at=end_at)
    assert total == 1
    assert items[0]["source"] == "pass"
    assert items[0]["time_spent_minutes"] == 6
    assert items[0]["pass_image_url"].endswith("org/passes/pass-1.png")
    assert items[0]["visitor_photo_urls"] == []
    assert items[0]["vehicle_photo_urls"] == []
    assert items[0]["created_by"] == "T. Nair"
    assert items[0]["validity_type"] == "one_time"
    assert items[0]["guard_user_id"] == "guard-1"
    assert items[0]["guard_name"] == "Ramesh Kumar"
    assert items[0]["visitor_phone_isd_code"] == "+91"
    assert items[0]["visitor_phone_number"] == "9876543210"
    assert items[0]["resident"] == {
        "contact_id": "owner-1",
        "person_name": "Ms. Radhi Sharma",
        "role": "Owner",
    }
    assert items[0]["visit_status"] == VisitorLogVisitStatus.EXITED.value
    assert items[0]["visitor_type"] == VisitorType.VISITOR.value
    assert items[0]["pass_code"] == "4821"
    assert items[0]["is_private"] is False
    assert "guard_first_name" not in items[0]
    assert "guard_last_name" not in items[0]


@pytest.mark.asyncio
async def test_list_logs_formats_walk_in_unit_label():
    """Walk-in rows append a multi-flat suffix to unit_label."""
    in_time = datetime(2026, 6, 9, 9, 12, tzinfo=timezone.utc)
    logs_repo = _FakeLogsRepo()
    logs_repo.list_result = (
        [
            {
                "source": "walk_in",
                "pass_id": "walk-in-1",
                "pass_type": "walk_in",
                "guest_name": "Ravi Delivery",
                "unit_label": "A-2102",
                "tower_name": "Tower A",
                "created_by": "Guard One",
                "scheduled_from": in_time,
                "scheduled_until": None,
                "validity_type": None,
                "entry_method": "manual",
                "guard_user_id": "guard-1",
                "guard_first_name": "Ramesh",
                "guard_last_name": "Kumar",
                "access_status": "granted",
                "visit_status": VisitorLogVisitStatus.INSIDE.value,
                "pass_code": None,
                "is_private": False,
                "in_time": in_time,
                "out_time": None,
                "flats_count": 3,
                "visitor_phone_isd_code": "+91",
                "visitor_phone_number": "9876501234",
                "resident_contact_id": "owner-1",
                "resident_person_name": "Ms. Radhi Sharma",
                "resident_role": "Owner",
                "visitor_photo_paths": ["org/walk-ins/photo-1.jpg"],
                "vehicle_photo_paths": [],
            }
        ],
        1,
    )
    svc = _service(logs_repo=logs_repo)
    items, _ = await svc.list_logs()
    assert items[0]["source"] == "walk_in"
    assert items[0]["unit_label"] == "A-2102 (+2 more)"
    assert items[0]["validity_type"] is None
    assert len(items[0]["visitor_photo_urls"]) == 1
    assert items[0]["visitor_photo_urls"][0].endswith("org/walk-ins/photo-1.jpg")
    assert items[0]["vehicle_photo_urls"] == []
    assert items[0]["pass_image_url"] is None
    assert items[0]["visitor_phone_isd_code"] == "+91"
    assert items[0]["visitor_phone_number"] == "9876501234"
    assert items[0]["resident"]["role"] == "Owner"
    assert items[0]["resident"]["person_name"] == "Ms. Radhi Sharma"
    assert items[0]["visit_status"] == VisitorLogVisitStatus.INSIDE.value
    assert items[0]["visitor_type"] == VisitorType.VISITOR.value
    assert items[0]["pass_code"] is None


@pytest.mark.asyncio
async def test_list_logs_guest_visitor_type():
    """Guest pass rows map to visitor_type guest."""
    logs_repo = _FakeLogsRepo()
    logs_repo.list_result = (
        [
            {
                "source": "pass",
                "pass_id": "pass-guest",
                "pass_type": PassType.GUEST.value,
                "visit_status": VisitorLogVisitStatus.APPROVED.value,
                "is_private": True,
            }
        ],
        1,
    )
    svc = _service(logs_repo=logs_repo)
    items, _ = await svc.list_logs()
    assert items[0]["visitor_type"] == VisitorType.GUEST.value
    assert items[0]["is_private"] is True


@pytest.mark.asyncio
async def test_get_overview():
    """Overview returns repository aggregates unchanged."""
    logs_repo = _FakeLogsRepo()
    logs_repo.overview_result = {
        "start_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "end_at": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "total_entries": 28,
        "inside_now": 3,
        "awaiting_approval": 1,
        "walk_ins": 6,
        "exited": 5,
        "denied_expired": 3,
    }
    svc = _service(logs_repo=logs_repo)
    result = await svc.get_overview(
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    assert result["total_entries"] == 28
    assert result["walk_ins"] == 6
    assert result["denied_expired"] == 3
    assert result["start_at"].startswith("2026-06-01T00:00:00")
    assert result["end_at"].startswith("2026-06-30T00:00:00")


@pytest.mark.asyncio
async def test_get_log_detail_returns_pass_timeline():
    """Detail view should merge pass row with normalized events and log fields."""
    pass_row = {
        "id": "pass-1",
        "unit_id": "unit-1",
        "created_by_contact_id": "owner-1",
        "pass_type": "guest",
        "status": "approved",
        "creator_first_name": "Radhi",
        "creator_last_name": "Sharma",
    }
    events = [
        {
            "id": "evt-1",
            "event_type": PassEventType.CHECKED_IN.value,
            "actor_user_id": "guard-1",
            "occurred_at": datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
            "entry_method": "code",
        },
        {
            "id": "evt-2",
            "event_type": PassEventType.CHECKED_OUT.value,
            "actor_user_id": "guard-1",
            "occurred_at": datetime(2026, 6, 9, 9, 6, tzinfo=timezone.utc),
        },
    ]
    passes_repo = _FakePassesRepo(row=pass_row)
    events_repo = _FakeEventsRepo(events=events)
    units_repo = _FakeUnitsRepo(
        residents={
            UnitsRepository._resident_pair_key("owner-1", "unit-1"): {
                "contact_id": "owner-1",
                "person_name": "Ms. Radhi Sharma",
                "role": "Owner",
            }
        }
    )
    svc = _service(passes_repo=passes_repo, events_repo=events_repo, units_repo=units_repo)
    svc._passes_service = type(
        "Passes",
        (),
        {
            "_normalize_event": staticmethod(lambda row: {**row, "normalized": True}),
            "_normalize_pass": staticmethod(
                lambda row, events=None, include_events=False: {
                    **row,
                    "created_by_contact_id": "owner-1",
                    "guest_phone_isd_code": "+91",
                    "guest_phone_number": "9876543210",
                    "vehicle_number": "MH12AB1234",
                    "pass_image_path": "org/passes/pass-4821.png",
                    "events": events or [],
                    "include_events": include_events,
                }
            ),
        },
    )()
    detail = await svc.get_log_detail(pass_id="pass-1")
    assert detail["source"] == "pass"
    assert detail["id"] == "pass-1"
    assert detail["include_events"] is True
    assert detail["events"][0]["normalized"] is True
    assert detail["created_by"] == "Radhi Sharma"
    assert detail["guard_user_id"] == "guard-1"
    assert detail["guard_name"] == "Mr Ajay Guard"
    assert detail["pass_image_url"].endswith("org/passes/pass-4821.png")
    assert detail["image_urls"] == [detail["pass_image_url"]]
    assert detail["visitor_phone_isd_code"] == "+91"
    assert detail["visitor_phone_number"] == "9876543210"
    assert detail["resident"]["contact_id"] == "owner-1"
    assert detail["resident"]["role"] == "Owner"
    assert detail["visit_status"] == VisitorLogVisitStatus.EXITED.value
    assert detail["visitor_type"] == VisitorType.GUEST.value
    assert detail["entry_method"] == "code"
    assert detail["vehicle_number"] == "MH12AB1234"
    assert detail["time_spent_minutes"] == 6


@pytest.mark.asyncio
async def test_get_log_detail_returns_walk_in_when_pass_missing():
    """Detail view falls back to walk-in entry by the same id."""
    passes_repo = _FakePassesRepo(row=None)

    class _FakeWalkInRepo:
        async def get_entry(self, **_kwargs):
            return {
                "id": "walk-in-1",
                "project_id": "project-1",
                "requested_by_user_id": "guard-1",
            }

        async def list_visit_units(self, **_kwargs):
            return [
                {
                    "id": "vu-1",
                    "unit_id": "unit-1",
                    "approved_by_contact_id": "owner-1",
                }
            ]

        async def list_events(self, **_kwargs):
            return [
                {
                    "id": "evt-enter-1",
                    "event_type": WalkInEventType.ENTERED.value,
                    "actor_user_id": "guard-1",
                    "occurred_at": datetime(2026, 8, 7, 9, 22, tzinfo=timezone.utc),
                }
            ]

    class _FakeWalkInService:
        def __init__(self):
            self.repo = _FakeWalkInRepo()

        async def _serialize_detail(self, row):
            return {
                "id": row["id"],
                "project_id": row["project_id"],
                "status": "exited",
                "entered_at": "2026-08-07T09:22:00+00:00",
                "exited_at": "2026-08-07T10:05:00+00:00",
                "visitor_phone_isd_code": "+91",
                "visitor_phone_number": "9876501234",
                "visitor_photo_paths": ["org/walk-ins/photo-1.jpg"],
                "vehicle_photo_paths": ["org/walk-ins/vehicle-1.jpg"],
                "visit_units": [
                    {
                        "id": "vu-1",
                        "tower_id": "tower-1",
                        "unit_id": "unit-1",
                        "unit_label": "A-2102",
                        "status": "approved",
                        "sort_order": 0,
                    }
                ],
                "events": [
                    {
                        "id": "evt-enter-1",
                        "event_type": WalkInEventType.ENTERED.value,
                        "actor_label": "Mr Ajay Guard",
                    }
                ],
            }

    units_repo = _FakeUnitsRepo(
        residents={
            UnitsRepository._resident_pair_key("owner-1", "unit-1"): {
                "contact_id": "owner-1",
                "person_name": "Ms. Radhi Sharma",
                "role": "Owner",
            }
        }
    )
    svc = _service(passes_repo=passes_repo, units_repo=units_repo)
    svc._walk_in_service = _FakeWalkInService()
    detail = await svc.get_log_detail(pass_id="walk-in-1")
    assert detail["source"] == "walk_in"
    assert detail["id"] == "walk-in-1"
    assert detail["status"] == "exited"
    assert detail["created_by"] == "Mr Ajay Guard"
    assert detail["guard_user_id"] == "guard-1"
    assert detail["guard_name"] == "Mr Ajay Guard"
    assert detail["visitor_phone_isd_code"] == "+91"
    assert detail["visitor_phone_number"] == "9876501234"
    assert detail["resident"]["role"] == "Owner"
    assert detail["visit_units"][0]["resident"]["person_name"] == "Ms. Radhi Sharma"
    assert len(detail["visitor_photo_urls"]) == 1
    assert detail["visitor_photo_urls"][0].endswith("org/walk-ins/photo-1.jpg")
    assert len(detail["vehicle_photo_urls"]) == 1
    assert detail["image_urls"] == detail["visitor_photo_urls"] + detail["vehicle_photo_urls"]
    assert detail["visit_status"] == VisitorLogVisitStatus.EXITED.value
    assert detail["visitor_type"] == VisitorType.VISITOR.value
    assert detail["entry_method"] == "manual"
    assert detail["vehicle_number"] is None
    assert detail["time_spent_minutes"] == 43


@pytest.mark.asyncio
async def test_get_log_detail_guard_name_from_actor_label_when_user_id_missing():
    """Detail guard fields should fall back to actor_label like the list API."""
    pass_row = {
        "id": "pass-1",
        "unit_id": "unit-1",
        "created_by_contact_id": "owner-1",
        "pass_type": "guest",
        "status": "approved",
    }
    events = [
        {
            "id": "evt-1",
            "event_type": PassEventType.CHECKED_IN.value,
            "actor_label": "Gate Guard Sharma",
            "occurred_at": datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
        },
    ]
    passes_repo = _FakePassesRepo(row=pass_row)
    events_repo = _FakeEventsRepo(events=events)
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    svc._passes_service = type(
        "Passes",
        (),
        {
            "_normalize_event": staticmethod(lambda row: row),
            "_normalize_pass": staticmethod(
                lambda row, events=None, include_events=False: {**row, "events": events or []}
            ),
        },
    )()
    detail = await svc.get_log_detail(pass_id="pass-1")
    assert detail["guard_user_id"] is None
    assert detail["guard_name"] == "Gate Guard Sharma"


def test_pass_visit_status_exited_with_check_in_and_out():
    """Pass visit_status is exited when both check-in and check-out exist."""
    events = [
        {
            "event_type": PassEventType.CHECKED_IN.value,
            "occurred_at": datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
        },
        {
            "event_type": PassEventType.CHECKED_OUT.value,
            "occurred_at": datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
        },
    ]
    status = VisitorLogsService._pass_visit_status(
        detail={"events": events, "status": "active", "valid_until": None}
    )
    assert status == VisitorLogVisitStatus.EXITED.value


def test_pass_visit_status_expired_without_check_in():
    """Pass visit_status is expired when validity ended without gate entry."""
    status = VisitorLogsService._pass_visit_status(
        detail={
            "events": [],
            "status": "active",
            "valid_until": datetime(2026, 6, 1, tzinfo=timezone.utc),
        }
    )
    assert status == VisitorLogVisitStatus.EXPIRED.value


def test_walk_in_visit_status_maps_awaiting():
    """Walk-in awaiting maps to awaiting_approval visit_status."""
    assert (
        VisitorLogsService._walk_in_visit_status(status=WalkInStatus.AWAITING.value)
        == VisitorLogVisitStatus.AWAITING_APPROVAL.value
    )


@pytest.mark.asyncio
async def test_get_log_detail_shows_creator_without_unit_role():
    """Pass detail shows the creator even when they lack a household role on the flat."""
    pass_row = {
        "id": "pass-rasika",
        "unit_id": "unit-t15103",
        "created_by_contact_id": "rasika-contact",
        "pass_type": "guest",
        "status": "expired",
        "creator_first_name": "Rasika",
        "creator_last_name": "Bharati",
    }
    passes_repo = _FakePassesRepo(row=pass_row)
    events_repo = _FakeEventsRepo(events=[])
    units_repo = _FakeUnitsRepo(
        residents={
            UnitsRepository._resident_pair_key("rasika-contact", "unit-t15103"): None,
        },
        names_by_id={"rasika-contact": "Rasika Bharati"},
    )
    svc = _service(passes_repo=passes_repo, events_repo=events_repo, units_repo=units_repo)
    svc._passes_service = type(
        "Passes",
        (),
        {
            "_normalize_event": staticmethod(lambda row: row),
            "_normalize_pass": staticmethod(
                lambda row, events=None, include_events=False: {
                    **row,
                    "creator_first_name": "Rasika",
                    "creator_last_name": "Bharati",
                    "events": events or [],
                    "include_events": include_events,
                }
            ),
        },
    )()
    detail = await svc.get_log_detail(pass_id="pass-rasika")
    assert detail["resident"]["person_name"] == "Rasika Bharati"
    assert detail["resident"]["role"] is None
    assert detail["visit_status"] == VisitorLogVisitStatus.EXPIRED.value


@pytest.mark.asyncio
async def test_get_log_detail_daily_help_pass_null_unit_id():
    """Daily help pass detail must not query residents with literal 'None' unit_id."""
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    pass_row = {
        "id": "pass-dh-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": None,
        "host_contact_id": None,
        "created_by_contact_id": None,
        "pass_type": PassType.DAILY_HELP.value,
        "guest_name": "Maid",
        "guest_phone_isd_code": "+91",
        "guest_phone_number": "9876543210",
        "visitor_count": 1,
        "vehicle_number": None,
        "purpose": None,
        "valid_from": now,
        "valid_until": now,
        "validity_type": "recurring",
        "allow_multiple_entries": True,
        "is_private": False,
        "max_entries": None,
        "entry_count": 0,
        "status": "active",
        "code": "1234",
        "pass_image_path": None,
        "notes": None,
        "unit_code": None,
        "unit_label": None,
        "tower_name": None,
        "floor_name": None,
        "config_label": None,
        "created_at": now,
        "updated_at": now,
    }

    class _TrackingUnitsRepo(_FakeUnitsRepo):
        async def get_contact_residents_batch(
            self,
            *,
            organization_id: str,
            contact_unit_pairs: list[tuple[str, str]],
        ):
            del organization_id
            for _, unit_id in contact_unit_pairs:
                assert unit_id != "None"
            return {}

    passes_repo = _FakePassesRepo(row=pass_row)
    events_repo = _FakeEventsRepo(events=[])
    svc = _service(
        passes_repo=passes_repo,
        events_repo=events_repo,
        units_repo=_TrackingUnitsRepo(),
    )
    svc._passes_service = PassesService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )

    detail = await svc.get_log_detail(pass_id="pass-dh-1")
    assert detail["source"] == "pass"
    assert detail["unit_id"] is None
    assert detail["resident"] is None
    assert detail["visitor_type"] == VisitorType.VISITOR.value


def test_build_resident_without_unit_role():
    """Creator/approver name is returned even when role on the flat is missing."""
    resident = VisitorLogsService._build_resident(
        contact_id="rasika-contact",
        person_name="Rasika Bharati",
        role=None,
    )
    assert resident == {
        "contact_id": "rasika-contact",
        "person_name": "Rasika Bharati",
        "role": None,
    }


def test_build_resident_accepts_family_role():
    """Family members who approve or request visits are included in resident."""
    resident = VisitorLogsService._build_resident(
        contact_id="contact-1",
        person_name="Namita K",
        role="Family",
    )
    assert resident == {
        "contact_id": "contact-1",
        "person_name": "Namita K",
        "role": "Family",
    }


def test_public_media_url_keeps_absolute_urls():
    """Absolute URLs should pass through unchanged."""
    url = VisitorLogsService._public_media_url("https://cdn.example.com/photo.jpg")
    assert url == "https://cdn.example.com/photo.jpg"
