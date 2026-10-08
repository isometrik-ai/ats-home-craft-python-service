"""Integration tests for staff marketplace endpoints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.user_service.app.api import marketplace_admin as marketplace_admin_api
from apps.user_service.app.schemas.enums.marketplace import MarketplaceAdminStatus
from apps.user_service.app.schemas.marketplace import AdminMarketplaceListQuery
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access_optional,
)
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
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
    "project_id": PROJECT_ID,
    "can_remove": True,
}


def test_marketplace_admin_router_registered():
    """Admin router exposes org-scoped marketplace routes."""
    paths = [route.path for route in marketplace_admin_api.router.routes]
    assert "/marketplace/admin/summary" in paths
    assert "/marketplace/admin/catalog" in paths
    assert "/marketplace/admin/listings" in paths
    assert "/marketplace/admin/listings/{listing_id}" in paths
    assert "/marketplace/admin/listings/{listing_id}/remove" in paths
    assert not any("{project_id}" in path for path in paths)
    assert not any("export" in path for path in paths)
    assert not any("settings" in path for path in paths)


def test_admin_status_filter_has_no_committee_option():
    """Staff status query does not include removed-by-committee."""
    values = {item.value for item in MarketplaceAdminStatus}
    assert values == {"all", "live", "sold", "past", "removed"}
    query = AdminMarketplaceListQuery(status=MarketplaceAdminStatus.REMOVED)
    assert query.project_id is None
    assert "tower" not in AdminMarketplaceListQuery.model_fields
    assert "sort" not in AdminMarketplaceListQuery.model_fields
    assert "group" not in AdminMarketplaceListQuery.model_fields
    with pytest.raises(ValidationError):
        AdminMarketplaceListQuery(status="removed_by_committee")


@pytest.mark.asyncio
async def test_get_marketplace_admin_summary(monkeypatch, client):
    """GET /marketplace/admin/summary returns the four header counts."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_summary(_self, *, project_id):
        del _self
        assert project_id is None
        return {
            "active_count": 12,
            "sold_count": 2,
            "past_count": 1,
            "removed_count": 3,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_admin_summary",
        fake_summary,
    )

    response = await client.get("/v1/marketplace/admin/summary")
    payload = assert_success(response)
    assert payload["data"]["active_count"] == 12
    assert payload["data"]["removed_count"] == 3


@pytest.mark.asyncio
async def test_get_marketplace_admin_summary_filters_project(monkeypatch, client):
    """GET /marketplace/admin/summary?project_id= scopes the header cards."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_summary(_self, *, project_id):
        del _self
        assert project_id == PROJECT_ID
        return {
            "active_count": 4,
            "sold_count": 1,
            "past_count": 0,
            "removed_count": 1,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_admin_summary",
        fake_summary,
    )

    response = await client.get(
        "/v1/marketplace/admin/summary",
        params={"project_id": PROJECT_ID},
    )
    payload = assert_success(response)
    assert payload["data"]["active_count"] == 4


@pytest.mark.asyncio
async def test_list_marketplace_admin_listings(monkeypatch, client):
    """GET /marketplace/admin/listings lists with optional project, search, and status."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_list(_self, *, query):
        del _self
        assert query.project_id == PROJECT_ID
        assert query.q == "table"
        assert query.status.value == "live"
        assert query.category == "furniture"
        return {"items": [_ADMIN_CARD], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings_admin",
        fake_list,
    )

    response = await client.get(
        "/v1/marketplace/admin/listings",
        params={
            "project_id": PROJECT_ID,
            "q": "table",
            "status": "live",
            "category": "furniture",
        },
    )
    payload = assert_success(response)
    assert payload["data"][0]["title"] == "Study table with chair"
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_get_marketplace_admin_listing(monkeypatch, client):
    """GET /marketplace/admin/listings/{id} returns the drawer."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_detail(_self, *, listing_id, project_id=None):
        del _self
        assert listing_id == LISTING_ID
        assert project_id is None
        return {**_ADMIN_CARD, "history": [{"event": "posted"}]}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing_admin",
        fake_detail,
    )

    response = await client.get(f"/v1/marketplace/admin/listings/{LISTING_ID}")
    payload = assert_success(response)
    assert payload["data"]["id"] == LISTING_ID
    assert payload["data"]["history"][0]["event"] == "posted"


@pytest.mark.asyncio
async def test_get_marketplace_admin_listing_filters_project(monkeypatch, client):
    """GET listing?project_id= returns the drawer for that society only."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_detail(_self, *, listing_id, project_id=None):
        del _self
        assert listing_id == LISTING_ID
        assert project_id == PROJECT_ID
        return {**_ADMIN_CARD, "history": [{"event": "posted"}]}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing_admin",
        fake_detail,
    )
    response = await client.get(
        f"/v1/marketplace/admin/listings/{LISTING_ID}",
        params={"project_id": PROJECT_ID},
    )
    payload = assert_success(response)
    assert payload["data"]["project_id"] == PROJECT_ID


@pytest.mark.asyncio
async def test_get_marketplace_admin_listing_missing(monkeypatch, client):
    """GET listing returns 404 when the row is not in the organization."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_detail(_self, *, listing_id, project_id=None):
        del _self, listing_id, project_id
        raise NotFoundException(
            message_key="marketplace.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing_admin",
        fake_detail,
    )

    response = await client.get(f"/v1/marketplace/admin/listings/{LISTING_ID}")
    assert_error(response, 404)


@pytest.mark.asyncio
async def test_remove_marketplace_admin_listing(monkeypatch, client):
    """POST .../remove takes a live listing down."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_remove(_self, *, listing_id, removal_note, project_id=None):
        del _self
        assert listing_id == LISTING_ID
        assert project_id is None
        assert removal_note == "Photos didn't show the actual item"
        return {"id": LISTING_ID, "status": "removed", "removal_note": removal_note}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing_admin",
        fake_remove,
    )

    response = await client.post(
        f"/v1/marketplace/admin/listings/{LISTING_ID}/remove",
        json={"removal_note": "Photos didn't show the actual item"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "removed"


@pytest.mark.asyncio
async def test_remove_marketplace_admin_listing_with_project_id(monkeypatch, client):
    """POST .../remove?project_id= scopes take-down to one society."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_remove(_self, *, listing_id, removal_note, project_id=None):
        del _self
        assert listing_id == LISTING_ID
        assert project_id == PROJECT_ID
        assert removal_note == "Taken down"
        return {
            "id": LISTING_ID,
            "status": "removed",
            "project_id": PROJECT_ID,
            "removal_note": removal_note,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing_admin",
        fake_remove,
    )
    response = await client.post(
        f"/v1/marketplace/admin/listings/{LISTING_ID}/remove",
        params={"project_id": PROJECT_ID},
        json={"removal_note": "Taken down"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "removed"
    assert payload["data"]["project_id"] == PROJECT_ID


@pytest.mark.asyncio
async def test_remove_marketplace_admin_listing_not_live(monkeypatch, client):
    """POST .../remove rejects a listing that is not live."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_remove(_self, *, listing_id, removal_note, project_id=None):
        del _self, listing_id, removal_note, project_id
        raise ValidationException(
            message_key="marketplace.errors.not_live",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing_admin",
        fake_remove,
    )

    response = await client.post(
        f"/v1/marketplace/admin/listings/{LISTING_ID}/remove",
        json={"removal_note": "Taken down"},
    )
    assert_error(response, 422)


@pytest.mark.asyncio
async def test_get_marketplace_admin_catalog_success(monkeypatch, client):
    """GET /marketplace/admin/catalog returns the static categories."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )
    response = await client.get("/v1/marketplace/admin/catalog")
    payload = assert_success(response)
    names = [item["name"] for item in payload["data"]["categories"]]
    assert "Furniture" in names
    assert "Services" not in names


@pytest.mark.asyncio
async def test_list_marketplace_admin_listings_org_wide(monkeypatch, client):
    """GET /marketplace/admin/listings without project_id lists the organization."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_list(_self, *, query):
        del _self
        assert query.project_id is None
        return {"items": [_ADMIN_CARD], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings_admin",
        fake_list,
    )
    response = await client.get("/v1/marketplace/admin/listings")
    payload = assert_success(response)
    assert payload["total"] == 1
    assert payload["data"][0]["project_id"] == PROJECT_ID


@pytest.mark.asyncio
async def test_list_marketplace_admin_listings_rejects_committee_status(monkeypatch, client):
    """GET /listings returns 422 for removed-by-committee."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )
    response = await client.get(
        "/v1/marketplace/admin/listings",
        params={"status": "removed_by_committee"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_marketplace_admin_listings_rejects_unknown_category(monkeypatch, client):
    """GET /listings returns 422 when the category is not in the catalog."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_list(_self, *, query):
        del _self, query
        raise ValidationException(
            message_key="marketplace.errors.invalid_category",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings_admin",
        fake_list,
    )
    response = await client.get(
        "/v1/marketplace/admin/listings",
        params={"category": "not-a-catalog-slug"},
    )
    assert_error(response, 422)


@pytest.mark.asyncio
async def test_remove_marketplace_admin_listing_rejects_empty_note(monkeypatch, client):
    """POST .../remove returns 422 when removal_note is empty."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )
    response = await client.post(
        f"/v1/marketplace/admin/listings/{LISTING_ID}/remove",
        json={"removal_note": ""},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remove_marketplace_admin_listing_missing(monkeypatch, client):
    """POST .../remove returns 404 when the listing is not in the organization."""
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )

    async def fake_remove(_self, *, listing_id, removal_note, project_id=None):
        del _self, listing_id, removal_note, project_id
        raise NotFoundException(
            message_key="marketplace.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing_admin",
        fake_remove,
    )
    response = await client.post(
        f"/v1/marketplace/admin/listings/{LISTING_ID}/remove",
        json={"removal_note": "Taken down"},
    )
    assert_error(response, 404)


@pytest.mark.asyncio
async def test_marketplace_admin_forbidden_without_permission(monkeypatch, client):
    """Staff without marketplace view cannot open the admin list."""

    async def fake_access(**kwargs):
        del kwargs
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.marketplace_admin.ensure_staff_project_access_optional",
        fake_access,
    )
    response = await client.get("/v1/marketplace/admin/listings")
    assert_error(response, 403)
