"""Unit tests for marketplace listing rules."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceAdminStatus,
    MarketplaceSaleRating,
)
from apps.user_service.app.schemas.marketplace import (
    AdminMarketplaceListQuery,
    BrowseListingsQuery,
    CreateListingRequest,
    ListingMediaInput,
    MarkSoldRequest,
    PublishListingRequest,
    SaveListingRequest,
    UpdateListingRequest,
)
from apps.user_service.app.services.marketplace_service import (
    MarketplaceService,
    cover_path,
    listing_media,
    publish_gaps,
    visible_flat,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _service() -> MarketplaceService:
    svc = MarketplaceService(db_connection=MagicMock(), user_context=MagicMock())
    svc.user_context.organization_id = "org-1"
    svc.repo = AsyncMock()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.repo.get_posting_role = AsyncMock(return_value={"role_type": "Owner", "started_at": None})
    svc.repo.get_unit_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "project_id": "project-1",
            "tower_id": "tower-1",
            "unit_code": "B-1104",
            "unit_label": "B-1104",
            "project_name": "ATS Nobility",
            "project_latitude": None,
            "project_longitude": None,
            "tower_name": "Tower B",
            "tower_latitude": None,
            "tower_longitude": None,
        }
    )
    return svc


def _listing(**overrides):
    base = {
        "id": "listing-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": "unit-1",
        "tower_id": "tower-1",
        "seller_contact_id": "seller-1",
        "category": "electronics",
        "subtype": "mobiles_tablets",
        "kind": "sale",
        "status": "live",
        "title": "Study table with chair",
        "description": "Used about a year.",
        "purchase_year": 2025,
        "price_amount": Decimal("4500"),
        "original_price_amount": Decimal("9000"),
        "negotiable": True,
        "brand": "Godrej",
        "condition": "lightly_used",
        "product_url": None,
        "show_flat_number": True,
        "original_bill_available": False,
        "published_at": datetime(2026, 10, 1, tzinfo=UTC),
        "expires_at": datetime(2026, 10, 31, tzinfo=UTC),
        "renewal_count": 0,
        "sold_at": None,
        "buyer_contact_id": None,
        "removed_at": None,
        "removal_note": None,
        "removed_by_user_id": None,
        "unit_code": "B-1104",
        "unit_label": "B-1104",
        "tower_name": "Tower B",
        "project_name": "ATS Nobility",
        "seller_first_name": "Rohan",
        "seller_last_name": "Bose",
        "seller_role": "Owner",
        "seller_role_started_at": None,
        "tower_latitude": None,
        "tower_longitude": None,
        "media": [
            {
                "type": "image",
                "path": "org/a.jpg",
                "file_type": "image/jpeg",
                "preview_path": "org/a_preview.jpg",
                "description": "Front",
                "order": 1,
            },
            {
                "type": "video",
                "path": "org/b.mp4",
                "file_type": "video/mp4",
                "preview_path": "org/b_preview.jpg",
                "description": "Walkaround",
                "order": 2,
            },
        ],
    }
    base.update(overrides)
    return base


def test_publish_gaps_lists_what_a_sale_still_needs():
    gaps = publish_gaps(
        {
            "category": "electronics",
            "subtype": "mobiles_tablets",
            "kind": "sale",
            "title": "",
            "description": None,
            "purchase_year": None,
            "price_amount": None,
            "condition": None,
            "unit_id": None,
        },
        media_count=1,
    )
    assert gaps == [
        "media",
        "title",
        "description",
        "purchase_year",
        "price",
        "condition",
        "pickup",
    ]


def test_giveaway_does_not_require_a_price():
    gaps = publish_gaps(
        {
            "category": "electronics",
            "subtype": "mobiles_tablets",
            "kind": "giveaway",
            "title": "Cartons",
            "description": "Free to collect.",
            "purchase_year": 2024,
            "price_amount": None,
            "condition": "well_used",
            "unit_id": "unit-1",
        },
        media_count=2,
    )
    assert gaps == []


def test_furniture_draft_needs_a_subtype():
    gaps = publish_gaps(
        {
            "category": "furniture",
            "kind": "giveaway",
            "title": "Desk",
            "description": "Free to collect.",
            "purchase_year": 2024,
            "price_amount": None,
            "condition": "well_used",
            "unit_id": "unit-1",
        },
        media_count=2,
    )
    assert gaps == ["subtype"]


def test_visible_flat_follows_the_seller_and_the_toggle():
    listing = _listing(show_flat_number=False)
    assert (
        visible_flat(listing=listing, viewer_contact_id="seller-1", viewer_project_id="project-1")
        == "B-1104"
    )
    assert (
        visible_flat(listing=listing, viewer_contact_id="buyer-1", viewer_project_id="project-1")
        is None
    )
    shown = _listing(show_flat_number=True)
    assert (
        visible_flat(listing=shown, viewer_contact_id="buyer-1", viewer_project_id="project-1")
        == "B-1104"
    )
    assert (
        visible_flat(listing=shown, viewer_contact_id="buyer-1", viewer_project_id="other") is None
    )


@pytest.mark.asyncio
async def test_live_edit_rejects_a_blank_title_before_saving():
    svc = _service()
    svc.repo.get_listing = AsyncMock(return_value=_listing())
    body = UpdateListingRequest(unit_id="unit-1", title="")
    with pytest.raises(ValidationException) as raised:
        await svc.update_listing(contact_id="seller-1", listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.live_incomplete"
    svc.repo.update_listing.assert_not_called()


@pytest.mark.asyncio
async def test_live_edit_keeps_the_post_live():
    svc = _service()
    updated = _listing(title="Study table")
    svc.repo.get_listing = AsyncMock(side_effect=[_listing(), updated])
    svc.repo.is_saved = AsyncMock(return_value=False)
    svc.repo.count_other_live = AsyncMock(return_value=0)
    body = UpdateListingRequest(unit_id="unit-1", title="Study table")
    result = await svc.update_listing(contact_id="seller-1", listing_id="listing-1", body=body)
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["title"] == "Study table"
    assert "status" not in fields
    assert result["status"] == "live"


def test_update_rejects_status_in_the_body():
    with pytest.raises(ValidationError):
        UpdateListingRequest(unit_id="unit-1", status="sold")


@pytest.mark.asyncio
async def test_update_rejects_a_removed_listing():
    svc = _service()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="removed"))
    body = UpdateListingRequest(unit_id="unit-1", title="Study table")
    with pytest.raises(ValidationException) as raised:
        await svc.update_listing(contact_id="seller-1", listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.not_editable"
    svc.repo.update_listing.assert_not_called()


@pytest.mark.asyncio
async def test_seller_remove_soft_deletes_a_live_post():
    svc = _service()
    svc.user_context.user_id = "user-1"
    removed = _listing(status="removed", removal_note="No longer selling")
    svc.repo.get_listing = AsyncMock(side_effect=[_listing(), removed])
    result = await svc.remove_listing(
        contact_id="seller-1",
        listing_id="listing-1",
        unit_id="unit-1",
        removal_note="No longer selling",
    )
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["status"] == "removed"
    assert fields["removal_note"] == "No longer selling"
    assert fields["removed_by_user_id"] == "user-1"
    assert result["status"] == "removed"


@pytest.mark.asyncio
async def test_mark_sold_rejects_the_seller_as_buyer():
    svc = _service()
    svc.repo.get_listing = AsyncMock(return_value=_listing())
    body = MarkSoldRequest(
        unit_id="unit-1", buyer_contact_id="seller-1", rating=MarketplaceSaleRating.SMOOTH
    )
    with pytest.raises(ValidationException) as raised:
        await svc.mark_sold(contact_id="seller-1", listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.buyer_is_seller"
    svc.repo.update_listing.assert_not_called()
    svc.repo.insert_feedback.assert_not_called()


def test_cover_path_prefers_the_first_image():
    media = listing_media(
        {
            "media": [
                {
                    "type": "video",
                    "path": "clip.mp4",
                    "preview_path": "clip.jpg",
                    "order": 2,
                },
                {
                    "type": "image",
                    "path": "photo.jpg",
                    "preview_path": "photo_preview.jpg",
                    "order": 1,
                },
            ]
        }
    )
    assert [item["path"] for item in media] == ["photo.jpg", "clip.mp4"]
    assert cover_path(media) == "photo.jpg"


def test_cover_path_uses_video_preview_when_there_is_no_image():
    media = [
        {
            "type": "video",
            "path": "clip.mp4",
            "preview_path": "clip.jpg",
            "order": 1,
        }
    ]
    assert cover_path(media) == "clip.jpg"


@pytest.mark.asyncio
async def test_create_listing_writes_media_on_the_listing():
    svc = _service()
    created = _listing(status="draft", id="listing-1")
    svc.repo.insert_listing = AsyncMock(return_value={"id": "listing-1"})
    svc.repo.update_listing = AsyncMock()
    svc.repo.get_listing = AsyncMock(return_value=created)
    svc.repo.is_saved = AsyncMock(return_value=False)
    svc.repo.count_other_live = AsyncMock(return_value=0)
    body = CreateListingRequest(
        unit_id="unit-1",
        category="electronics",
        subtype="mobiles_tablets",
        media=[
            ListingMediaInput(
                type="image",
                path="org/a.jpg",
                file_type="image/jpeg",
                description="Front",
                order=1,
            ),
            ListingMediaInput(
                type="video",
                path="org/b.mp4",
                file_type="VIDEO/MP4",
                preview_path="org/b_preview.jpg",
                order=2,
            ),
        ],
    )
    await svc.create_listing(contact_id="seller-1", body=body)
    insert = svc.repo.insert_listing.await_args.kwargs
    assert insert["category"] == "electronics"
    assert insert["subtype"] == "mobiles_tablets"
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["media"][0]["type"] == "image"
    assert fields["media"][0]["preview_path"] is None
    assert fields["media"][1]["type"] == "video"
    assert fields["media"][1]["file_type"] == "video/mp4"
    assert fields["media"][1]["preview_path"] == "org/b_preview.jpg"


def test_video_media_requires_preview_path():
    with pytest.raises(ValidationError):
        ListingMediaInput(
            type="video",
            path="org/clip.mp4",
            file_type="video/mp4",
            order=1,
        )


@pytest.mark.asyncio
async def test_create_listing_rejects_an_unknown_file_type():
    svc = _service()
    svc.repo.insert_listing = AsyncMock(return_value={"id": "listing-1"})
    body = CreateListingRequest(
        unit_id="unit-1",
        category="electronics",
        subtype="mobiles_tablets",
        media=[
            ListingMediaInput(
                type="image",
                path="org/a.gif",
                file_type="image/gif",
                order=1,
            )
        ],
    )
    with pytest.raises(ValidationException) as raised:
        await svc.create_listing(contact_id="seller-1", body=body)
    assert raised.value.message_key == "marketplace.errors.invalid_file_type"


@pytest.mark.asyncio
async def test_create_listing_rejects_a_video_mime_on_an_image():
    svc = _service()
    svc.repo.insert_listing = AsyncMock(return_value={"id": "listing-1"})
    body = CreateListingRequest(
        unit_id="unit-1",
        category="electronics",
        subtype="mobiles_tablets",
        media=[
            ListingMediaInput(
                type="image",
                path="org/a.mp4",
                file_type="video/mp4",
                order=1,
            )
        ],
    )
    with pytest.raises(ValidationException) as raised:
        await svc.create_listing(contact_id="seller-1", body=body)
    assert raised.value.message_key == "marketplace.errors.invalid_file_type"


def test_publish_and_save_bodies():
    publish = PublishListingRequest(unit_id="unit-1")
    assert publish.unit_id == "unit-1"
    save = SaveListingRequest(unit_id="unit-1", saved=False)
    assert save.saved is False


@pytest.mark.asyncio
async def test_list_listings_passes_browse_query():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.list_listings = AsyncMock(return_value=([_listing()], 1))
    svc.repo.is_saved = AsyncMock(return_value=False)
    query = BrowseListingsQuery(q="table", category="furniture", page=1, page_size=20)
    data = await svc.list_listings(contact_id="seller-1", query=query)
    kwargs = svc.repo.list_listings.await_args.kwargs
    assert kwargs["query"] is query
    assert kwargs["category"] == "furniture"
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_get_admin_summary_expires_then_counts():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_admin_summary = AsyncMock(
        return_value={
            "active_count": 12,
            "sold_count": 2,
            "past_count": 1,
            "removed_count": 3,
        }
    )
    data = await svc.get_admin_summary()
    svc.repo.expire_due.assert_awaited_once_with(organization_id="org-1")
    assert svc.repo.get_admin_summary.await_args.kwargs["project_id"] is None
    assert data["active_count"] == 12
    assert data["past_count"] == 1


@pytest.mark.asyncio
async def test_list_listings_admin_maps_filters():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.list_admin_listings = AsyncMock(return_value=([_listing()], 1))
    query = AdminMarketplaceListQuery(
        project_id="project-1",
        q="table",
        status=MarketplaceAdminStatus.LIVE,
        category="furniture",
        page=1,
        page_size=20,
    )
    data = await svc.list_listings_admin(query=query)
    kwargs = svc.repo.list_admin_listings.await_args.kwargs
    assert kwargs["query"] is query
    assert kwargs["category"] == "furniture"
    assert data["total"] == 1
    assert data["items"][0]["resident_name"] == "Rohan B."
    assert data["items"][0]["unit_label"] == "B-1104"
    assert data["items"][0]["project_id"] == "project-1"
    assert data["items"][0]["can_remove"] is True
    assert "tower" not in AdminMarketplaceListQuery.model_fields


@pytest.mark.asyncio
async def test_get_listing_admin_builds_history_and_unit_stats():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_admin_listing = AsyncMock(return_value=_listing())
    svc.repo.count_unit_listing_stats = AsyncMock(
        return_value={
            "listings_from_unit_total": 3,
            "listings_from_unit_active": 2,
            "removed_before_count": 1,
        }
    )
    data = await svc.get_listing_admin(listing_id="listing-1")
    assert data["listings_from_unit_total"] == 3
    assert data["removed_before_count"] == 1
    assert data["history"][0]["event"] == "posted"
    assert data["history"][1]["event"] == "live"
    assert data["can_remove"] is True


@pytest.mark.asyncio
async def test_get_listing_admin_missing():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_admin_listing = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_listing_admin(listing_id="missing")


@pytest.mark.asyncio
async def test_remove_listing_admin_takes_live_listing_down():
    svc = _service()
    svc.user_context.user_id = "staff-1"
    svc.repo.remove_live_listing_admin = AsyncMock(return_value=True)
    svc.repo.get_admin_listing = AsyncMock(
        side_effect=[
            _listing(status="live"),
            _listing(status="removed", removal_note="Photos didn't match"),
        ]
    )
    svc.repo.expire_due = AsyncMock()
    svc.repo.count_unit_listing_stats = AsyncMock(
        return_value={
            "listings_from_unit_total": 1,
            "listings_from_unit_active": 0,
            "removed_before_count": 0,
        }
    )
    data = await svc.remove_listing_admin(
        listing_id="listing-1",
        removal_note="Photos didn't match",
    )
    kwargs = svc.repo.remove_live_listing_admin.await_args.kwargs
    assert kwargs["project_id"] is None
    assert kwargs["listing_id"] == "listing-1"
    assert kwargs["removed_by_user_id"] == "staff-1"
    assert kwargs["removal_note"] == "Photos didn't match"
    assert data["status"] == "removed"
    assert data["removal_note"] == "Photos didn't match"


@pytest.mark.asyncio
async def test_remove_listing_admin_deletes_draft_permanently():
    svc = _service()
    svc.user_context.user_id = "staff-1"
    svc.repo.get_admin_listing = AsyncMock(return_value=_listing(status="draft", title="Old draft"))
    svc.repo.delete_draft_listing_admin = AsyncMock(return_value=True)
    data = await svc.remove_listing_admin(
        listing_id="listing-1",
        removal_note="Unpublished duplicate",
    )
    assert data["status"] == "deleted"
    assert data["title"] == "Old draft"
    svc.repo.delete_draft_listing_admin.assert_awaited_once()
    svc.repo.remove_live_listing_admin.assert_not_called()


@pytest.mark.asyncio
async def test_remove_listing_admin_rejects_non_live():
    svc = _service()
    svc.user_context.user_id = "staff-1"
    svc.repo.remove_live_listing_admin = AsyncMock(return_value=False)
    svc.repo.get_admin_listing = AsyncMock(return_value=_listing(status="sold"))
    with pytest.raises(ValidationException) as raised:
        await svc.remove_listing_admin(
            listing_id="listing-1",
            removal_note="Taken down",
        )
    assert raised.value.message_key == "marketplace.errors.not_live"


@pytest.mark.asyncio
async def test_remove_listing_admin_missing_listing():
    svc = _service()
    svc.user_context.user_id = "staff-1"
    svc.repo.get_admin_listing = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.remove_listing_admin(
            listing_id="listing-1",
            removal_note="Taken down",
        )


@pytest.mark.asyncio
async def test_get_admin_summary_filters_project():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_admin_summary = AsyncMock(
        return_value={
            "active_count": 4,
            "sold_count": 1,
            "past_count": 0,
            "removed_count": 1,
        }
    )
    data = await svc.get_admin_summary(project_id="project-1")
    assert svc.repo.get_admin_summary.await_args.kwargs["project_id"] == "project-1"
    assert data["active_count"] == 4


@pytest.mark.asyncio
async def test_list_listings_admin_org_wide():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.list_admin_listings = AsyncMock(return_value=([_listing()], 1))
    query = AdminMarketplaceListQuery(q="table")
    data = await svc.list_listings_admin(query=query)
    assert query.project_id is None
    assert svc.repo.list_admin_listings.await_args.kwargs["query"] is query
    assert data["items"][0]["can_remove"] is True


@pytest.mark.asyncio
async def test_list_listings_admin_rejects_unknown_category():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    query = AdminMarketplaceListQuery(category="not-a-catalog-slug")
    with pytest.raises(ValidationException) as raised:
        await svc.list_listings_admin(query=query)
    assert raised.value.message_key == "marketplace.errors.invalid_category"


@pytest.mark.asyncio
async def test_remove_listing_admin_rejects_blank_note():
    svc = _service()
    svc.user_context.user_id = "staff-1"
    with pytest.raises(ValidationException) as raised:
        await svc.remove_listing_admin(listing_id="listing-1", removal_note="   ")
    assert raised.value.message_key == "marketplace.errors.removal_note_required"


@pytest.mark.asyncio
async def test_remove_listing_admin_passes_project_id():
    svc = _service()
    svc.user_context.user_id = "staff-1"
    svc.repo.remove_live_listing_admin = AsyncMock(return_value=True)
    svc.repo.get_admin_listing = AsyncMock(
        side_effect=[
            _listing(status="live"),
            _listing(status="removed"),
        ]
    )
    svc.repo.expire_due = AsyncMock()
    svc.repo.count_unit_listing_stats = AsyncMock(
        return_value={
            "listings_from_unit_total": 1,
            "listings_from_unit_active": 0,
            "removed_before_count": 0,
        }
    )
    await svc.remove_listing_admin(
        listing_id="listing-1",
        removal_note="Taken down",
        project_id="project-1",
    )
    assert svc.repo.remove_live_listing_admin.await_args.kwargs["project_id"] == "project-1"
