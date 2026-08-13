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
