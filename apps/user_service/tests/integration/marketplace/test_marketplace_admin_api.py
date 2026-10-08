"""Integration tests for staff marketplace endpoints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.user_service.app.api import marketplace_admin as marketplace_admin_api
from apps.user_service.app.schemas.enums.marketplace import MarketplaceAdminStatus
from apps.user_service.app.schemas.marketplace import AdminMarketplaceListQuery
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

PROJECT_ID = "project-1"
LISTING_ID = "listing-1"

_ADMIN_CARD = {
    "id": LISTING_ID,
    "title": "Study table with chair",
    "status": "live",
    "kind": "sale",
    "price_amount": "4500",
    "resident_name": "Rohan B.",
    "unit_label": "B-1104",
    "tower_name": "Tower B",
    "can_remove": True,
}


def test_marketplace_admin_router_registered():
    """Admin router exposes project-scoped marketplace routes."""
    paths = [route.path for route in marketplace_admin_api.router.routes]
    assert "/projects/{project_id}/marketplace/summary" in paths
    assert "/projects/{project_id}/marketplace/catalog" in paths
    assert "/projects/{project_id}/marketplace/listings" in paths
    assert "/projects/{project_id}/marketplace/listings/{listing_id}" in paths
    assert "/projects/{project_id}/marketplace/listings/{listing_id}/remove" in paths
    assert not any("export" in path for path in paths)
    assert not any("settings" in path for path in paths)


def test_admin_status_filter_has_no_committee_option():
    """Staff status query does not include removed-by-committee."""
    values = {item.value for item in MarketplaceAdminStatus}
    assert values == {"all", "live", "sold", "past", "removed"}
    query = AdminMarketplaceListQuery(status=MarketplaceAdminStatus.REMOVED)
    assert "tower" not in query.model_fields
    assert "sort" not in query.model_fields
    assert "group" not in query.model_fields
    with pytest.raises(ValidationError):
        AdminMarketplaceListQuery(status="removed_by_committee")


@pytest.mark.asyncio
async def test_get_project_marketplace_summary(monkeypatch, client):
    """GET /projects/{id}/marketplace/summary returns the four header counts."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.marketplace_admin")

    async def fake_summary(_self, *, project_id):
        del _self
        assert project_id == PROJECT_ID
        return {
            "active_count": 12,
            "sold_count": 2,
            "past_count": 1,
            "removed_count": 3,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_project_summary",
        fake_summary,
    )

    response = await client.get(f"/v1/projects/{PROJECT_ID}/marketplace/summary")
    payload = assert_success(response)
    assert payload["data"]["active_count"] == 12
    assert payload["data"]["removed_count"] == 3


@pytest.mark.asyncio
async def test_list_project_marketplace_listings(monkeypatch, client):
    """GET /projects/{id}/marketplace/listings lists with search and status."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.marketplace_admin")

    async def fake_list(_self, *, project_id, query):
        del _self
        assert project_id == PROJECT_ID
        assert query.q == "table"
        assert query.status.value == "live"
        assert query.category == "furniture"
        return {"items": [_ADMIN_CARD], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings_for_project",
        fake_list,
    )

    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/marketplace/listings",
        params={"q": "table", "status": "live", "category": "furniture"},
    )
    payload = assert_success(response)
    assert payload["data"][0]["title"] == "Study table with chair"
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_get_project_marketplace_listing(monkeypatch, client):
    """GET /projects/{id}/marketplace/listings/{id} returns the drawer."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.marketplace_admin")

    async def fake_detail(_self, *, project_id, listing_id):
        del _self
        assert project_id == PROJECT_ID
        assert listing_id == LISTING_ID
        return {**_ADMIN_CARD, "history": [{"event": "posted"}]}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing_for_project",
        fake_detail,
    )

    response = await client.get(f"/v1/projects/{PROJECT_ID}/marketplace/listings/{LISTING_ID}")
    payload = assert_success(response)
    assert payload["data"]["id"] == LISTING_ID
    assert payload["data"]["history"][0]["event"] == "posted"


@pytest.mark.asyncio
async def test_get_project_marketplace_listing_missing(monkeypatch, client):
    """GET listing returns 404 when the row is not in the project."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.marketplace_admin")

    async def fake_detail(_self, *, project_id, listing_id):
        del _self, project_id, listing_id
        raise NotFoundException(
            message_key="marketplace.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing_for_project",
        fake_detail,
    )

    response = await client.get(f"/v1/projects/{PROJECT_ID}/marketplace/listings/{LISTING_ID}")
    assert_error(response, 404)


@pytest.mark.asyncio
async def test_remove_project_marketplace_listing(monkeypatch, client):
    """POST .../remove takes a live listing down."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.marketplace_admin")

    async def fake_remove(_self, *, project_id, listing_id, removal_note):
        del _self
        assert project_id == PROJECT_ID
        assert listing_id == LISTING_ID
        assert removal_note == "Photos didn't show the actual item"
        return {"id": LISTING_ID, "status": "removed", "removal_note": removal_note}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing_admin",
        fake_remove,
    )

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/marketplace/listings/{LISTING_ID}/remove",
        json={"removal_note": "Photos didn't show the actual item"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "removed"


@pytest.mark.asyncio
async def test_remove_project_marketplace_listing_not_live(monkeypatch, client):
    """POST .../remove rejects a listing that is not live."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.marketplace_admin")

    async def fake_remove(_self, *, project_id, listing_id, removal_note):
        del _self, project_id, listing_id, removal_note
        raise ValidationException(
            message_key="marketplace.errors.not_live",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing_admin",
        fake_remove,
    )

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/marketplace/listings/{LISTING_ID}/remove",
        json={"removal_note": "Taken down"},
    )
    assert_error(response, 422)
