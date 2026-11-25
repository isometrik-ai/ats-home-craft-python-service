"""Success and failure path tests for Daily Help ratings, reviews, and attendance."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg import UniqueViolationError
from pydantic import ValidationError

from apps.user_service.app.schemas.daily_help import (
    CreateDailyHelpRatingRequest,
    UpdateDailyHelpRatingRequest,
)
from apps.user_service.app.schemas.enums import (
    DailyHelpRatingAspect,
    DailyHelpRatingMediaType,
    DailyHelpRatingTrait,
    DailyHelpStatus,
)
from apps.user_service.app.services.daily_help_service import (
    DailyHelpService,
    _experience_label,
)
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
    assert svc.repo.insert_rating.await_args.kwargs["aspects"] == []
    assert svc.repo.insert_rating.await_args.kwargs["media"] == []


def test_rating_request_rejects_duplicate_aspects_and_mismatched_media():
    with pytest.raises(ValidationError):
        CreateDailyHelpRatingRequest(
            stars=Decimal("5.0"),
            aspects=[
                {"aspect": "punctuality", "stars": Decimal("5.0")},
                {"aspect": "punctuality", "stars": Decimal("4.0")},
            ],
        )
    with pytest.raises(ValidationError):
        CreateDailyHelpRatingRequest(
            stars=Decimal("5.0"),
            media=[
                {
                    "media_type": "video",
                    "file_path": "ratings/clip.mp4",
                    "mime_type": "image/jpeg",
                }
            ],
        )


@pytest.mark.asyncio
async def test_create_rating_stores_aspect_scores_and_media():
    svc = _rating_service(linked=True)
    svc.repo.insert_rating = AsyncMock()
    svc.repo.get_rating_summary = AsyncMock(
        return_value={
            "rating_count": 1,
            "review_count": 1,
            "average_stars": 5.0,
            "star_distribution": {"5": 1},
            "category_averages": {"punctuality": 5.0, "work_quality": 4.0},
            "trait_counts": {},
        }
    )

    summary = await svc.create_rating(
        contact_id="contact-1",
        unit_id="unit-1",
        profile_id="profile-1",
        body=CreateDailyHelpRatingRequest(
            stars=Decimal("5.0"),
            comment="  Always on time and the flat is spotless.  ",
            aspects=[
                {"aspect": DailyHelpRatingAspect.PUNCTUALITY, "stars": Decimal("5.0")},
                {"aspect": DailyHelpRatingAspect.WORK_QUALITY, "stars": Decimal("4.0")},
                {"aspect": DailyHelpRatingAspect.BEHAVIOR, "stars": Decimal("5.0")},
                {"aspect": DailyHelpRatingAspect.COMMUNICATION, "stars": Decimal("5.0")},
            ],
            media=[
                {
                    "media_type": DailyHelpRatingMediaType.PHOTO,
                    "file_path": " daily-help/ratings/photo-1.jpg ",
                    "file_name": "photo-1.jpg",
                    "mime_type": "image/jpeg",
                }
            ],
        ),
    )

    assert summary.category_averages["punctuality"] == 5.0
    payload = svc.repo.insert_rating.await_args.kwargs
    assert payload["stars"] == Decimal("5.0")
    assert payload["comment"] == "Always on time and the flat is spotless."
    assert payload["aspects"] == [
        {"aspect": "punctuality", "stars": Decimal("5.0")},
        {"aspect": "work_quality", "stars": Decimal("4.0")},
        {"aspect": "behavior", "stars": Decimal("5.0")},
        {"aspect": "communication", "stars": Decimal("5.0")},
    ]
    assert payload["media"] == [
        {
            "media_type": "photo",
            "file_path": "daily-help/ratings/photo-1.jpg",
            "file_name": "photo-1.jpg",
            "mime_type": "image/jpeg",
            "file_size_bytes": None,
            "sort_order": 0,
        }
    ]


@pytest.mark.asyncio
async def test_update_rating_replaces_aspects_and_media():
    svc = _rating_service(linked=True)
    svc.repo.update_rating = AsyncMock(
        return_value={
            "id": "rating-1",
            "stars": 5.0,
            "comment": "Excellent service",
            "traits": [],
            "aspects": [
                {"aspect": "punctuality", "stars": Decimal("5.0")},
                {"aspect": "behavior", "stars": Decimal("4.0")},
            ],
            "media": [
                {
                    "media_type": "video",
                    "file_path": "daily-help/ratings/clip.mp4",
                    "file_name": "clip.mp4",
                    "mime_type": "video/mp4",
                    "file_size_bytes": 2048,
                    "sort_order": 1,
                }
            ],
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
            comment="  Excellent service  ",
            aspects=[
                {"aspect": "punctuality", "stars": Decimal("5.0")},
                {"aspect": "behavior", "stars": Decimal("4.0")},
            ],
            media=[
                {
                    "media_type": "video",
                    "file_path": "daily-help/ratings/clip.mp4",
                    "file_name": "clip.mp4",
                    "mime_type": "VIDEO/MP4",
                    "file_size_bytes": 2048,
                    "sort_order": 1,
                }
            ],
        ),
    )

    payload = svc.repo.update_rating.await_args.kwargs
    assert payload["comment"] == "Excellent service"
    assert payload["aspects"][1]["aspect"] == "behavior"
    assert payload["media"][0]["mime_type"] == "video/mp4"
    assert rating.experience_label == "great"
    assert rating.aspects[0].aspect == "punctuality"
    assert rating.aspects[0].stars == 5.0
    assert rating.media[0].media_type == "video"
    assert rating.media[0].file_path == "daily-help/ratings/clip.mp4"
    assert "id" not in rating.media[0].model_dump()


def test_rating_request_accepts_photo_and_video_mime_types():
    photo = CreateDailyHelpRatingRequest(
        stars=Decimal("4.0"),
        media=[{"media_type": "photo", "file_path": "ratings/a.heic", "mime_type": "image/heic"}],
    )
    video = CreateDailyHelpRatingRequest(
        stars=Decimal("4.0"),
        media=[
            {
                "media_type": "video",
                "file_path": "ratings/a.mov",
                "mime_type": "video/quicktime",
            }
        ],
    )
    assert photo.media[0].mime_type == "image/heic"
    assert video.media[0].mime_type == "video/quicktime"
    assert photo.stars == Decimal("4.0")


def test_rating_request_rejects_invalid_experience_fields():
    invalid_bodies = [
        {"stars": Decimal("0")},
        {"stars": Decimal("5.5")},
        {"stars": Decimal("5.0"), "aspects": [{"aspect": "craftsmanship", "stars": 5}]},
        {
            "stars": Decimal("5.0"),
            "aspects": [{"aspect": "punctuality", "stars": Decimal("0.4")}],
        },
        {
            "stars": Decimal("5.0"),
            "aspects": [{"aspect": "punctuality", "stars": Decimal("5.0")}] * 5,
        },
        {
            "stars": Decimal("5.0"),
            "media": [
                {"media_type": "photo", "file_path": f"ratings/{index}.jpg"} for index in range(6)
            ],
        },
        {"stars": Decimal("5.0"), "media": [{"media_type": "photo", "file_path": "   "}]},
        {"stars": Decimal("5.0"), "media": [{"media_type": "audio", "file_path": "ratings/a.mp3"}]},
        {
            "stars": Decimal("5.0"),
            "media": [
                {"media_type": "photo", "file_path": "ratings/a.jpg", "mime_type": "video/mp4"}
            ],
        },
        {
            "stars": Decimal("5.0"),
            "media": [{"media_type": "photo", "file_path": "ratings/a.jpg", "file_size_bytes": 0}],
        },
        {"stars": Decimal("5.0"), "unknown": "nope"},
    ]
    for body in invalid_bodies:
        with pytest.raises(ValidationError):
            CreateDailyHelpRatingRequest(**body)


def test_experience_label_matches_star_row():
    assert _experience_label(1) == "terrible"
    assert _experience_label(2) == "bad"
    assert _experience_label(3) == "okay"
    assert _experience_label(4) == "good"
    assert _experience_label(5) == "great"
    assert _experience_label(4.5) == "great"


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
    assert rating.experience_label == "great"
    assert rating.aspects == []
    assert rating.media == []
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
