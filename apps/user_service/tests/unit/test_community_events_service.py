"""Unit tests for community events admin service helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.community_events import (
    CommunityEventListItemResponse,
    CommunityEventListQuery,
    CommunityEventMediaInput,
)
from apps.user_service.app.schemas.enums import (
    CommunityEventCategory,
    CommunityEventPublishStatus,
    CommunityEventType,
)
from apps.user_service.app.services.community_events_service import (
    CommunityEventsService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"


def _event_row(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    today = date.today()
    base = {
        "id": EVENT_ID,
        "display_code": "EVT-1",
        "title": "Summer fest",
        "description": "",
        "category": CommunityEventCategory.SOCIAL.value,
        "publish_status": CommunityEventPublishStatus.DRAFT.value,
        "record_status": "active",
        "is_multi_day": False,
        "start_date": today,
        "end_date": today,
        "event_type": CommunityEventType.FREE.value,
        "max_tickets_per_resident": 4,
        "adult_price_minor": 0,
        "child_ticket_mode": "not_applicable",
        "child_price_minor": 0,
        "apply_tax": False,
        "tax_rate": 18.0,
        "currency": "INR",
        "tickets_booked": 0,
        "bookings_count": 0,
        "paid_bookings_count": 0,
        "revenue_collected_minor": 0,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _service() -> CommunityEventsService:
    svc = CommunityEventsService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="admin@example.com",
            organization_id="org-1",
        ),
    )
    svc.repo = MagicMock()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    return svc


class TestDeriveBookingState:
    def test_closed_when_not_published(self) -> None:
        row = {"publish_status": CommunityEventPublishStatus.DRAFT.value}
        assert CommunityEventsService._derive_booking_state(row) == "closed"

    def test_closed_when_past_booking_deadline(self) -> None:
        row = {
            "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
            "booking_closes_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        assert CommunityEventsService._derive_booking_state(row) == "closed"

    def test_closed_when_at_capacity(self) -> None:
        row = {
            "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
            "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
            "total_capacity": 50,
            "tickets_booked": 50,
        }
        assert CommunityEventsService._derive_booking_state(row) == "closed"

    def test_open_when_published_with_capacity(self) -> None:
        row = {
            "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
            "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
            "total_capacity": 50,
            "tickets_booked": 10,
        }
        assert CommunityEventsService._derive_booking_state(row) == "open"


class TestFacilityLocationLabel:
    def test_builds_from_tower_wing_floor(self) -> None:
        label = CommunityEventsService._facility_location_label(
            {
                "tower_name": "Tower A",
                "wing": "East",
                "floor_level": "3",
                "location_notes": "Near lift",
            }
        )
        assert "Tower A" in label
        assert "Near lift" in label

    def test_falls_back_to_location_notes(self) -> None:
        assert (
            CommunityEventsService._facility_location_label({"location_notes": "Clubhouse lawn"})
            == "Clubhouse lawn"
        )


class TestValidateGallery:
    def test_rejects_invalid_mime(self) -> None:
        with pytest.raises(ValidationException):
            CommunityEventsService._validate_gallery(
                [
                    CommunityEventMediaInput(
                        file_path="org/events/a.bin",
                        mime_type="application/octet-stream",
                        size_bytes=100,
                        sort_order=0,
                    )
                ]
            )

    def test_accepts_valid_mime(self) -> None:
        CommunityEventsService._validate_gallery(
            [
                CommunityEventMediaInput(
                    file_path="org/events/a.jpg",
                    mime_type="image/jpeg",
                    size_bytes=100,
                    sort_order=0,
                )
            ]
        )


class TestValidatePricing:
    def test_rejects_non_positive_adult_price(self) -> None:
        body = MagicMock()
        body.event_type = CommunityEventType.PAID
        body.adult_price_minor = 0
        with pytest.raises(ValidationException):
            CommunityEventsService._validate_pricing(body)


@pytest.mark.asyncio
async def test_get_summary():
    svc = _service()
    svc.repo.get_summary_counts = AsyncMock(
        return_value={
            "total_events": 5,
            "upcoming": 2,
            "total_rsvps": 20,
            "revenue_collected_minor": 5000,
            "tab_all": 5,
            "tab_draft": 1,
            "tab_published": 3,
            "tab_completed": 1,
            "tab_cancelled": 0,
            "tab_deleted": 0,
        }
    )
    summary = await svc.get_summary(project_id=PROJECT_ID)
    assert summary.total_events == 5
    assert summary.tabs["published"] == 3


@pytest.mark.asyncio
async def test_list_events_and_serialize():
    svc = _service()
    svc.repo.list_events = AsyncMock(
        return_value=(
            [
                {
                    "id": EVENT_ID,
                    "display_code": "EVT-1",
                    "title": "Fest",
                    "category": "community",
                    "start_date": date.today(),
                    "end_date": date.today(),
                    "is_multi_day": False,
                    "event_type": "free",
                    "publish_status": "published",
                    "record_status": "active",
                    "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
                    "total_capacity": 100,
                    "tickets_booked": 10,
                    "bookings_count": 5,
                    "paid_bookings_count": 3,
                    "revenue_collected_minor": 1000,
                    "ticket_breakdown_adult": 8,
                    "ticket_breakdown_child": 2,
                    "cover_image_path": "projects/org-1/cover.jpg",
                }
            ],
            1,
        )
    )
    items, total = await svc.list_events(
        project_id=PROJECT_ID,
        query=CommunityEventListQuery(tab="published"),
    )
    assert total == 1
    assert items[0].booking_state == "open"
    assert items[0].cover_image_path == "projects/org-1/cover.jpg"


def test_serialize_list_item_cover_image_path_null() -> None:
    svc = _service()
    item = svc._serialize_list_item(
        {
            "id": EVENT_ID,
            "display_code": "EVT-1",
            "title": "Fest",
            "category": "social",
            "start_date": date.today(),
            "end_date": date.today(),
            "is_multi_day": False,
            "event_type": "free",
            "publish_status": "published",
            "record_status": "active",
            "tickets_booked": 0,
            "bookings_count": 0,
            "paid_bookings_count": 0,
            "revenue_collected_minor": 0,
            "ticket_breakdown_adult": 0,
            "ticket_breakdown_child": 0,
        }
    )
    assert isinstance(item, CommunityEventListItemResponse)
    assert item.cover_image_path is None


@pytest.mark.asyncio
async def test_create_event_persists_cover_image_path():
    from apps.user_service.app.schemas.community_events import (
        CreateCommunityEventRequest,
    )
    from apps.user_service.app.schemas.enums import CommunityEventPublishMode

    svc = _service()
    cover_path = "projects/org-1/events/cover.jpg"
    row_with_cover = _event_row(cover_image_path=cover_path)
    svc.repo.allocate_event_sequence = AsyncMock(return_value=1)
    svc.repo.insert_event = AsyncMock(return_value=row_with_cover)
    svc.repo.fetch_event_by_id = AsyncMock(return_value=row_with_cover)
    svc.repo.list_gallery = AsyncMock(return_value=[])
    svc.repo.insert_audit_log = AsyncMock()
    svc._validate_facility = AsyncMock()
    svc._dispatch_publish_push = AsyncMock()

    detail = await svc.create_event(
        project_id=PROJECT_ID,
        body=CreateCommunityEventRequest(
            title="Fest",
            start_date=date.today(),
            end_date=date.today(),
            cover_image_path=cover_path,
            publish_mode=CommunityEventPublishMode.DRAFT,
        ),
    )

    insert_payload = svc.repo.insert_event.await_args.kwargs["data"]
    assert insert_payload["cover_image_path"] == cover_path
    assert detail.cover_image_path == cover_path


@pytest.mark.asyncio
async def test_get_event_not_found():
    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.get_event(project_id=PROJECT_ID, event_id=EVENT_ID)


@pytest.mark.asyncio
async def test_create_event_draft_and_publish_validation():
    svc = _service()
    svc.repo.allocate_event_sequence = AsyncMock(return_value=1)
    svc.repo.insert_event = AsyncMock(return_value=_event_row())
    svc.repo.fetch_event_by_id = AsyncMock(return_value=_event_row())
    svc.repo.list_gallery = AsyncMock(return_value=[])
    svc.repo.insert_audit_log = AsyncMock()
    svc._validate_facility = AsyncMock()
    svc._dispatch_publish_push = AsyncMock()

    from apps.user_service.app.schemas.community_events import (
        CreateCommunityEventRequest,
    )
    from apps.user_service.app.schemas.enums import CommunityEventPublishMode

    draft = await svc.create_event(
        project_id=PROJECT_ID,
        body=CreateCommunityEventRequest(
            title="Fest",
            start_date=date.today(),
            end_date=date.today(),
            publish_mode=CommunityEventPublishMode.DRAFT,
        ),
    )
    assert draft.title == "Summer fest"

    with pytest.raises(ValidationException):
        await svc.create_event(
            project_id=PROJECT_ID,
            body=CreateCommunityEventRequest(
                title="Fest",
                start_date=date.today() + timedelta(days=2),
                end_date=date.today(),
                publish_mode=CommunityEventPublishMode.DRAFT,
            ),
        )


@pytest.mark.asyncio
async def test_publish_event_success_and_conflict():
    svc = _service()
    published_row = _event_row(
        publish_status=CommunityEventPublishStatus.PUBLISHED.value,
        facility_id="fac-1",
        booking_closes_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    svc.repo.fetch_event_by_id = AsyncMock(
        side_effect=[
            _event_row(
                publish_status=CommunityEventPublishStatus.DRAFT.value,
                facility_id="fac-1",
                booking_closes_at=datetime.now(timezone.utc) + timedelta(days=1),
            ),
            published_row,
            published_row,
        ]
    )
    svc.repo.update_event_fields = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc.repo.list_gallery = AsyncMock(return_value=[])
    svc._dispatch_publish_push = AsyncMock()

    published = await svc.publish_event(project_id=PROJECT_ID, event_id=EVENT_ID)
    assert published.title == "Summer fest"

    svc.repo.fetch_event_by_id = AsyncMock(
        return_value=_event_row(publish_status=CommunityEventPublishStatus.PUBLISHED.value)
    )
    from libs.shared_utils.http_exceptions import ConflictException

    with pytest.raises(ConflictException):
        await svc.publish_event(project_id=PROJECT_ID, event_id=EVENT_ID)


@pytest.mark.asyncio
async def test_cancel_and_delete_event():
    svc = _service()
    svc.repo.update_event_fields = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=_event_row())
    svc.repo.list_gallery = AsyncMock(return_value=[])
    svc.repo.list_active_bookers_for_notification = AsyncMock(
        return_value=[
            {"contact_id": "contact-1", "user_id": "user-1"},
            {"contact_id": "contact-2", "user_id": "user-2"},
        ]
    )
    svc.notifications = MagicMock()
    svc.notifications.notify_event_cancelled = AsyncMock()

    from apps.user_service.app.schemas.community_events import (
        CancelCommunityEventRequest,
    )

    cancelled = await svc.cancel_event(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        body=CancelCommunityEventRequest(reason="Rain"),
    )
    assert cancelled.title == "Summer fest"
    assert svc.notifications.notify_event_cancelled.await_count == 2
    notified_contacts = {
        call.kwargs["contact_id"]
        for call in svc.notifications.notify_event_cancelled.await_args_list
    }
    assert notified_contacts == {"contact-1", "contact-2"}

    deleted = await svc.delete_event(project_id=PROJECT_ID, event_id=EVENT_ID)
    assert deleted.title == "Summer fest"


@pytest.mark.asyncio
async def test_get_event_success():
    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=_event_row())
    svc.repo.list_gallery = AsyncMock(return_value=[])
    detail = await svc.get_event(project_id=PROJECT_ID, event_id=EVENT_ID)
    assert detail.display_code == "EVT-1"


@pytest.mark.asyncio
async def test_update_event_success():
    from apps.user_service.app.schemas.community_events import (
        UpdateCommunityEventRequest,
    )

    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(
        side_effect=[_event_row(), _event_row(title="Updated fest")]
    )
    svc.repo.update_event_fields = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc.repo.list_gallery = AsyncMock(return_value=[])

    detail = await svc.update_event(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        body=UpdateCommunityEventRequest(title="Updated fest"),
    )
    assert detail.title == "Updated fest"
    svc.repo.update_event_fields.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_event_not_found():
    from apps.user_service.app.schemas.community_events import (
        UpdateCommunityEventRequest,
    )

    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.update_event(
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            body=UpdateCommunityEventRequest(title="Updated"),
        )


@pytest.mark.asyncio
async def test_update_event_deleted_conflict():
    from apps.user_service.app.schemas.community_events import (
        UpdateCommunityEventRequest,
    )
    from apps.user_service.app.schemas.enums import CommunityEventRecordStatus

    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(
        return_value=_event_row(record_status=CommunityEventRecordStatus.DELETED.value)
    )
    with pytest.raises(ConflictException):
        await svc.update_event(
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            body=UpdateCommunityEventRequest(title="Updated"),
        )


@pytest.mark.asyncio
async def test_update_event_structural_conflict_when_booked():
    from apps.user_service.app.schemas.community_events import (
        UpdateCommunityEventRequest,
    )

    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(
        return_value=_event_row(
            publish_status=CommunityEventPublishStatus.PUBLISHED.value,
            tickets_booked=5,
        )
    )
    with pytest.raises(ConflictException):
        await svc.update_event(
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            body=UpdateCommunityEventRequest(start_date=date.today() + timedelta(days=1)),
        )


@pytest.mark.asyncio
async def test_complete_and_restore_event():
    svc = _service()
    svc.repo.update_event_fields = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=_event_row())
    svc.repo.list_gallery = AsyncMock(return_value=[])

    completed = await svc.complete_event(project_id=PROJECT_ID, event_id=EVENT_ID)
    assert completed.title == "Summer fest"

    restored = await svc.restore_event(project_id=PROJECT_ID, event_id=EVENT_ID)
    assert restored.title == "Summer fest"


@pytest.mark.asyncio
async def test_list_bookings():
    from apps.user_service.app.schemas.community_events import (
        CommunityEventBookingListQuery,
    )

    svc = _service()
    now = datetime.now(timezone.utc)
    svc.repo.list_bookings_for_event = AsyncMock(
        return_value=(
            [
                {
                    "id": "booking-1",
                    "display_code": "BKG-1",
                    "contact_id": "contact-1",
                    "contact_name": "Resident",
                    "adult_tickets": 2,
                    "child_tickets": 0,
                    "total_tickets": 2,
                    "total_amount_minor": 0,
                    "currency": "INR",
                    "booking_status": "confirmed",
                    "payment_status": "paid",
                    "paid_at": now,
                    "booked_at": now,
                }
            ],
            1,
        )
    )
    items, total = await svc.list_bookings(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        query=CommunityEventBookingListQuery(),
    )
    assert total == 1
    assert items[0].display_code == "BKG-1"


@pytest.mark.asyncio
async def test_export_events_csv():
    from apps.user_service.app.schemas.community_events import CommunityEventExportQuery

    svc = _service()
    svc.repo.list_events_for_export = AsyncMock(
        return_value=[
            {
                "display_code": "EVT-1",
                "title": "Summer fest",
                "category": "social",
                "publish_status": "published",
                "event_type": "free",
                "start_date": date.today(),
                "end_date": date.today(),
                "tickets_booked": 0,
                "total_capacity": 100,
                "revenue_collected_minor": 0,
            }
        ]
    )
    csv_text = await svc.export_events_csv(
        project_id=PROJECT_ID,
        query=CommunityEventExportQuery(),
    )
    assert "Summer fest" in csv_text


@pytest.mark.asyncio
async def test_mark_booking_paid_and_waived_delegates():
    from apps.user_service.app.schemas.community_events import (
        MarkBookingPaidRequest,
        MarkBookingWaivedRequest,
    )

    svc = _service()
    now = datetime.now(timezone.utc)
    booking_row = {
        "id": "booking-1",
        "display_code": "BKG-1",
        "contact_id": "contact-1",
        "contact_name": "Resident",
        "adult_tickets": 2,
        "child_tickets": 0,
        "total_tickets": 2,
        "total_amount_minor": 500,
        "currency": "INR",
        "booking_status": "confirmed",
        "payment_status": "paid",
        "paid_at": now,
        "booked_at": now,
    }
    svc.booking_service = MagicMock()
    svc.booking_service.mark_paid = AsyncMock(return_value=booking_row)
    svc.booking_service.mark_waived = AsyncMock(return_value=booking_row)

    paid = await svc.mark_booking_paid(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        booking_id="booking-1",
        body=MarkBookingPaidRequest(payment_notes="Cash"),
    )
    assert paid.payment_status == "paid"

    waived = await svc.mark_booking_waived(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        booking_id="booking-1",
        body=MarkBookingWaivedRequest(payment_notes="Comp"),
    )
    assert waived.payment_status == "paid"


@pytest.mark.asyncio
async def test_export_bookings_csv():
    svc = _service()
    now = datetime.now(timezone.utc)
    svc.repo.list_bookings_for_export = AsyncMock(
        return_value=[
            {
                "display_code": "BKG-1",
                "contact_name": "Resident",
                "adult_tickets": 2,
                "child_tickets": 0,
                "total_tickets": 2,
                "total_amount_minor": 500,
                "currency": "INR",
                "booking_status": "confirmed",
                "payment_status": "paid",
                "paid_at": now,
                "booked_at": now,
            }
        ]
    )
    csv_text = await svc.export_bookings_csv(project_id=PROJECT_ID, event_id=EVENT_ID)
    assert "BKG-1" in csv_text


@pytest.mark.asyncio
async def test_create_event_publish_validation_failures():
    from apps.user_service.app.schemas.community_events import (
        CreateCommunityEventRequest,
    )
    from apps.user_service.app.schemas.enums import CommunityEventPublishMode

    svc = _service()
    svc._validate_facility = AsyncMock()

    with pytest.raises(ValidationException):
        await svc.create_event(
            project_id=PROJECT_ID,
            body=CreateCommunityEventRequest(
                title="Fest",
                start_date=date.today(),
                end_date=date.today(),
                publish_mode=CommunityEventPublishMode.PUBLISH,
            ),
        )

    svc._validate_facility = AsyncMock()
    with pytest.raises(ValidationException):
        await svc.create_event(
            project_id=PROJECT_ID,
            body=CreateCommunityEventRequest(
                title="Fest",
                start_date=date.today() + timedelta(days=2),
                end_date=date.today(),
                publish_mode=CommunityEventPublishMode.DRAFT,
            ),
        )


@pytest.mark.asyncio
async def test_publish_event_validation_failures():
    svc = _service()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.publish_event(project_id=PROJECT_ID, event_id=EVENT_ID)

    svc.repo.fetch_event_by_id = AsyncMock(
        return_value=_event_row(
            publish_status=CommunityEventPublishStatus.DRAFT.value,
            facility_id=None,
        )
    )
    with pytest.raises(ValidationException):
        await svc.publish_event(project_id=PROJECT_ID, event_id=EVENT_ID)

    svc.repo.fetch_event_by_id = AsyncMock(
        return_value=_event_row(publish_status=CommunityEventPublishStatus.PUBLISHED.value)
    )
    with pytest.raises(ConflictException):
        await svc.publish_event(project_id=PROJECT_ID, event_id=EVENT_ID)


@pytest.mark.asyncio
async def test_create_event_publish_mode_success():
    from apps.user_service.app.schemas.community_events import (
        CreateCommunityEventRequest,
    )
    from apps.user_service.app.schemas.enums import CommunityEventPublishMode

    svc = _service()
    svc.repo.allocate_event_sequence = AsyncMock(return_value=3)
    published_row = _event_row(publish_status=CommunityEventPublishStatus.PUBLISHED.value)
    svc.repo.insert_event = AsyncMock(return_value=published_row)
    svc.repo.insert_audit_log = AsyncMock()
    svc.repo.fetch_event_by_id = AsyncMock(return_value=published_row)
    svc.repo.list_gallery = AsyncMock(return_value=[])
    svc._validate_facility = AsyncMock()
    svc._dispatch_publish_push = AsyncMock()

    body = CreateCommunityEventRequest(
        title="Summer fest",
        start_date=date.today(),
        end_date=date.today(),
        publish_mode=CommunityEventPublishMode.PUBLISH,
        facility_id="fac-1",
        booking_closes_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    detail = await svc.create_event(project_id=PROJECT_ID, body=body)
    assert detail.title == "Summer fest"
    svc._dispatch_publish_push.assert_awaited_once()
