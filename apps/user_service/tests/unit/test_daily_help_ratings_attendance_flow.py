"""Success and failure path tests for Daily Help ratings, reviews, and attendance."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.daily_help import (
    CreateDailyHelpRatingRequest,
    UpdateDailyHelpRatingRequest,
)
from apps.user_service.app.schemas.enums import DailyHelpRatingTrait, DailyHelpStatus
from apps.user_service.app.services.daily_help_service import DailyHelpService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)


def _user_context() -> UserContext:
    return UserContext(
        user_id="user-1",
        email="resident@example.com",
        organization_id="org-1",
    )


def _active_profile(**overrides: object) -> dict[str, object]:
    row = {
        "id": "profile-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "status": DailyHelpStatus.ACTIVE.value,
        "linked_pass_id": "pass-1",
        "display_name": "Mrs. Lakshmi Devi",
    }
    row.update(overrides)
    return row


def _rating_service(*, linked: bool = True) -> DailyHelpService:
    svc = DailyHelpService(db_connection=MagicMock(), user_context=_user_context())
    svc._ensure_resident_unit = AsyncMock(return_value="project-1")
    svc._get_profile_or_raise = AsyncMock(return_value=_active_profile())
    svc._viewer_has_household_link = AsyncMock(return_value=linked)
    svc.repo = MagicMock()
    svc.events_repo = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# Ratings — success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rating_success_returns_enriched_summary():
    svc = _rating_service(linked=True)
    svc.repo.insert_rating = AsyncMock()
    svc.repo.get_rating_summary = AsyncMock(
        return_value={
            "rating_count": 1,
            "review_count": 1,
            "average_stars": 4.5,
            "star_distribution": {"5": 1},
            "category_averages": {"punctuality": 4.5},
            "trait_counts": {"very_punctual": 1},
        }
    )

    summary = await svc.create_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        body=CreateDailyHelpRatingRequest(
            stars=Decimal("4.5"),
            comment="Very punctual",
            traits=[DailyHelpRatingTrait.VERY_PUNCTUAL],
        ),
    )

    assert summary.rating_count == 1
    assert summary.review_count == 1
    assert summary.average_stars == 4.5
    assert summary.star_distribution["5"] == 1
    assert summary.category_averages["punctuality"] == 4.5
    svc.repo.insert_rating.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_rating_success():
    svc = _rating_service(linked=True)
    svc.repo.update_rating = AsyncMock(
        return_value={
            "id": "rating-1",
            "stars": 5.0,
            "comment": "Excellent",
            "traits": ["exceptional_service"],
            "created_at": datetime(2024, 5, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2024, 5, 2, tzinfo=timezone.utc),
        }
    )

    rating = await svc.update_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        body=UpdateDailyHelpRatingRequest(
            stars=Decimal("5.0"),
            comment="Excellent",
            traits=[DailyHelpRatingTrait.EXCEPTIONAL_SERVICE],
        ),
    )

    assert rating.stars == 5.0
    assert rating.traits == ["exceptional_service"]


@pytest.mark.asyncio
async def test_get_resident_rating_success_and_not_submitted():
    svc = _rating_service(linked=True)
    svc.repo.get_rating_by_rater = AsyncMock(
        side_effect=[
            {
                "id": "rating-1",
                "stars": 4.0,
                "comment": "Good",
                "traits": [],
                "created_at": datetime(2024, 5, 1, tzinfo=timezone.utc),
                "updated_at": datetime(2024, 5, 1, tzinfo=timezone.utc),
            },
            None,
        ]
    )

    found = await svc.get_resident_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
    )
    assert found is not None
    assert found.stars == 4.0

    missing = await svc.get_resident_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
    )
    assert missing is None


@pytest.mark.asyncio
async def test_get_rating_summary_success():
    svc = _rating_service(linked=True)
    svc.repo.get_rating_summary = AsyncMock(
        return_value={
            "rating_count": 128,
            "review_count": 18,
            "average_stars": 4.4,
            "star_distribution": {"5": 78, "4": 31},
            "category_averages": {"behavior": 4.6},
            "trait_counts": {"great_attitude": 40},
        }
    )

    summary = await svc.get_rating_summary(
        project_id="project-1",
        profile_id="profile-1",
    )

    assert summary.rating_count == 128
    assert summary.review_count == 18
    assert summary.category_averages["behavior"] == 4.6


@pytest.mark.asyncio
async def test_list_profile_reviews_success_admin_and_resident():
    svc = _rating_service(linked=True)
    svc.repo.count_ratings_for_profile = AsyncMock(return_value=1)
    svc.repo.list_ratings_for_profile_paginated = AsyncMock(
        return_value=[
            {
                "id": "rating-1",
                "unit_id": "unit-1",
                "rated_by_contact_id": "contact-1",
                "stars": 3.0,
                "comment": "Average",
                "traits": ["quite_regular"],
                "unit_code": "A-2201",
                "unit_label": "A-2201",
                "rated_by_name": "Rohit Malhotra",
                "created_at": datetime(2023, 7, 11, tzinfo=timezone.utc),
                "updated_at": datetime(2023, 7, 11, tzinfo=timezone.utc),
            }
        ]
    )

    resident_items, resident_total = await svc.list_profile_reviews(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        stars=3,
        sort="highest_rated",
        page=1,
        page_size=10,
    )
    assert resident_total == 1
    assert resident_items[0].unit_code == "A-2201"

    admin_items, admin_total = await svc.list_profile_reviews(
        project_id="project-1",
        profile_id="profile-1",
        sort="oldest_first",
        page=1,
        page_size=10,
    )
    assert admin_total == 1
    assert admin_items[0].rated_by_name == "Rohit Malhotra"


# ---------------------------------------------------------------------------
# Ratings — failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rating_fails_without_household_link():
    svc = _rating_service(linked=False)

    with pytest.raises(ValidationException) as exc:
        await svc.create_rating(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=CreateDailyHelpRatingRequest(stars=Decimal("4.0")),
        )

    assert exc.value.message_key == "daily_help.errors.rating_household_link_required"


@pytest.mark.asyncio
async def test_create_rating_fails_when_profile_not_active():
    svc = _rating_service(linked=True)
    svc._get_profile_or_raise = AsyncMock(
        return_value=_active_profile(status=DailyHelpStatus.INACTIVE.value)
    )

    with pytest.raises(NotFoundException):
        await svc.create_rating(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=CreateDailyHelpRatingRequest(stars=Decimal("4.0")),
        )


@pytest.mark.asyncio
async def test_create_rating_fails_on_duplicate():
    svc = _rating_service(linked=True)
    svc.repo.insert_rating = AsyncMock(side_effect=UniqueViolationError("duplicate"))

    with pytest.raises(ConflictException) as exc:
        await svc.create_rating(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=CreateDailyHelpRatingRequest(stars=Decimal("4.0")),
        )

    assert exc.value.message_key == "daily_help.errors.duplicate_rating"


@pytest.mark.asyncio
async def test_update_rating_fails_without_household_link():
    svc = _rating_service(linked=False)

    with pytest.raises(ValidationException) as exc:
        await svc.update_rating(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=UpdateDailyHelpRatingRequest(stars=Decimal("5.0")),
        )

    assert exc.value.message_key == "daily_help.errors.rating_household_link_required"


@pytest.mark.asyncio
async def test_update_rating_fails_when_not_found():
    svc = _rating_service(linked=True)
    svc.repo.update_rating = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException) as exc:
        await svc.update_rating(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            body=UpdateDailyHelpRatingRequest(stars=Decimal("5.0")),
        )

    assert exc.value.message_key == "daily_help.errors.rating_not_found"


# ---------------------------------------------------------------------------
# Attendance — success and failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_attendance_success_builds_calendar():
    svc = _rating_service(linked=True)
    svc._build_attendance_calendar = AsyncMock(
        return_value={
            "year": 2024,
            "month": 5,
            "days_in_month": 31,
            "present_count": 21,
            "absent_count": 2,
            "last_check_in_at": "2024-05-28T10:00:00Z",
            "days": [{"date": "2024-05-01", "status": "present"}],
            "check_in_count": 21,
            "events": [],
        }
    )

    data = await svc.get_attendance(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        year=2024,
        month=5,
    )

    assert data["present_count"] == 21
    assert data["days_in_month"] == 31


@pytest.mark.asyncio
async def test_get_attendance_fails_on_invalid_month():
    svc = _rating_service(linked=True)

    with pytest.raises(ValidationException) as exc:
        await svc.get_attendance(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            year=2024,
            month=13,
        )

    assert exc.value.message_key == "daily_help.errors.invalid_attendance_month"


@pytest.mark.asyncio
async def test_mark_attendance_absence_success():
    svc = _rating_service(linked=True)
    svc.repo.upsert_attendance_absence = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.events_repo.list_check_in_dates_for_month = AsyncMock(return_value=[])

    result = await svc.mark_attendance_absence(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        attendance_date=date.today(),
    )

    assert result == {"date": date.today().isoformat(), "status": "absent"}


@pytest.mark.asyncio
async def test_mark_attendance_absence_fails_without_household_link():
    svc = _rating_service(linked=False)

    with pytest.raises(ValidationException) as exc:
        await svc.mark_attendance_absence(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            attendance_date=date.today(),
        )

    assert exc.value.message_key == "daily_help.errors.attendance_household_link_required"


@pytest.mark.asyncio
async def test_mark_attendance_absence_fails_on_future_date():
    svc = _rating_service(linked=True)

    with pytest.raises(ValidationException) as exc:
        await svc.mark_attendance_absence(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            attendance_date=date(2099, 1, 1),
        )

    assert exc.value.message_key == "daily_help.errors.attendance_date_in_future"


@pytest.mark.asyncio
async def test_mark_attendance_absence_fails_when_already_checked_in():
    svc = _rating_service(linked=True)
    svc.events_repo.list_check_in_dates_for_month = AsyncMock(return_value=[date(2024, 5, 22)])

    with pytest.raises(ConflictException) as exc:
        await svc.mark_attendance_absence(
            contact_id="contact-1",
            unit_id="unit-1",
            profile_id="profile-1",
            attendance_date=date(2024, 5, 22),
        )

    assert exc.value.message_key == "daily_help.errors.attendance_already_checked_in"
