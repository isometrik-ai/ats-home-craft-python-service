"""Unit tests for staff marketplace rules."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums.marketplace import MarketplaceAdminStatus
from apps.user_service.app.schemas.marketplace import AdminMarketplaceListQuery
from apps.user_service.app.services.marketplace_admin_service import (
    MarketplaceAdminService,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _admin_service() -> MarketplaceAdminService:
    svc = MarketplaceAdminService(db_connection=MagicMock(), user_context=MagicMock())
    svc.user_context.organization_id = "org-1"
    svc.repo = AsyncMock()
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
        ],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_get_admin_summary_expires_then_counts():
    svc = _admin_service()
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
    svc = _admin_service()
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
    svc = _admin_service()
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
    svc = _admin_service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.get_admin_listing = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_listing_admin(listing_id="missing")


@pytest.mark.asyncio
async def test_remove_listing_admin_takes_live_listing_down():
    svc = _admin_service()
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
    svc = _admin_service()
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
    svc = _admin_service()
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
    svc = _admin_service()
    svc.user_context.user_id = "staff-1"
    svc.repo.get_admin_listing = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.remove_listing_admin(
            listing_id="listing-1",
            removal_note="Taken down",
        )


@pytest.mark.asyncio
async def test_get_admin_summary_filters_project():
    svc = _admin_service()
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
    svc = _admin_service()
    svc.repo.expire_due = AsyncMock()
    svc.repo.list_admin_listings = AsyncMock(return_value=([_listing()], 1))
    query = AdminMarketplaceListQuery(q="table")
    data = await svc.list_listings_admin(query=query)
    assert query.project_id is None
    assert svc.repo.list_admin_listings.await_args.kwargs["query"] is query
    assert data["items"][0]["can_remove"] is True


@pytest.mark.asyncio
async def test_list_listings_admin_rejects_unknown_category():
    svc = _admin_service()
    svc.repo.expire_due = AsyncMock()
    query = AdminMarketplaceListQuery(category="not-a-catalog-slug")
    with pytest.raises(ValidationException) as raised:
        await svc.list_listings_admin(query=query)
    assert raised.value.message_key == "marketplace.errors.invalid_category"


@pytest.mark.asyncio
async def test_remove_listing_admin_rejects_blank_note():
    svc = _admin_service()
    svc.user_context.user_id = "staff-1"
    with pytest.raises(ValidationException) as raised:
        await svc.remove_listing_admin(listing_id="listing-1", removal_note="   ")
    assert raised.value.message_key == "marketplace.errors.removal_note_required"


@pytest.mark.asyncio
async def test_remove_listing_admin_passes_project_id():
    svc = _admin_service()
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
