"""Unit tests for marketplace listing rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceListingAction,
    MarketplaceReportDecision,
    MarketplaceReportReason,
    MarketplaceSaleRating,
)
from apps.user_service.app.schemas.marketplace import (
    CreateReportRequest,
    ListingActionRequest,
    MarkSoldRequest,
    ReviewReportRequest,
    SaveListingRequest,
    UpdateListingRequest,
)
from apps.user_service.app.services.marketplace_service import (
    MarketplaceService,
    publish_gaps,
    visible_flat,
)
from libs.shared_utils.http_exceptions import ValidationException


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
        "category": "Electronics",
        "subtype": None,
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
        "rules_accepted_at": datetime(2026, 10, 1, tzinfo=UTC),
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
    }
    base.update(overrides)
    return base


def test_publish_gaps_lists_what_a_sale_still_needs():
    gaps = publish_gaps(
        {
            "category": "Electronics",
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
            "category": "Electronics",
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
    svc.repo.count_media = AsyncMock(return_value=2)
    body = UpdateListingRequest(title="")
    with pytest.raises(ValidationException) as raised:
        await svc.update_listing(contact_id="seller-1", listing_id="listing-1", body=body)
    assert raised.value.message_key == "marketplace.errors.live_incomplete"
    svc.repo.update_listing.assert_not_called()


@pytest.mark.asyncio
async def test_live_edit_keeps_the_post_live():
    svc = _service()
    updated = _listing(title="Study table")
    svc.repo.get_listing = AsyncMock(side_effect=[_listing(), updated])
    svc.repo.count_media = AsyncMock(return_value=2)
    svc.repo.list_media = AsyncMock(return_value=[])
    svc.repo.is_saved = AsyncMock(return_value=False)
    svc.repo.count_other_live = AsyncMock(return_value=0)
    body = UpdateListingRequest(title="Study table")
    result = await svc.update_listing(contact_id="seller-1", listing_id="listing-1", body=body)
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["title"] == "Study table"
    assert "status" not in fields
    assert result["status"] == "live"


@pytest.mark.asyncio
async def test_seller_remove_moves_a_live_post_to_draft():
    svc = _service()
    draft = _listing(status="draft")
    svc.repo.get_listing = AsyncMock(side_effect=[_listing(), draft])
    result = await svc.remove_listing(
        contact_id="seller-1", listing_id="listing-1", unit_id="unit-1"
    )
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields == {"status": "draft"}
    assert result["status"] == "draft"
    assert result["published_at"] == draft["published_at"]


@pytest.mark.asyncio
async def test_restore_puts_a_removed_draft_back_live():
    svc = _service()
    draft = _listing(status="draft")
    restored = _listing(status="live")
    svc.repo.get_listing = AsyncMock(side_effect=[draft, restored])
    svc.repo.count_media = AsyncMock(return_value=2)
    svc.repo.ensure_cover = AsyncMock()
    result = await svc.restore_listing(
        contact_id="seller-1", listing_id="listing-1", unit_id="unit-1"
    )
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields == {"status": "live"}
    assert result["status"] == "live"


@pytest.mark.asyncio
async def test_restore_rejects_a_draft_that_was_never_published():
    svc = _service()
    svc.repo.get_listing = AsyncMock(return_value=_listing(status="draft", published_at=None))
    with pytest.raises(ValidationException) as raised:
        await svc.restore_listing(contact_id="seller-1", listing_id="listing-1", unit_id="unit-1")
    assert raised.value.message_key == "marketplace.errors.not_restorable"


@pytest.mark.asyncio
async def test_restore_starts_a_new_window_when_the_old_one_has_passed():
    svc = _service()
    past = datetime.now(UTC) - timedelta(days=1)
    draft = _listing(status="draft", expires_at=past)
    restored = _listing(status="live", expires_at=past)
    svc.repo.get_listing = AsyncMock(side_effect=[draft, restored])
    svc.repo.count_media = AsyncMock(return_value=2)
    svc.repo.ensure_cover = AsyncMock()
    await svc.restore_listing(contact_id="seller-1", listing_id="listing-1", unit_id="unit-1")
    fields = svc.repo.update_listing.await_args.kwargs["fields"]
    assert fields["status"] == "live"
    assert fields["expires_at"] > datetime.now(UTC)


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


@pytest.mark.asyncio
async def test_report_does_not_change_the_listing():
    svc = _service()
    listing = _listing()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_listing = AsyncMock(return_value=listing)
    svc.repo.list_active_project_coords = AsyncMock(return_value=[])
    svc.repo.insert_report = AsyncMock(return_value={"id": "report-1"})
    body = CreateReportRequest(unit_id="unit-1", reason=MarketplaceReportReason.NOT_ALLOWED)
    result = await svc.create_report(contact_id="buyer-1", listing_id="listing-1", body=body)
    assert result["status"] == "open"
    svc.repo.update_listing.assert_not_called()


def test_listing_action_and_review_bodies():
    action = ListingActionRequest(unit_id="unit-1", action=MarketplaceListingAction.RESTORE)
    assert action.action == MarketplaceListingAction.RESTORE
    assert action.rules_accepted is None
    save = SaveListingRequest(unit_id="unit-1", saved=False)
    assert save.saved is False
    review = ReviewReportRequest(decision=MarketplaceReportDecision.DISMISS)
    assert review.removal_note is None
