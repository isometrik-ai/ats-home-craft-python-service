"""Unit tests for DailyHelpService helpers and orchestration."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.daily_help import (
    CreateDailyHelpRequest,
    ResidentDailyHelpListQuery,
    SetDailyHelpOpenToWorkRequest,
)
from apps.user_service.app.schemas.enums import (
    DailyHelpStatus,
    PassType,
    VisitorType,
)
from apps.user_service.app.services.daily_help_service import DailyHelpService
from apps.user_service.app.services.visitor_logs_service import VisitorLogsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)


def _user_context() -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="staff@example.com",
        organization_id="org-1",
    )


class _FakePushDispatcher:
    def __init__(self) -> None:
        self.org_calls: list[dict[str, Any]] = []
        self.user_calls: list[dict[str, Any]] = []

    async def send_to_org_members(self, **kwargs):
        self.org_calls.append(kwargs)
        return 1

    async def send_to_user(self, **kwargs):
        self.user_calls.append(kwargs)
        return None


def test_build_display_name_includes_initials_and_parts():
    """Display name joins non-empty name parts."""
    name = DailyHelpService._build_display_name(
        initials="Mrs.",
        first_name="Lakshmi",
        middle_name=None,
        last_name="Devi",
    )
    assert name == "Mrs. Lakshmi Devi"


def test_mask_phone_number_hides_all_but_last_four():
    """Resident directory masks phone numbers."""
    assert DailyHelpService._mask_phone_number("9655011223") == "XXXXXX1223"


def test_visitor_type_daily_help_maps_to_visitor():
    """Daily help pass rows use visitor type in logs."""
    visitor_type = VisitorLogsService._visitor_type_from_row(
        {"pass_type": PassType.DAILY_HELP.value}
    )
    assert visitor_type == VisitorType.VISITOR.value


@pytest.mark.asyncio
async def test_create_profile_issues_pass_and_links():
    """Create profile inserts pass and links linked_pass_id."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.categories_repo = MagicMock()
    svc.categories_repo.get_by_id = AsyncMock(
        return_value={
            "id": "cat-1",
            "name": "Maid",
            "status": "active",
        }
    )
    svc.repo = MagicMock()
    svc.repo.generate_unique_passcode = AsyncMock(return_value="4821")
    svc.repo.insert_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "display_name": "Mrs. Lakshmi Devi",
            "category_id": "cat-1",
            "category_name": "Maid",
            "gate_passcode": "4821",
            "status": DailyHelpStatus.ACTIVE.value,
            "phone_isd_code": "+91",
            "phone_number": "9655011223",
            "photo_path": "org/photo.jpg",
        }
    )
    svc.repo.insert_document = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.repo.link_pass_id = AsyncMock(return_value={"linked_pass_id": "pass-1"})
    svc.passes_repo = MagicMock()
    svc.passes_repo.insert_daily_help = AsyncMock(return_value={"id": "pass-1"})
    svc._resolve_created_by_name = AsyncMock(return_value="Admin User")

    body = CreateDailyHelpRequest(
        initials="Mrs.",
        first_name="Lakshmi",
        last_name="Devi",
        phone_isd_code="+91",
        phone_number="9655011223",
        category_id="cat-1",
    )
    result = await svc.create_profile(project_id="project-1", body=body)

    assert result.id == "profile-1"
    assert result.gate_passcode == "4821"
    insert_kwargs = svc.repo.insert_profile.await_args.kwargs
    assert insert_kwargs["open_to_work"] is True
    svc.passes_repo.insert_daily_help.assert_awaited_once()
    pass_payload = svc.passes_repo.insert_daily_help.await_args.args[0]
    assert pass_payload["pass_type"] == PassType.DAILY_HELP.value
    assert pass_payload["code"] == "4821"
    svc.repo.link_pass_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_deactivate_cancels_linked_pass():
    """Deactivate sets profile inactive and cancels linked pass."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.ACTIVE.value,
            "linked_pass_id": "pass-1",
        }
    )
    svc.repo.update_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.INACTIVE.value,
            "linked_pass_id": "pass-1",
        }
    )
    svc.repo.insert_event = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.cancel_by_pass_id = AsyncMock(return_value={"id": "pass-1"})

    result = await svc.deactivate_profile(project_id="project-1", profile_id="profile-1")

    assert result.status == DailyHelpStatus.INACTIVE.value
    svc.passes_repo.cancel_by_pass_id.assert_awaited_once_with(
        organization_id="org-1",
        pass_id="pass-1",
    )


@pytest.mark.asyncio
async def test_reactivate_reissues_pass_when_cancelled():
    """Reactivate sets profile active and re-issues pass when linked pass is cancelled."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.INACTIVE.value,
            "linked_pass_id": "pass-1",
            "display_name": "Helper",
            "phone_isd_code": "+91",
            "phone_number": "9999999999",
            "gate_passcode": "4821",
            "photo_path": None,
        }
    )
    svc.repo.update_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.ACTIVE.value,
            "linked_pass_id": "pass-1",
            "display_name": "Helper",
            "phone_isd_code": "+91",
            "phone_number": "9999999999",
            "gate_passcode": "4821",
            "photo_path": None,
        }
    )
    svc.repo.link_pass_id = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.get_by_id = AsyncMock(return_value={"id": "pass-1", "status": "cancelled"})
    svc.passes_repo.insert_daily_help = AsyncMock(return_value={"id": "pass-2"})

    result = await svc.reactivate_profile(project_id="project-1", profile_id="profile-1")

    assert result.status == DailyHelpStatus.ACTIVE.value
    svc.passes_repo.insert_daily_help.assert_awaited_once()
    svc.repo.link_pass_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_reactivate_raises_when_not_inactive():
    """Reactivate rejects active or deleted profiles."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={"id": "profile-1", "status": DailyHelpStatus.ACTIVE.value}
    )

    result = await svc.reactivate_profile(project_id="project-1", profile_id="profile-1")
    assert result.status == DailyHelpStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_reactivate_raises_when_deleted():
    """Reactivate rejects deleted profiles (use restore instead)."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={"id": "profile-1", "status": DailyHelpStatus.DELETED.value}
    )

    with pytest.raises(ConflictException):
        await svc.reactivate_profile(project_id="project-1", profile_id="profile-1")


@pytest.mark.asyncio
async def test_list_profiles_includes_household_link_count():
    """Admin list rows expose per-profile household link counts."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.list_profiles = AsyncMock(
        return_value=(
            [
                {
                    "id": "profile-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "display_name": "Lakshmi",
                    "category_id": "cat-1",
                    "category_name": "Maid",
                    "phone_isd_code": "+91",
                    "phone_number": "9876543210",
                    "photo_path": "org/daily-help/photo_lakshmi.jpg",
                    "document_count": 2,
                    "household_link_count": 3,
                    "status": DailyHelpStatus.ACTIVE.value,
                    "gate_passcode": "1234",
                    "open_to_work": True,
                    "created_at": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                }
            ],
            1,
        )
    )

    from apps.user_service.app.schemas.daily_help import DailyHelpListQuery

    items, total = await svc.list_profiles(
        project_id="project-1",
        query=DailyHelpListQuery(page=1, page_size=20),
    )

    assert total == 1
    assert items[0].household_link_count == 3
    assert items[0].photo_path == "org/daily-help/photo_lakshmi.jpg"


@pytest.mark.asyncio
async def test_list_household_links_returns_active_units():
    """Admin can list active household unit links for a profile."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value={"id": "profile-1"})
    svc.repo.list_active_links_for_profile = AsyncMock(
        return_value=[
            {
                "id": "link-1",
                "unit_id": "unit-1",
                "linked_by_contact_id": "owner-1",
                "status": "active",
                "started_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                "unit_code": "A-1203",
                "unit_label": "A-1203",
            }
        ]
    )

    items = await svc.list_household_links(project_id="project-1", profile_id="profile-1")

    assert len(items) == 1
    assert items[0].unit_id == "unit-1"
    assert items[0].unit_label == "A-1203"


@pytest.mark.asyncio
async def test_get_detail_raises_when_missing():
    """Missing profile returns not found."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.get_detail(project_id="project-1", profile_id="missing")


@pytest.mark.asyncio
async def test_list_resident_household_links_returns_linked_profiles():
    """Resident can list daily help profiles linked to their unit by category."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.list_active_links_for_unit = AsyncMock(
        return_value=[
            {
                "id": "link-1",
                "unit_id": "unit-1",
                "linked_by_contact_id": "contact-1",
                "status": "active",
                "started_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                "profile_id": "profile-1",
                "display_name": "Ramesh Kumar",
                "initials": "Mr.",
                "photo_path": "photo.jpg",
                "phone_isd_code": "+91",
                "phone_number": "9876543210",
                "gate_passcode": "1234",
                "open_to_work": True,
                "linked_pass_id": None,
                "category_id": "cat-1",
                "category_name": "Maids",
            }
        ]
    )
    svc.repo.get_rating_summaries_batch = AsyncMock(
        return_value={"profile-1": {"rating_count": 1, "average_stars": 4.0}}
    )
    svc.events_repo = MagicMock()
    svc.events_repo.has_open_check_in = AsyncMock(return_value=False)

    items = await svc.list_resident_household_links(
        contact_id="contact-1",
        unit_id="unit-1",
    )

    assert len(items) == 1
    assert items[0].category_id == "cat-1"
    assert items[0].category_name == "Maids"
    assert items[0].linked_count == 1
    assert items[0].open_to_work_count == 1
    assert len(items[0].linked_profiles) == 1
    assert items[0].linked_profiles[0].link_id == "link-1"
    assert items[0].linked_profiles[0].profile_id == "profile-1"
    assert items[0].linked_profiles[0].display_name == "Ramesh Kumar"
    assert items[0].linked_profiles[0].phone == "+91 9876543210"
    assert items[0].linked_profiles[0].gate_passcode == "1234"
    assert items[0].linked_profiles[0].open_to_work is True
    assert items[0].linked_profiles[0].average_stars == 4.0


@pytest.mark.asyncio
async def test_list_resident_household_links_empty_when_no_links():
    """Return an empty list when the unit has no household-linked profiles."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.list_active_links_for_unit = AsyncMock(return_value=[])

    items = await svc.list_resident_household_links(
        contact_id="contact-1",
        unit_id="unit-1",
    )

    assert items == []


@pytest.mark.asyncio
async def test_list_resident_categories_includes_profile_previews():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.categories_repo = MagicMock()
    svc.categories_repo.list_by_project = AsyncMock(return_value=[{"id": "cat-1", "name": "Maids"}])
    svc.repo = MagicMock()
    svc.repo.list_profiles = AsyncMock(
        return_value=(
            [
                {
                    "id": f"profile-{idx}",
                    "display_name": f"Helper {idx}",
                    "photo_path": f"photo-{idx}.jpg",
                    "initials": "Ms.",
                    "phone_isd_code": "+91",
                    "phone_number": "9655011223",
                    "open_to_work": idx == 0,
                    "household_link_count": idx + 1,
                    "created_at": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                    "linked_pass_id": None,
                }
                for idx in range(6)
            ],
            6,
        )
    )
    svc.events_repo = MagicMock()
    svc.events_repo.has_open_check_in = AsyncMock(return_value=False)
    svc.repo.get_rating_summaries_batch = AsyncMock(
        return_value={
            "profile-0": {"rating_count": 2, "average_stars": 4.5},
        }
    )

    items = await svc.list_resident_categories(
        contact_id="contact-1",
        unit_id="unit-1",
    )

    assert len(items) == 1
    assert items[0].profile_count == 6
    assert len(items[0].preview_profiles) == 4
    assert items[0].preview_profiles[0].display_name == "Helper 0"
    assert items[0].preview_profiles[0].photo_path == "photo-0.jpg"
    assert items[0].preview_profiles[0].phone == "+91 9655011223"
    assert items[0].preview_profiles[0].open_to_work is True
    assert items[0].preview_profiles[0].household_link_count == 1
    assert items[0].preview_profiles[0].average_stars == 4.5
    assert items[0].preview_profiles[1].average_stars is None


@pytest.mark.asyncio
async def test_list_resident_profiles_includes_average_stars():
    """Directory list enriches cards with aggregated rating averages."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.list_profiles = AsyncMock(
        return_value=(
            [
                {
                    "id": "profile-rated",
                    "display_name": "Rated Helper",
                    "category_id": "cat-1",
                    "category_name": "Maid",
                    "phone_isd_code": "+91",
                    "phone_number": "9999999999",
                    "gate_passcode": "1234",
                    "household_link_count": 1,
                    "open_to_work": True,
                    "linked_pass_id": None,
                    "created_at": None,
                },
                {
                    "id": "profile-unrated",
                    "display_name": "Unrated Helper",
                    "category_id": "cat-1",
                    "category_name": "Maid",
                    "phone_isd_code": "+91",
                    "phone_number": "8888888888",
                    "gate_passcode": "5678",
                    "household_link_count": 0,
                    "open_to_work": False,
                    "linked_pass_id": None,
                    "created_at": None,
                },
            ],
            2,
        )
    )
    svc.repo.get_rating_summaries_batch = AsyncMock(
        return_value={"profile-rated": {"rating_count": 1, "average_stars": 4.5}}
    )
    svc.repo.list_links_for_units = AsyncMock(return_value=[])

    items, total = await svc.list_resident_profiles(
        contact_id="contact-1",
        query=ResidentDailyHelpListQuery(unit_id="unit-1", page=1, page_size=20),
    )

    assert total == 2
    assert items[0].average_stars == 4.5
    assert items[1].average_stars is None
    svc.repo.get_rating_summaries_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_resident_open_to_work_updates_profile():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.ACTIVE.value,
            "open_to_work": False,
        }
    )
    svc.repo.update_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "open_to_work": True,
        }
    )
    svc.repo.list_links_for_units = AsyncMock(return_value=[{"id": "link-1"}])
    svc.repo.insert_event = AsyncMock()

    result = await svc.set_resident_open_to_work(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        body=SetDailyHelpOpenToWorkRequest(open_to_work=True),
    )

    assert result.id == "profile-1"
    assert result.open_to_work is True
    svc.repo.update_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_resident_open_to_work_requires_household_link():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={"id": "profile-1", "status": DailyHelpStatus.ACTIVE.value}
    )
    svc.repo.list_links_for_units = AsyncMock(return_value=[])

    with pytest.raises(ValidationException):
        await svc.set_resident_open_to_work(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=SetDailyHelpOpenToWorkRequest(open_to_work=False),
        )


@pytest.mark.asyncio
async def test_remove_household_link_records_optional_reason():
    """Removing a household link stores an optional reason on the audit event."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value={"id": "profile-1"})
    svc.repo.list_active_links_for_profile = AsyncMock(
        return_value=[
            {
                "id": "link-1",
                "unit_id": "unit-1",
                "linked_by_contact_id": "contact-1",
                "status": "active",
                "unit_code": "A-1203",
                "unit_label": "A-1203",
            }
        ]
    )
    svc.repo.remove_link = AsyncMock(
        return_value={
            "id": "link-1",
            "unit_id": "unit-1",
            "status": "removed",
            "removed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            "removal_reason": "No longer needed",
        }
    )
    svc.repo.insert_event = AsyncMock()

    result = await svc.remove_household_link(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        link_id="link-1",
        reason="No longer needed",
    )

    assert result.status == "removed"
    assert result.removal_reason == "No longer needed"
    svc.repo.remove_link.assert_awaited_once_with(
        organization_id="org-1",
        profile_id="profile-1",
        link_id="link-1",
        removal_reason="No longer needed",
    )
    svc.repo.insert_event.assert_awaited_once()
    event_kwargs = svc.repo.insert_event.await_args.kwargs
    assert event_kwargs["payload"]["reason"] == "No longer needed"
    assert event_kwargs["payload"]["link_id"] == "link-1"


@pytest.mark.asyncio
async def test_get_attendance_builds_monthly_calendar():
    """Attendance merges gate check-ins and resident absences per day."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value={"id": "profile-1", "linked_pass_id": "pass-1"})
    svc.repo.list_attendance_absence_dates_for_month = AsyncMock(
        return_value=[__import__("datetime").date(2024, 5, 7)]
    )
    svc.events_repo = MagicMock()
    svc.events_repo.list_check_in_dates_for_month = AsyncMock(
        return_value=[
            __import__("datetime").date(2024, 5, 1),
            __import__("datetime").date(2024, 5, 2),
        ]
    )
    svc.events_repo.list_check_ins_for_month = AsyncMock(
        return_value=[
            {"id": "event-1", "occurred_at": __import__("datetime").datetime(2024, 5, 1, 9, 0)},
        ]
    )
    svc.events_repo.get_last_check_in = AsyncMock(
        return_value={
            "id": "event-1",
            "occurred_at": __import__("datetime").datetime(2024, 5, 2, 9, 0),
            "access_status": "granted",
        }
    )

    result = await svc.get_attendance(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        year=2024,
        month=5,
    )

    assert result["year"] == 2024
    assert result["month"] == 5
    assert result["days_in_month"] == 31
    assert result["present_count"] == 2
    assert result["absent_count"] == 1
    assert result["days"][0] == {"date": "2024-05-01", "status": "present"}
    assert result["days"][6] == {"date": "2024-05-07", "status": "absent"}
    assert result["days"][22] == {"date": "2024-05-23", "status": None}
    assert len(result["events"]) == 1


@pytest.mark.asyncio
async def test_mark_attendance_absence_records_resident_report():
    """Marking absent persists the row and audit event when no gate check-in exists."""
    from datetime import date

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.ACTIVE.value,
            "linked_pass_id": "pass-1",
        }
    )
    svc.repo.list_links_for_units = AsyncMock(return_value=[{"id": "link-1"}])
    svc.repo.upsert_attendance_absence = AsyncMock(
        return_value={"id": "absence-1", "attendance_date": date(2024, 5, 22)}
    )
    svc.repo.insert_event = AsyncMock()
    svc.events_repo = MagicMock()
    svc.events_repo.list_check_in_dates_for_month = AsyncMock(return_value=[])

    result = await svc.mark_attendance_absence(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        attendance_date=date(2024, 5, 22),
    )

    assert result == {"date": "2024-05-22", "status": "absent"}
    svc.repo.upsert_attendance_absence.assert_awaited_once()
    svc.repo.insert_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_attendance_absence_rejects_gate_check_in_day():
    """Cannot mark absent when the helper already checked in at the gate."""
    from datetime import date

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={
            "id": "profile-1",
            "status": DailyHelpStatus.ACTIVE.value,
            "linked_pass_id": "pass-1",
        }
    )
    svc.repo.list_links_for_units = AsyncMock(return_value=[{"id": "link-1"}])
    svc.events_repo = MagicMock()
    svc.events_repo.list_check_in_dates_for_month = AsyncMock(return_value=[date(2024, 5, 22)])

    with pytest.raises(ConflictException):
        await svc.mark_attendance_absence(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            attendance_date=date(2024, 5, 22),
        )


@pytest.mark.asyncio
async def test_get_resident_rating_returns_existing_rating():
    from datetime import datetime, timezone

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value={"id": "profile-1"})
    svc.repo.get_rating_by_rater = AsyncMock(
        return_value={
            "id": "rating-1",
            "stars": 4.5,
            "comment": "Great work",
            "traits": ["very_punctual"],
            "created_at": datetime(2024, 5, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2024, 5, 2, tzinfo=timezone.utc),
        }
    )

    result = await svc.get_resident_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
    )

    assert result is not None
    assert result.id == "rating-1"
    assert result.stars == 4.5
    assert result.traits == ["very_punctual"]


@pytest.mark.asyncio
async def test_update_rating_requires_existing_rating():
    from decimal import Decimal

    from apps.user_service.app.schemas.daily_help import UpdateDailyHelpRatingRequest

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value={"id": "profile-1", "status": DailyHelpStatus.ACTIVE.value}
    )
    svc.repo.update_rating = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.update_rating(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=UpdateDailyHelpRatingRequest(stars=Decimal("5.0")),
        )


def _detail_row(**overrides: object) -> dict[str, object]:
    row = {
        "id": "profile-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "initials": "Mrs.",
        "first_name": "Lakshmi",
        "middle_name": None,
        "last_name": "Devi",
        "display_name": "Mrs. Lakshmi Devi",
        "phone_isd_code": "+91",
        "phone_number": "9655011223",
        "alternate_phone_isd_code": None,
        "alternate_phone_number": None,
        "category_id": "cat-1",
        "category_name": "Maid",
        "gender": None,
        "date_of_birth": None,
        "photo_path": None,
        "gate_passcode": None,
        "status": DailyHelpStatus.PENDING_APPROVAL.value,
        "open_to_work": True,
        "linked_pass_id": None,
        "created_by_user_id": "staff-1",
        "submitted_by_user_id": "staff-1",
        "reviewed_by_user_id": None,
        "reviewed_at": None,
        "rejection_reason": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "deleted_at": None,
    }
    row.update(overrides)
    return row


def _create_body() -> CreateDailyHelpRequest:
    return CreateDailyHelpRequest(
        initials="Mrs.",
        first_name="Lakshmi",
        last_name="Devi",
        phone_isd_code="+91",
        phone_number="9655011223",
        category_id="cat-1",
    )


def _stub_category_lookup(svc: DailyHelpService) -> None:
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.categories_repo = MagicMock()
    svc.categories_repo.get_by_id = AsyncMock(
        return_value={"id": "cat-1", "name": "Maid", "status": "active"}
    )


@pytest.mark.asyncio
async def test_submit_profile_notifies_reviewers():
    """Security submit notifies org admins for review."""
    push = _FakePushDispatcher()
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=push,
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.insert_profile = AsyncMock(
        return_value={
            "id": "profile-pending",
            "created_at": datetime.now(timezone.utc),
        }
    )
    svc.repo.insert_document = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.passes_repo = MagicMock()
    svc._resolve_created_by_name = AsyncMock(return_value="Guard User")

    await svc.submit_profile(project_id="project-1", body=_create_body())

    assert len(push.org_calls) == 1
    call = push.org_calls[0]
    assert call["message_key"] == "notifications.push.daily_help.submitted"
    assert call["params"] == {"helper_name": "Mrs. Lakshmi Devi"}
    assert call["data"]["profile_id"] == "profile-pending"


@pytest.mark.asyncio
async def test_submit_profile_does_not_issue_pass():
    """Security submit creates pending profile without pass or passcode."""
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=_FakePushDispatcher(),
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.insert_profile = AsyncMock(
        return_value={
            "id": "profile-pending",
            "created_at": datetime.now(timezone.utc),
        }
    )
    svc.repo.insert_document = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.insert_daily_help = AsyncMock()
    svc._resolve_created_by_name = AsyncMock(return_value="Guard User")

    result = await svc.submit_profile(project_id="project-1", body=_create_body())

    assert result.status == DailyHelpStatus.PENDING_APPROVAL.value
    assert result.gate_passcode is None
    assert result.linked_pass_id is None
    svc.passes_repo.insert_daily_help.assert_not_awaited()
    insert_kwargs = svc.repo.insert_profile.await_args.kwargs
    assert insert_kwargs["status"] == DailyHelpStatus.PENDING_APPROVAL.value
    assert insert_kwargs["gate_passcode"] is None


@pytest.mark.asyncio
async def test_approve_profile_notifies_submitter():
    """Admin approve notifies the security submitter."""
    push = _FakePushDispatcher()
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=push,
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        side_effect=[
            _detail_row(id="profile-pending", status=DailyHelpStatus.PENDING_APPROVAL.value),
            _detail_row(
                id="profile-pending",
                status=DailyHelpStatus.ACTIVE.value,
                gate_passcode="4821",
                linked_pass_id="pass-1",
                submitted_by_user_id="staff-1",
            ),
        ]
    )
    svc.repo.generate_unique_passcode = AsyncMock(return_value="4821")
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-pending",
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="4821",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.link_pass_id = AsyncMock()
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc.passes_repo = MagicMock()
    svc.passes_repo.insert_daily_help = AsyncMock(return_value={"id": "pass-1"})
    svc._resolve_created_by_name = AsyncMock(return_value="Admin User")

    await svc.approve_profile(project_id="project-1", profile_id="profile-pending")

    assert len(push.user_calls) == 1
    call = push.user_calls[0]
    assert call["recipient_user_id"] == "staff-1"
    assert call["message_key"] == "notifications.push.daily_help.approved"
    assert call["params"] == {"helper_name": "Mrs. Lakshmi Devi"}


@pytest.mark.asyncio
async def test_approve_profile_issues_pass_for_pending_submission():
    """Admin approve activates pending profile and issues gate pass."""
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=_FakePushDispatcher(),
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        side_effect=[
            _detail_row(id="profile-pending", status=DailyHelpStatus.PENDING_APPROVAL.value),
            _detail_row(
                id="profile-pending",
                status=DailyHelpStatus.ACTIVE.value,
                gate_passcode="4821",
                linked_pass_id="pass-1",
            ),
        ]
    )
    svc.repo.generate_unique_passcode = AsyncMock(return_value="4821")
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-pending",
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="4821",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.link_pass_id = AsyncMock()
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc.passes_repo = MagicMock()
    svc.passes_repo.insert_daily_help = AsyncMock(return_value={"id": "pass-1"})
    svc._resolve_created_by_name = AsyncMock(return_value="Admin User")

    result = await svc.approve_profile(project_id="project-1", profile_id="profile-pending")

    assert result.status == DailyHelpStatus.ACTIVE.value
    svc.passes_repo.insert_daily_help.assert_awaited_once()
    svc.repo.link_pass_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_profile_notifies_submitter():
    """Admin reject notifies the security submitter."""
    from apps.user_service.app.schemas.daily_help import RejectDailyHelpRequest

    push = _FakePushDispatcher()
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=push,
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-pending",
            status=DailyHelpStatus.PENDING_APPROVAL.value,
            submitted_by_user_id="staff-1",
        )
    )
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-pending",
            status=DailyHelpStatus.REJECTED.value,
            rejection_reason="Incomplete documents",
            submitted_by_user_id="staff-1",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc._resolve_created_by_name = AsyncMock(return_value=None)

    await svc.reject_profile(
        project_id="project-1",
        profile_id="profile-pending",
        body=RejectDailyHelpRequest(rejection_reason="Incomplete documents"),
    )

    assert len(push.user_calls) == 1
    call = push.user_calls[0]
    assert call["recipient_user_id"] == "staff-1"
    assert call["message_key"] == "notifications.push.daily_help.rejected"


@pytest.mark.asyncio
async def test_reject_profile_sets_rejected_status():
    """Admin reject marks pending profile as rejected."""
    from apps.user_service.app.schemas.daily_help import RejectDailyHelpRequest

    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=_FakePushDispatcher(),
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-pending",
            status=DailyHelpStatus.PENDING_APPROVAL.value,
        )
    )
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-pending",
            status=DailyHelpStatus.REJECTED.value,
            rejection_reason="Incomplete documents",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc._resolve_created_by_name = AsyncMock(return_value=None)

    result = await svc.reject_profile(
        project_id="project-1",
        profile_id="profile-pending",
        body=RejectDailyHelpRequest(rejection_reason="Incomplete documents"),
    )

    assert result.status == DailyHelpStatus.REJECTED.value
    assert result.rejection_reason == "Incomplete documents"


@pytest.mark.asyncio
async def test_resubmit_profile_notifies_reviewers():
    """Security resubmit notifies org admins for review."""
    push = _FakePushDispatcher()
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=push,
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-rejected",
            status=DailyHelpStatus.REJECTED.value,
        )
    )
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-rejected",
            status=DailyHelpStatus.PENDING_APPROVAL.value,
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc._resolve_created_by_name = AsyncMock(return_value="Guard User")

    await svc.resubmit_profile(
        project_id="project-1",
        profile_id="profile-rejected",
        body=_create_body(),
    )

    assert len(push.org_calls) == 1
    call = push.org_calls[0]
    assert call["message_key"] == "notifications.push.daily_help.resubmitted"


@pytest.mark.asyncio
async def test_resubmit_profile_moves_rejected_back_to_pending():
    """Security resubmit clears rejection and returns profile to pending review."""
    svc = DailyHelpService(
        db_connection=MagicMock(),
        user_context=_user_context(),
        push_dispatcher=_FakePushDispatcher(),
    )
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-rejected",
            status=DailyHelpStatus.REJECTED.value,
        )
    )
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            id="profile-rejected",
            status=DailyHelpStatus.PENDING_APPROVAL.value,
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc._resolve_created_by_name = AsyncMock(return_value="Guard User")

    result = await svc.resubmit_profile(
        project_id="project-1",
        profile_id="profile-rejected",
        body=_create_body(),
    )

    assert result.status == DailyHelpStatus.PENDING_APPROVAL.value
    update_fields = svc.repo.update_profile.await_args.kwargs["fields"]
    assert update_fields["status"] == DailyHelpStatus.PENDING_APPROVAL.value
    assert update_fields["rejection_reason"] is None


@pytest.mark.asyncio
async def test_list_my_submissions_filters_by_submitted_by_user():
    """Security list scopes rows to the current submitter."""
    from apps.user_service.app.schemas.daily_help import DailyHelpSubmissionListQuery

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.list_profiles = AsyncMock(
        return_value=(
            [
                {
                    "id": "profile-pending",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "display_name": "Mrs. Lakshmi Devi",
                    "gender": None,
                    "category_id": "cat-1",
                    "category_name": "Maid",
                    "phone_isd_code": "+91",
                    "phone_number": "9655011223",
                    "document_count": 1,
                    "household_link_count": 0,
                    "status": DailyHelpStatus.PENDING_APPROVAL.value,
                    "gate_passcode": None,
                    "open_to_work": True,
                    "created_at": datetime.now(timezone.utc),
                    "rejection_reason": None,
                    "reviewed_at": None,
                }
            ],
            1,
        )
    )

    items, total = await svc.list_my_submissions(
        project_id="project-1",
        query=DailyHelpSubmissionListQuery(status=DailyHelpStatus.PENDING_APPROVAL),
    )

    assert total == 1
    assert items[0].status == DailyHelpStatus.PENDING_APPROVAL.value
    assert items[0].rejection_reason is None
    svc.repo.list_profiles.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        status=DailyHelpStatus.PENDING_APPROVAL.value,
        search=None,
        submitted_by_user_id="staff-1",
        limit=20,
        offset=0,
    )


@pytest.mark.asyncio
async def test_get_my_submission_returns_owned_profile():
    """Security detail returns submission owned by the caller."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value=_detail_row(id="profile-pending"))
    svc.repo.list_documents = AsyncMock(return_value=[])
    svc.repo.list_events = AsyncMock(return_value=[])
    svc.repo.list_active_links_for_profile = AsyncMock(return_value=[])
    svc.repo.list_slots = AsyncMock(return_value=[])
    svc.repo.get_rating_summary = AsyncMock(return_value={"rating_count": 0, "average_stars": 0})
    svc._resolve_created_by_name = AsyncMock(return_value="Guard User")

    result = await svc.get_my_submission(project_id="project-1", profile_id="profile-pending")

    assert result.id == "profile-pending"
    assert result.submitted_by_user_id == "staff-1"


@pytest.mark.asyncio
async def test_get_my_submission_not_found_for_other_submitter():
    """Security cannot read another guard's submission."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(id="profile-pending", submitted_by_user_id="other-guard")
    )

    with pytest.raises(NotFoundException):
        await svc.get_my_submission(project_id="project-1", profile_id="profile-pending")


@pytest.mark.asyncio
async def test_export_csv_success_and_invalid_format():
    from apps.user_service.app.schemas.daily_help import DailyHelpExportQuery

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = MagicMock()
    svc.repo.list_profiles = AsyncMock(
        return_value=(
            [
                {
                    "id": "profile-1",
                    "display_name": "Mrs. Lakshmi Devi",
                    "category_name": "Maid",
                    "phone_isd_code": "+91",
                    "phone_number": "9655011223",
                    "gender": None,
                    "status": DailyHelpStatus.ACTIVE.value,
                    "gate_passcode": "4821",
                    "document_count": 1,
                    "household_link_count": 2,
                    "created_at": datetime.now(timezone.utc),
                }
            ],
            1,
        )
    )

    csv_text = await svc.export_csv(
        project_id="project-1",
        query=DailyHelpExportQuery(format="csv"),
    )
    assert "Mrs. Lakshmi Devi" in csv_text

    with pytest.raises(ValidationException):
        await svc.export_csv(
            project_id="project-1",
            query=DailyHelpExportQuery(format="xlsx"),
        )


@pytest.mark.asyncio
async def test_deactivate_reactivate_delete_restore_profile():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        side_effect=[
            _detail_row(status=DailyHelpStatus.ACTIVE.value, linked_pass_id="pass-1"),
            _detail_row(status=DailyHelpStatus.INACTIVE.value, linked_pass_id="pass-1"),
            _detail_row(status=DailyHelpStatus.ACTIVE.value, linked_pass_id="pass-1"),
            _detail_row(status=DailyHelpStatus.DELETED.value),
        ]
    )
    svc.repo.update_profile = AsyncMock(
        side_effect=[
            _detail_row(status=DailyHelpStatus.INACTIVE.value),
            _detail_row(status=DailyHelpStatus.ACTIVE.value, linked_pass_id="pass-2"),
            _detail_row(status=DailyHelpStatus.DELETED.value),
            _detail_row(status=DailyHelpStatus.ACTIVE.value),
        ]
    )
    svc.repo.insert_event = AsyncMock()
    svc._cancel_linked_pass = AsyncMock()
    svc._reissue_pass_if_needed = AsyncMock()

    deactivated = await svc.deactivate_profile(project_id="project-1", profile_id="profile-1")
    assert deactivated.status == DailyHelpStatus.INACTIVE.value

    reactivated = await svc.reactivate_profile(project_id="project-1", profile_id="profile-1")
    assert reactivated.status == DailyHelpStatus.ACTIVE.value

    deleted = await svc.delete_profile(project_id="project-1", profile_id="profile-1")
    assert deleted.status == DailyHelpStatus.DELETED.value

    svc.repo.get_profile = AsyncMock(return_value=_detail_row(status=DailyHelpStatus.DELETED.value))
    restored = await svc.restore_profile(project_id="project-1", profile_id="profile-1")
    assert restored.status == DailyHelpStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_create_and_update_category():
    from apps.user_service.app.schemas.daily_help import (
        CreateDailyHelpCategoryRequest,
        UpdateDailyHelpCategoryRequest,
    )

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.categories_repo = MagicMock()
    category_row = {
        "id": "cat-2",
        "organization_id": "org-1",
        "project_id": "project-1",
        "name": "Cook",
        "sort_order": 2,
        "status": "active",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    svc.categories_repo.insert = AsyncMock(return_value=category_row)
    svc.categories_repo.get_by_id = AsyncMock(return_value=category_row)
    svc.categories_repo.update = AsyncMock(return_value={**category_row, "name": "Chef"})

    created = await svc.create_category(
        project_id="project-1",
        body=CreateDailyHelpCategoryRequest(name="Cook", sort_order=2),
    )
    assert created.name == "Cook"

    updated = await svc.update_category(
        project_id="project-1",
        category_id="cat-2",
        body=UpdateDailyHelpCategoryRequest(name="Chef"),
    )
    assert updated.name == "Chef"


@pytest.mark.asyncio
async def test_list_and_search_resident_profiles():
    from apps.user_service.app.schemas.daily_help import (
        ResidentDailyHelpListQuery,
        ResidentDailyHelpSearchQuery,
    )

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._ensure_resident_unit = AsyncMock(return_value="project-1")
    svc.repo = MagicMock()
    profile_row = {
        "id": "profile-1",
        "display_name": "Mrs. Lakshmi Devi",
        "category_id": "cat-1",
        "category_name": "Maid",
        "phone_isd_code": "+91",
        "phone_number": "9655011223",
        "photo_path": None,
        "gate_passcode": "4821",
        "household_link_count": 0,
        "open_to_work": True,
        "linked_pass_id": None,
        "created_at": datetime.now(timezone.utc),
    }
    svc.repo.list_profiles = AsyncMock(return_value=([profile_row], 1))
    svc.repo.get_rating_summaries_batch = AsyncMock(
        return_value={"profile-1": {"average_stars": 4.5}}
    )
    svc._viewer_has_household_link = AsyncMock(return_value=False)
    svc._profile_is_inside = AsyncMock(return_value=False)

    items, total = await svc.list_resident_profiles(
        contact_id="contact-1",
        query=ResidentDailyHelpListQuery(unit_id="unit-1"),
    )
    assert total == 1

    search_items, search_total = await svc.search_resident_profiles(
        contact_id="contact-1",
        query=ResidentDailyHelpSearchQuery(unit_id="unit-1", q="Lakshmi"),
    )
    assert search_total == 1
    assert search_items[0].display_name == "Mrs. Lakshmi Devi"


@pytest.mark.asyncio
async def test_add_admin_household_link():
    """Admin can link an active profile to a project unit."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value)
    )
    svc._ensure_project_unit = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.has_active_link = AsyncMock(return_value=False)
    svc.repo.insert_link = AsyncMock(
        return_value={
            "id": "link-1",
            "unit_id": "unit-1",
            "linked_by_contact_id": None,
            "status": "active",
            "started_at": datetime.now(timezone.utc),
        }
    )
    svc.repo.list_active_links_for_profile = AsyncMock(
        return_value=[
            {
                "id": "link-1",
                "unit_id": "unit-1",
                "linked_by_contact_id": None,
                "status": "active",
                "started_at": datetime.now(timezone.utc),
                "unit_code": "A-101",
                "unit_label": "Flat 101",
            }
        ]
    )
    svc.repo.insert_event = AsyncMock()

    link = await svc.add_admin_household_link(
        project_id="project-1",
        profile_id="profile-1",
        unit_id="unit-1",
    )

    assert link.id == "link-1"
    assert link.unit_code == "A-101"
    svc.repo.insert_link.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        profile_id="profile-1",
        unit_id="unit-1",
        linked_by_contact_id=None,
    )


@pytest.mark.asyncio
async def test_remove_admin_household_link():
    """Admin can unlink a profile from a unit."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._get_profile_or_raise = AsyncMock(return_value=_detail_row())
    svc.repo = MagicMock()
    svc.repo.list_active_links_for_profile = AsyncMock(
        return_value=[
            {
                "id": "link-1",
                "unit_id": "unit-1",
                "linked_by_contact_id": None,
                "status": "active",
                "started_at": datetime.now(timezone.utc),
                "unit_code": "A-101",
                "unit_label": None,
            }
        ]
    )
    svc.repo.remove_link = AsyncMock(
        return_value={
            "id": "link-1",
            "unit_id": "unit-1",
            "status": "removed",
            "removed_at": datetime.now(timezone.utc),
            "removal_reason": "Admin update",
        }
    )
    svc.repo.insert_event = AsyncMock()

    link = await svc.remove_admin_household_link(
        project_id="project-1",
        profile_id="profile-1",
        link_id="link-1",
        reason="Admin update",
    )

    assert link.status == "removed"
    assert link.removal_reason == "Admin update"


@pytest.mark.asyncio
async def test_regenerate_gate_passcode_updates_profile_and_pass():
    """Admin regenerate issues a new code and syncs the active linked pass."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="1234",
            linked_pass_id="pass-1",
        )
    )
    svc.repo = MagicMock()
    svc.repo.generate_unique_passcode = AsyncMock(return_value="5798")
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="5798",
            linked_pass_id="pass-1",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.get_by_id = AsyncMock(return_value={"id": "pass-1", "status": "active"})
    svc.passes_repo.update_active_pass_code = AsyncMock(
        return_value={"id": "pass-1", "code": "5798"}
    )
    svc.members_repo = MagicMock()
    svc.members_repo.get_user_profile_by_id = AsyncMock(
        return_value={"first_name": "Admin", "last_name": "User"}
    )

    result = await svc.regenerate_gate_passcode(
        project_id="project-1",
        profile_id="profile-1",
    )

    assert result.gate_passcode == "5798"
    assert result.linked_pass_id == "pass-1"
    assert result.updated_by_name == "Admin User"
    svc.passes_repo.update_active_pass_code.assert_awaited_once_with(
        organization_id="org-1",
        pass_id="pass-1",
        code="5798",
    )


@pytest.mark.asyncio
async def test_regenerate_gate_passcode_reissues_missing_pass():
    """Regenerate re-issues the linked pass when it is missing or inactive."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="1234",
            linked_pass_id="pass-1",
        )
    )
    svc.repo = MagicMock()
    svc.repo.generate_unique_passcode = AsyncMock(return_value="5798")
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="5798",
            linked_pass_id="pass-1",
        )
    )
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="5798",
            linked_pass_id="pass-2",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.get_by_id = AsyncMock(return_value={"id": "pass-1", "status": "cancelled"})
    svc._reissue_pass_if_needed = AsyncMock()
    svc.members_repo = MagicMock()
    svc.members_repo.get_user_profile_by_id = AsyncMock(return_value=None)

    result = await svc.regenerate_gate_passcode(
        project_id="project-1",
        profile_id="profile-1",
    )

    assert result.gate_passcode == "5798"
    assert result.linked_pass_id == "pass-2"
    svc._reissue_pass_if_needed.assert_awaited_once()


@pytest.mark.asyncio
async def test_regenerate_gate_passcode_requires_active_profile():
    """Inactive profiles cannot regenerate a gate passcode."""
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.INACTIVE.value,
            gate_passcode="1234",
        )
    )

    with pytest.raises(ValidationException):
        await svc.regenerate_gate_passcode(
            project_id="project-1",
            profile_id="profile-1",
        )


@pytest.mark.asyncio
async def test_add_household_link_and_create_rating():
    from decimal import Decimal

    from apps.user_service.app.schemas.daily_help import CreateDailyHelpRatingRequest

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._ensure_resident_unit = AsyncMock(return_value="project-1")
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value)
    )
    svc.repo = MagicMock()
    svc.repo.has_active_link = AsyncMock(return_value=False)
    svc.repo.insert_link = AsyncMock(
        return_value={
            "id": "link-1",
            "profile_id": "profile-1",
            "unit_id": "unit-1",
            "status": "active",
            "started_at": datetime.now(timezone.utc),
        }
    )
    svc.repo.insert_event = AsyncMock()
    svc.repo.insert_rating = AsyncMock()
    svc.repo.get_rating_summary = AsyncMock(
        return_value={"rating_count": 1, "average_stars": 5.0, "trait_counts": {}}
    )

    link = await svc.add_household_link(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
    )
    assert link.id == "link-1"

    svc.repo.has_active_link = AsyncMock(return_value=True)
    with pytest.raises(ConflictException):
        await svc.add_household_link(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
        )

    rating = await svc.create_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        body=CreateDailyHelpRatingRequest(stars=Decimal("5.0")),
    )
    assert rating.rating_count == 1


@pytest.mark.asyncio
async def test_mark_attendance_absence_success_and_failures():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._ensure_resident_unit = AsyncMock(return_value="project-1")
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value)
    )
    svc._viewer_has_household_link = AsyncMock(return_value=True)
    svc.repo = MagicMock()
    svc.repo.upsert_attendance_absence = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.events_repo = MagicMock()
    svc.events_repo.list_check_in_dates_for_month = AsyncMock(return_value=[])

    result = await svc.mark_attendance_absence(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        attendance_date=date.today(),
    )
    assert result["status"] == "absent"

    svc._viewer_has_household_link = AsyncMock(return_value=False)
    with pytest.raises(ValidationException):
        await svc.mark_attendance_absence(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            attendance_date=date.today(),
        )


@pytest.mark.asyncio
async def test_set_resident_open_to_work():
    from apps.user_service.app.schemas.daily_help import SetDailyHelpOpenToWorkRequest

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._ensure_resident_unit = AsyncMock(return_value="project-1")
    svc._get_profile_or_raise = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value)
    )
    svc._viewer_has_household_link = AsyncMock(return_value=True)
    svc.repo = MagicMock()
    svc.repo.update_profile = AsyncMock(return_value=_detail_row(open_to_work=False))
    svc.repo.insert_event = AsyncMock()

    toggled = await svc.set_resident_open_to_work(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        body=SetDailyHelpOpenToWorkRequest(open_to_work=False),
    )
    assert toggled.open_to_work is False


@pytest.mark.asyncio
async def test_seed_default_categories_when_empty():
    from apps.user_service.app.services.daily_help_service import (
        DEFAULT_DAILY_HELP_CATEGORY_NAMES,
    )

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.categories_repo = MagicMock()
    svc.categories_repo.list_by_project = AsyncMock(return_value=[])
    svc.categories_repo.insert = AsyncMock(
        side_effect=[
            {
                "id": f"cat-{idx}",
                "organization_id": "org-1",
                "project_id": "project-1",
                "name": name,
                "sort_order": idx,
                "status": "active",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            for idx, name in enumerate(DEFAULT_DAILY_HELP_CATEGORY_NAMES)
        ]
    )

    categories = await svc.seed_default_categories(project_id="project-1")
    assert len(categories) == len(DEFAULT_DAILY_HELP_CATEGORY_NAMES)


def test_format_phone_and_date_helpers():
    """Phone and date formatting helpers handle edge cases."""
    assert DailyHelpService._format_phone(isd_code="+91", phone_number=None) is None
    assert DailyHelpService._format_phone(isd_code="+91", phone_number="99999") == "+91 99999"
    assert DailyHelpService._format_phone(isd_code=None, phone_number="99999") == "99999"
    assert DailyHelpService._mask_phone_number("1234") == "XXXX"
    assert DailyHelpService._format_date(None) is None
    assert DailyHelpService._format_date(date(2026, 8, 1)) == "2026-08-01"
    assert DailyHelpService._format_date("2026-08-01") == "2026-08-01"


@pytest.mark.asyncio
async def test_resolve_created_by_name():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.members_repo = MagicMock()
    svc.members_repo.get_user_profile_by_id = AsyncMock(
        return_value={"first_name": "Admin", "last_name": "User"}
    )

    assert await svc._resolve_created_by_name(None) is None
    assert await svc._resolve_created_by_name("staff-1") == "Admin User"

    svc.members_repo.get_user_profile_by_id = AsyncMock(return_value=None)
    assert await svc._resolve_created_by_name("staff-1") is None


@pytest.mark.asyncio
async def test_ensure_resident_unit_validates_access():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.contact_units_repo = MagicMock()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await svc._ensure_resident_unit(contact_id="contact-1", unit_id="unit-1")

    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value=None)

    with pytest.raises(ValidationException):
        await svc._ensure_resident_unit(contact_id="contact-1", unit_id="unit-1")

    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "project-1"})
    project_id = await svc._ensure_resident_unit(contact_id="contact-1", unit_id="unit-1")
    assert project_id == "project-1"


@pytest.mark.asyncio
async def test_update_profile_syncs_pass_on_identity_change():
    from apps.user_service.app.schemas.daily_help import UpdateDailyHelpRequest

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value, linked_pass_id="pass-1")
    )
    svc.repo.update_profile = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            first_name="Updated",
            linked_pass_id="pass-1",
            gate_passcode="1234",
        )
    )
    svc.repo.insert_event = AsyncMock()
    svc._sync_pass_guest_snapshot = AsyncMock()
    svc._serialize_detail = AsyncMock(return_value=MagicMock(id="profile-1"))

    await svc.update_profile(
        project_id="project-1",
        profile_id="profile-1",
        body=UpdateDailyHelpRequest(first_name="Updated"),
    )

    svc._sync_pass_guest_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_deactivate_profile_idempotent_when_already_inactive():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.INACTIVE.value)
    )

    result = await svc.deactivate_profile(project_id="project-1", profile_id="profile-1")

    assert result.status == DailyHelpStatus.INACTIVE.value
    svc.repo.update_profile.assert_not_called()


@pytest.mark.asyncio
async def test_add_and_delete_document():
    from apps.user_service.app.schemas.daily_help import AddDailyHelpDocumentRequest
    from apps.user_service.app.schemas.enums import DailyHelpDocumentType

    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    _stub_category_lookup(svc)
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value))
    svc.repo.insert_document = AsyncMock(
        return_value={
            "id": "doc-1",
            "document_type": DailyHelpDocumentType.ID_PROOF.value,
            "label": "Aadhaar",
            "file_path": "/files/a.pdf",
            "file_name": "a.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 100,
            "sort_order": 0,
        }
    )
    svc.repo.delete_document = AsyncMock(return_value={"id": "doc-1", "document_type": "id_proof"})
    svc.repo.insert_event = AsyncMock()

    added = await svc.add_document(
        project_id="project-1",
        profile_id="profile-1",
        body=AddDailyHelpDocumentRequest(
            document_type=DailyHelpDocumentType.ID_PROOF,
            label="Aadhaar",
            file_path="/files/a.pdf",
            file_name="a.pdf",
            mime_type="application/pdf",
            file_size_bytes=100,
        ),
    )
    assert added.id == "doc-1"

    deleted = await svc.delete_document(
        project_id="project-1",
        profile_id="profile-1",
        document_id="doc-1",
    )
    assert deleted.id == "doc-1"


@pytest.mark.asyncio
async def test_get_verify_profile_summary_filters_ineligible():
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = MagicMock()
    svc.repo.get_profile = AsyncMock(return_value=None)
    assert (
        await svc.get_verify_profile_summary(project_id="project-1", profile_id="profile-1") == {}
    )

    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.INACTIVE.value, gate_passcode="1234")
    )
    assert (
        await svc.get_verify_profile_summary(project_id="project-1", profile_id="profile-1") == {}
    )

    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(status=DailyHelpStatus.ACTIVE.value, gate_passcode=None)
    )
    assert (
        await svc.get_verify_profile_summary(project_id="project-1", profile_id="profile-1") == {}
    )

    svc.repo.get_profile = AsyncMock(
        return_value=_detail_row(
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode="4821",
            category_name="Maid",
        )
    )
    summary = await svc.get_verify_profile_summary(project_id="project-1", profile_id="profile-1")
    assert summary["gate_passcode"] == "4821"
    assert summary["display_name"] == "Mrs. Lakshmi Devi"
