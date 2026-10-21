"""Unit tests for marketplace listing rules."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from asyncpg import UniqueViolationError
from pydantic import ValidationError

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceReportReason,
    MarketplaceSaleRating,
    MarketplaceSoldType,
)
from apps.user_service.app.schemas.marketplace import (
    BrowseListingsQuery,
    CreateListingRequest,
    ListingMediaInput,
    MarkSoldRequest,
    ReportListingRequest,
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
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ValidationException,
)


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
        media_count=0,
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
async def test_seller_remove_deletes_draft_permanently():
    svc = _service()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="draft", title="Old phone"))
    svc.repo.delete_draft_listing = AsyncMock(return_value=True)
    result = await svc.remove_listing(
        contact_id="seller-1",
        listing_id="listing-1",
        unit_id="unit-1",
        removal_note="Do not need this listing",
    )
    assert result["status"] == "deleted"
    assert result["title"] == "Old phone"
    svc.repo.delete_draft_listing.assert_awaited_once()
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
    seller_uuid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    svc.repo.get_listing = AsyncMock(
        return_value=_listing(status="live", seller_contact_id=str(seller_uuid))
    )
    body = MarkSoldRequest(
        unit_id="unit-1",
        sold_type=MarketplaceSoldType.INSIDE,
        buyer_contact_id=seller_uuid,
        rating=MarketplaceSaleRating.SMOOTH,
    )
    with pytest.raises(ValidationException) as raised:
        await svc.mark_sold(contact_id=str(seller_uuid), listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.buyer_is_seller"
    svc.repo.update_listing.assert_not_called()
    svc.repo.insert_feedback.assert_not_called()


@pytest.mark.asyncio
async def test_mark_sold_outside_clears_buyer():
    svc = _service()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="live"))
    svc.repo.contact_in_organization = AsyncMock(return_value=True)
    body = MarkSoldRequest(unit_id="unit-1", sold_type=MarketplaceSoldType.OUTSIDE)
    result = await svc.mark_sold(contact_id="seller-1", listing_id="listing-1", body=body)
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["status"] == "sold"
    assert fields["buyer_contact_id"] is None
    assert fields["sold_type"] == "outside"
    assert result["sold_type"] == "outside"
    assert result["buyer_name"] is None
    svc.repo.insert_feedback.assert_not_called()


@pytest.mark.asyncio
async def test_mark_sold_inside_persists_sold_type_and_buyer():
    svc = _service()
    buyer_uuid = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="live"))
    svc.repo.contact_in_organization = AsyncMock(return_value=True)
    svc.repo.get_contact_card = AsyncMock(
        return_value={
            "first_name": "Ananya",
            "last_name": "M",
            "unit_label": "1503",
            "unit_code": "1503",
        }
    )
    body = MarkSoldRequest(
        unit_id="unit-1",
        sold_type=MarketplaceSoldType.INSIDE,
        buyer_contact_id=buyer_uuid,
    )
    result = await svc.mark_sold(contact_id="seller-1", listing_id="listing-1", body=body)
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["sold_type"] == "inside"
    assert fields["buyer_contact_id"] == str(buyer_uuid)
    assert result["sold_type"] == "inside"
    assert result["buyer_name"] == "Ananya M."


def test_mark_sold_request_inside_requires_buyer():
    with pytest.raises(ValidationError):
        MarkSoldRequest(unit_id="unit-1", sold_type=MarketplaceSoldType.INSIDE)


def test_mark_sold_request_outside_rejects_buyer():
    with pytest.raises(ValidationError):
        MarkSoldRequest(
            unit_id="unit-1",
            sold_type=MarketplaceSoldType.OUTSIDE,
            buyer_contact_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        )


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


def test_save_body():
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
async def test_my_listings_includes_cover_path():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.list_mine = AsyncMock(return_value=([_listing(status="sold")], 1))
    data = await svc.my_listings(
        contact_id="seller-1",
        status="all",
        page=1,
        page_size=20,
    )
    assert data["items"][0]["cover_path"] == "org/a.jpg"


@pytest.mark.asyncio
async def test_get_listing_detail_includes_seller_isometrik_id():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_listing = AsyncMock(
        return_value=_listing(
            seller_isometrik_user_id="6abdf6ad9126be00010e596f",
        )
    )
    svc.repo.count_other_live = AsyncMock(return_value=1)
    data = await svc.get_listing(contact_id="seller-1", listing_id="listing-1")
    assert data["seller_contact_id"] == "seller-1"
    assert data["isometrik_id"] == "6abdf6ad9126be00010e596f"
    assert data["more_from_seller_count"] == 1


@pytest.mark.asyncio
async def test_report_listing_maps_reason_to_display_text():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="live"))
    svc.repo.insert_report = AsyncMock(return_value={"id": "report-1"})
    svc.repo.count_reports_for_listing = AsyncMock(return_value=2)
    body = ReportListingRequest(
        unit_id="unit-1",
        reason=MarketplaceReportReason.SOLD_BUT_LISTED,
    )
    data = await svc.report_listing(contact_id="buyer-1", listing_id="listing-1", body=body)
    assert data["reason"] == "Sold but still listed"
    assert data["listing_report_count"] == 2
    assert data["listing_hidden"] is False


@pytest.mark.asyncio
async def test_report_listing_marks_hidden_at_three_reports():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="live"))
    svc.repo.insert_report = AsyncMock(return_value={"id": "report-1"})
    svc.repo.count_reports_for_listing = AsyncMock(return_value=3)
    body = ReportListingRequest(
        unit_id="unit-1",
        reason=MarketplaceReportReason.NOT_ALLOWED,
    )
    data = await svc.report_listing(contact_id="buyer-1", listing_id="listing-1", body=body)
    assert data["listing_hidden"] is True


@pytest.mark.asyncio
async def test_report_listing_rejects_own_listing():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_listing = AsyncMock(
        return_value=_listing(status="live", seller_contact_id="seller-1")
    )
    body = ReportListingRequest(
        unit_id="unit-1",
        reason=MarketplaceReportReason.NOT_ALLOWED,
    )
    with pytest.raises(ValidationException) as raised:
        await svc.report_listing(contact_id="seller-1", listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.cannot_report_own"


@pytest.mark.asyncio
async def test_report_listing_maps_duplicate_to_conflict():
    svc = _service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="live"))
    svc.repo.insert_report = AsyncMock(side_effect=UniqueViolationError("duplicate"))
    body = ReportListingRequest(
        unit_id="unit-1",
        reason=MarketplaceReportReason.SOMETHING_ELSE,
    )
    with pytest.raises(ConflictException) as raised:
        await svc.report_listing(contact_id="buyer-1", listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.report_exists"


@pytest.mark.asyncio
async def test_release_for_contact_delete_drops_drafts_removes_live_and_clears_saves():
    """Contact delete hard-deletes drafts, marks live listings removed, and clears bookmarks."""
    from apps.user_service.app.db.repositories.marketplace_repository import (
        MarketplaceRepository,
    )

    conn = MagicMock()
    conn.execute = AsyncMock()
    repo = MarketplaceRepository(db_connection=conn)

    await repo.release_for_contact_delete(
        organization_id="org-1",
        contact_id="seller-1",
        removed_by_user_id="admin-1",
        removal_note="Removed because the seller contact was deleted.",
    )

    assert conn.execute.await_count == 3
    draft_sql, *_ = conn.execute.await_args_list[0].args
    live_sql, *_ = conn.execute.await_args_list[1].args
    saved_sql, *_ = conn.execute.await_args_list[2].args
    assert "DELETE FROM marketplace_listings" in draft_sql
    assert "status = 'draft'" in draft_sql
    assert "status = 'removed'" in live_sql
    assert "status = 'live'" in live_sql
    assert "DELETE FROM marketplace_saved_items" in saved_sql


@pytest.mark.asyncio
async def test_release_for_contact_delete_skips_live_remove_without_actor():
    """Live listings stay unless an actor user id can satisfy the removed-row check."""
    from apps.user_service.app.db.repositories.marketplace_repository import (
        MarketplaceRepository,
    )

    conn = MagicMock()
    conn.execute = AsyncMock()
    repo = MarketplaceRepository(db_connection=conn)

    await repo.release_for_contact_delete(
        organization_id="org-1",
        contact_id="seller-1",
        removed_by_user_id=None,
        removal_note="Removed because the seller contact was deleted.",
    )

    assert conn.execute.await_count == 2
    assert "DELETE FROM marketplace_listings" in conn.execute.await_args_list[0].args[0]
    assert "DELETE FROM marketplace_saved_items" in conn.execute.await_args_list[1].args[0]
