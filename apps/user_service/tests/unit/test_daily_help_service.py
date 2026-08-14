"""Unit tests for DailyHelpService helpers and orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.daily_help import (
    CreateDailyHelpRequest,
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
