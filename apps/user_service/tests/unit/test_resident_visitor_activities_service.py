"""Unit tests for ResidentVisitorActivitiesService."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    PassType,
    VisitorLogVisitStatus,
    VisitorType,
)
from apps.user_service.app.services.resident_visitor_activities_service import (
    ResidentVisitorActivitiesService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

ORG_ID = "11111111-1111-4111-8111-111111111111"
CONTACT_ID = "22222222-2222-4222-8222-222222222222"
OTHER_CONTACT_ID = "33333333-3333-4333-8333-333333333333"
UNIT_ID = "44444444-4444-4444-8444-444444444444"
PROJECT_ID = "55555555-5555-4555-8555-555555555555"
PASS_ID = "66666666-6666-4666-8666-666666666666"
WALK_IN_ID = "77777777-7777-4777-8777-777777777777"


class _FakeContactUnitsRepo:
    def __init__(self, *, has_unit: bool = True) -> None:
        self.has_unit = has_unit

    async def contact_has_active_unit(self, **_kwargs) -> bool:
        return self.has_unit

    async def get_unit_project(self, **_kwargs) -> dict[str, Any] | None:
        if not self.has_unit:
            return None
        return {
            "id": UNIT_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "unit_code": "A-2102",
            "unit_label": "A-2102",
        }


class _FakeVisitorLogsService:
    def __init__(self) -> None:
        self.list_result: tuple[list[dict[str, Any]], int] = ([], 0)
        self.detail_result: dict[str, Any] = {}

    async def list_logs(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        self.last_list_kwargs = kwargs
        return self.list_result

    async def get_log_detail(self, **kwargs) -> dict[str, Any]:
        self.last_detail_kwargs = kwargs
        return self.detail_result


def _service(
    *,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
    visitor_logs: _FakeVisitorLogsService | None = None,
) -> ResidentVisitorActivitiesService:
    svc = ResidentVisitorActivitiesService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="resident@example.com",
            organization_id=ORG_ID,
            user_type="resident",
        ),
    )
    svc.contact_units_repo = contact_units_repo or _FakeContactUnitsRepo()
    fake_logs = visitor_logs or _FakeVisitorLogsService()
    svc._visitor_logs = fake_logs
    return svc


@pytest.mark.asyncio
async def test_list_activities_requires_unit_access():
    svc = _service(contact_units_repo=_FakeContactUnitsRepo(has_unit=False))
    with pytest.raises(ValidationException):
        await svc.list_activities(contact_id=CONTACT_ID, unit_id=UNIT_ID)


@pytest.mark.asyncio
async def test_list_activities_applies_private_filter_and_maps_rows():
    logs = _FakeVisitorLogsService()
    logs.list_result = (
        [
            {
                "source": "pass",
                "pass_id": PASS_ID,
                "pass_type": PassType.GUEST.value,
                "sub_type": None,
                "guest_name": "Ravi Kumar",
                "visit_status": VisitorLogVisitStatus.EXITED.value,
                "visitor_type": VisitorType.GUEST.value,
                "is_private": False,
                "visitor_photo_urls": [],
                "vehicle_photo_urls": [],
            },
            {
                "source": "walk_in",
                "pass_id": WALK_IN_ID,
                "pass_type": PassType.DELIVERY.value,
                "sub_type": "Amazon",
                "guest_name": "Courier",
                "visit_status": VisitorLogVisitStatus.INSIDE.value,
                "visitor_type": VisitorType.VISITOR.value,
                "is_private": False,
                "visitor_photo_urls": ["https://example.com/photo.jpg"],
                "vehicle_photo_urls": [],
            },
        ],
        2,
    )
    svc = _service(visitor_logs=logs)

    items, total = await svc.list_activities(contact_id=CONTACT_ID, unit_id=UNIT_ID)

    assert total == 2
    assert logs.last_list_kwargs["unit_id"] == UNIT_ID
    assert logs.last_list_kwargs["project_id"] == PROJECT_ID
    assert logs.last_list_kwargs["visible_to_contact_id"] == CONTACT_ID
    assert items[0]["type"] == PassType.GUEST.value
    assert items[1]["type"] == PassType.DELIVERY.value
    assert items[1]["sub_type"] == "Amazon"
    assert "guard_name" not in items[0]


@pytest.mark.asyncio
async def test_get_pass_detail_hides_private_pass_from_other_contact():
    logs = _FakeVisitorLogsService()
    logs.detail_result = {
        "source": "pass",
        "id": PASS_ID,
        "unit_id": UNIT_ID,
        "pass_type": PassType.GUEST.value,
        "guest_name": "Private Guest",
        "guest_phone_isd_code": "+91",
        "guest_phone_number": "9876543210",
        "validity_type": "one_time",
        "allow_multiple_entries": False,
        "is_private": True,
        "entry_count": 0,
        "status": "active",
        "display_status": "active",
        "code": "4821",
        "host_contact_id": OTHER_CONTACT_ID,
        "visit_status": VisitorLogVisitStatus.APPROVED.value,
        "visitor_type": VisitorType.GUEST.value,
        "events": [],
        "image_urls": [],
    }
    svc = _service(visitor_logs=logs)

    with pytest.raises(NotFoundException):
        await svc.get_activity_detail(
            contact_id=CONTACT_ID,
            unit_id=UNIT_ID,
            activity_id=PASS_ID,
        )


@pytest.mark.asyncio
async def test_get_daily_help_detail_allows_null_unit_when_household_linked():
    logs = _FakeVisitorLogsService()
    logs.detail_result = {
        "source": "pass",
        "id": PASS_ID,
        "unit_id": None,
        "pass_type": PassType.DAILY_HELP.value,
        "guest_name": "Maid",
        "guest_phone_isd_code": "+91",
        "guest_phone_number": "9876543210",
        "validity_type": "recurring",
        "allow_multiple_entries": True,
        "is_private": False,
        "entry_count": 1,
        "status": "active",
        "display_status": "active",
        "code": "1234",
        "daily_help_category_name": "Housekeeping",
        "visit_status": VisitorLogVisitStatus.INSIDE.value,
        "visitor_type": VisitorType.VISITOR.value,
        "events": [],
        "image_urls": [],
    }
    svc = _service(visitor_logs=logs)
    svc._visitor_logs.passes_repo = MagicMock()
    svc._visitor_logs.passes_repo.get_by_id = AsyncMock(return_value={"daily_help_id": "profile-1"})
    svc.daily_help_repo = MagicMock()
    svc.daily_help_repo.has_active_household_link = AsyncMock(return_value=True)

    detail = await svc.get_activity_detail(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        activity_id=PASS_ID,
    )

    assert detail["type"] == PassType.DAILY_HELP.value
    assert detail["unit_id"] == UNIT_ID


@pytest.mark.asyncio
async def test_get_pass_detail_returns_resident_shape_for_creator():
    logs = _FakeVisitorLogsService()
    logs.detail_result = {
        "source": "pass",
        "id": PASS_ID,
        "unit_id": UNIT_ID,
        "pass_type": PassType.DAILY_HELP.value,
        "guest_name": "Maid",
        "guest_phone_isd_code": "+91",
        "guest_phone_number": "9876543210",
        "validity_type": "recurring",
        "allow_multiple_entries": True,
        "is_private": False,
        "entry_count": 1,
        "status": "active",
        "display_status": "active",
        "code": "1234",
        "daily_help_category_name": "Housekeeping",
        "visit_status": VisitorLogVisitStatus.EXITED.value,
        "visitor_type": VisitorType.VISITOR.value,
        "time_spent_minutes": 45,
        "events": [],
        "image_urls": [],
    }
    svc = _service(visitor_logs=logs)

    detail = await svc.get_activity_detail(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        activity_id=PASS_ID,
    )

    assert detail["source"] == "pass"
    assert detail["type"] == PassType.DAILY_HELP.value
    assert detail["sub_type"] == "Housekeeping"
    assert detail["pass_code"] == "1234"
    assert "guard_name" not in detail


@pytest.mark.asyncio
async def test_get_walk_in_detail_scopes_visit_unit_to_flat():
    logs = _FakeVisitorLogsService()
    logs.detail_result = {
        "source": "walk_in",
        "id": WALK_IN_ID,
        "type": PassType.DELIVERY.value,
        "sub_type": "Swiggy",
        "visitor_first_name": "Ravi",
        "visitor_last_name": "Delivery",
        "visitor_phone_isd_code": "+91",
        "visitor_phone_number": "9876501234",
        "status": "entered",
        "flats_count": 2,
        "requested_at": datetime(2026, 8, 7, 9, 15, tzinfo=timezone.utc).isoformat(),
        "entered_at": datetime(2026, 8, 7, 9, 22, tzinfo=timezone.utc).isoformat(),
        "exited_at": None,
        "visit_status": VisitorLogVisitStatus.INSIDE.value,
        "visitor_type": VisitorType.VISITOR.value,
        "time_spent_minutes": 10,
        "visitor_photo_urls": [],
        "vehicle_photo_urls": [],
        "image_urls": [],
        "visit_units": [
            {
                "id": "vu-1",
                "tower_id": "tower-1",
                "unit_id": UNIT_ID,
                "status": "approved",
                "sort_order": 0,
            },
            {
                "id": "vu-2",
                "tower_id": "tower-1",
                "unit_id": "other-unit",
                "status": "awaiting",
                "sort_order": 1,
            },
        ],
        "events": [],
        "milestones": [],
    }
    svc = _service(visitor_logs=logs)

    detail = await svc.get_activity_detail(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        activity_id=WALK_IN_ID,
    )

    assert detail["source"] == "walk_in"
    assert detail["visit_unit"]["unit_id"] == UNIT_ID
    assert "visit_units" not in detail


def test_normalize_list_item_maps_allowed_by_and_entries_on_day():
    guest_row = {
        "source": "pass",
        "pass_id": PASS_ID,
        "pass_type": PassType.GUEST.value,
        "guest_name": "Guest",
        "visit_status": VisitorLogVisitStatus.INSIDE.value,
        "visitor_type": VisitorType.GUEST.value,
        "resident": {
            "contact_id": CONTACT_ID,
            "person_name": "Shakti",
            "role": "Owner",
        },
    }
    daily_help_row = {
        "source": "pass",
        "pass_id": PASS_ID,
        "pass_type": PassType.DAILY_HELP.value,
        "guest_name": "Rupesh Kumar",
        "visit_status": VisitorLogVisitStatus.EXITED.value,
        "visitor_type": VisitorType.VISITOR.value,
        "daily_check_in_count": 2,
    }

    guest_item = ResidentVisitorActivitiesService._normalize_list_item(guest_row)
    daily_help_item = ResidentVisitorActivitiesService._normalize_list_item(daily_help_row)

    assert guest_item["allowed_by"] == {
        "contact_id": CONTACT_ID,
        "person_name": "Shakti",
        "role": "Owner",
    }
    assert guest_item["entries_on_day"] is None
    assert daily_help_item["allowed_by"] is None
    assert daily_help_item["entries_on_day"] == 2
