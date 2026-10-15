"""HTTP tests for resident buy and sell routes."""

from __future__ import annotations

import pytest

from apps.user_service.tests.integration.helpers import (
    admin_context,
    patch_ensure_staff_project_access_optional,
)
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

CONTACT_ID = "contact-1"
UNIT_ID = "unit-1"
LISTING_ID = "listing-1"


def _resident(monkeypatch) -> None:
    async def fake_extract(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(), {"id": CONTACT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.api.marketplace.extract_onboarding_contact_context",
        fake_extract,
    )


@pytest.mark.asyncio
async def test_catalog_success_uses_the_real_catalog(monkeypatch, client):
    """GET /marketplace/catalog returns the seven categories."""
    _resident(monkeypatch)
    response = await client.get("/v1/marketplace/catalog")
    payload = assert_success(response)
    names = [item["name"] for item in payload["data"]["categories"]]
    assert names[0] == "Furniture"
    assert len(names) == 7
    assert "Services" not in names


@pytest.mark.asyncio
async def test_report_reason_catalog_success(monkeypatch, client):
    """GET /marketplace/report-reasons returns the four report options."""
    _resident(monkeypatch)
    response = await client.get("/v1/marketplace/report-reasons")
    payload = assert_success(response)
    data = payload["data"]
    assert data["sheet_title"] == "Report this listing"
    slugs = [item["slug"] for item in data["reasons"]]
    assert slugs == [
        "not_allowed",
        "business_or_broker",
        "sold_but_listed",
        "something_else",
    ]
    assert payload["data"]["reasons"][1]["name"] == "Looks like a business or broker"


@pytest.mark.asyncio
async def test_report_listing_success_returns_display_reason(monkeypatch, client):
    """POST /listings/{id}/report returns 201 and reason as label, not slug."""
    _resident(monkeypatch)

    async def fake_report(_self, *, contact_id, listing_id, body):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        assert body.unit_id == UNIT_ID
        assert body.reason.value == "business_or_broker"
        return {
            "id": "323d45d8-ba1b-4bf9-988c-ede6043fe566",
            "listing_id": LISTING_ID,
            "reason": "Looks like a business or broker",
            "listing_report_count": 1,
            "listing_hidden": False,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.report_listing",
        fake_report,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID, "reason": "business_or_broker"},
    )
    payload = assert_success(response, status_code=201)
    assert payload["message"] == "Report sent to the committee."
    assert payload["statusCode"] == 201
    data = payload["data"]
    assert data["reason"] == "Looks like a business or broker"
    assert "business_or_broker" not in data["reason"]
    assert data["listing_report_count"] == 1
    assert data["listing_hidden"] is False
    assert data["listing_id"] == LISTING_ID


@pytest.mark.asyncio
async def test_report_listing_success_flags_hidden_at_three_reports(monkeypatch, client):
    """POST /report sets listing_hidden when the listing hits three reports."""
    _resident(monkeypatch)

    async def fake_report(_self, *, contact_id, listing_id, body):
        del contact_id, listing_id, body
        return {
            "id": "report-3",
            "listing_id": LISTING_ID,
            "reason": "Not allowed in the society",
            "listing_report_count": 3,
            "listing_hidden": True,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.report_listing",
        fake_report,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID, "reason": "not_allowed"},
    )
    payload = assert_success(response, status_code=201)
    assert payload["data"]["listing_report_count"] == 3
    assert payload["data"]["listing_hidden"] is True


@pytest.mark.asyncio
async def test_report_listing_conflict_when_already_reported(monkeypatch, client):
    """POST /report returns 409 when this resident already has an open report."""
    _resident(monkeypatch)

    async def fake_report(_self, *, contact_id, listing_id, body):
        del contact_id, listing_id, body
        raise ConflictException(
            message_key="marketplace.errors.report_exists",
            custom_code=CustomStatusCode.CONFLICT,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.report_listing",
        fake_report,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID, "reason": "sold_but_listed"},
    )
    assert_error(response, 409, message_fragment="already reported")


@pytest.mark.asyncio
async def test_report_listing_fails_for_own_listing(monkeypatch, client):
    """POST /report returns 422 when the seller tries to report themselves."""
    _resident(monkeypatch)

    async def fake_report(_self, *, contact_id, listing_id, body):
        del contact_id, listing_id, body
        raise ValidationException(
            message_key="marketplace.errors.cannot_report_own",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.report_listing",
        fake_report,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID, "reason": "something_else"},
    )
    assert_error(response, 422, message_fragment="own listing")


@pytest.mark.asyncio
async def test_report_listing_not_found_when_listing_missing(monkeypatch, client):
    """POST /report returns 404 when the listing is not live or does not exist."""
    _resident(monkeypatch)

    async def fake_report(_self, *, contact_id, listing_id, body):
        del contact_id, listing_id, body
        raise NotFoundException(
            message_key="marketplace.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.report_listing",
        fake_report,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID, "reason": "not_allowed"},
    )
    assert_error(response, 404)


@pytest.mark.asyncio
async def test_report_listing_rejects_invalid_reason_slug(monkeypatch, client):
    """POST /report returns 422 when reason is not one of the catalog slugs."""
    _resident(monkeypatch)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID, "reason": "spam"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_report_listing_requires_unit_id_and_reason(monkeypatch, client):
    """POST /report returns 422 when the body is incomplete."""
    _resident(monkeypatch)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/report",
        json={"unit_id": UNIT_ID},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_listing_success(monkeypatch, client):
    """POST /marketplace/listings creates an unpublished listing."""
    _resident(monkeypatch)

    async def fake_create(_self, *, contact_id, body):
        assert contact_id == CONTACT_ID
        assert body.category == "electronics"
        assert body.subtype == "mobiles_tablets"
        return {"id": LISTING_ID, "status": "draft", "category": "electronics"}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.create_listing",
        fake_create,
    )
    response = await client.post(
        "/v1/marketplace/listings",
        json={"unit_id": UNIT_ID, "category": "electronics", "subtype": "mobiles_tablets"},
    )
    payload = assert_success(response, status_code=201)
    assert payload["data"]["status"] == "draft"


@pytest.mark.asyncio
async def test_create_listing_fails_without_a_category(monkeypatch, client):
    """POST /listings returns 422 when category is missing."""
    _resident(monkeypatch)
    response = await client.post("/v1/marketplace/listings", json={"unit_id": UNIT_ID})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_listing_fails_for_an_unknown_category(monkeypatch, client):
    """POST /listings returns 422 when the category is not in the catalog."""
    _resident(monkeypatch)

    async def fake_create(_self, *, contact_id, body):
        del _self, contact_id, body
        raise ValidationException(
            message_key="marketplace.errors.invalid_category",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.create_listing",
        fake_create,
    )
    response = await client.post(
        "/v1/marketplace/listings",
        json={"unit_id": UNIT_ID, "category": "Spaceships"},
    )
    assert_error(response, status_code=422, message_fragment="category")


@pytest.mark.asyncio
async def test_listing_detail_not_found(monkeypatch, client):
    """GET /marketplace/listings/{id} returns 404 when the listing is missing."""
    _resident(monkeypatch)

    async def fake_get(_self, *, contact_id, listing_id):
        del _self, contact_id, listing_id
        raise NotFoundException(
            message_key="marketplace.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing",
        fake_get,
    )
    response = await client.get(f"/v1/marketplace/listings/{LISTING_ID}")
    assert_error(response, status_code=404, message_fragment="could not be found")


@pytest.mark.asyncio
async def test_publish_success(monkeypatch, client):
    """POST /listings/{id}/publish goes live from preview."""
    _resident(monkeypatch)

    async def fake_publish(_self, *, contact_id, listing_id):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        return {
            "id": LISTING_ID,
            "published_at": "2026-10-07T00:00:00Z",
            "expires_at": "2026-11-06T00:00:00Z",
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.publish",
        fake_publish,
    )
    response = await client.post(f"/v1/marketplace/listings/{LISTING_ID}/publish")
    payload = assert_success(response)
    assert payload["message"] == "Listing is live."
    assert payload["data"]["id"] == LISTING_ID


@pytest.mark.asyncio
async def test_save_and_unsave(monkeypatch, client):
    """POST /listings/{id}/save bookmarks or clears the bookmark."""
    _resident(monkeypatch)
    seen = []

    async def fake_save(_self, *, contact_id, listing_id, unit_id):
        seen.append(("save", contact_id, listing_id, unit_id))

    async def fake_unsave(_self, *, contact_id, listing_id, unit_id):
        seen.append(("unsave", contact_id, listing_id, unit_id))

    service = "apps.user_service.app.services.marketplace_service.MarketplaceService"
    monkeypatch.setattr(f"{service}.save", fake_save)
    monkeypatch.setattr(f"{service}.unsave", fake_unsave)

    saved = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/save",
        json={"unit_id": UNIT_ID, "saved": True},
    )
    assert_success(saved)
    assert saved.json()["message"] == "Listing saved."

    cleared = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/save",
        json={"unit_id": UNIT_ID, "saved": False},
    )
    assert_success(cleared)
    assert cleared.json()["message"] == "Listing removed from saved."
    assert seen[0][0] == "save"
    assert seen[1][0] == "unsave"


@pytest.mark.asyncio
async def test_save_fails_without_the_flag(monkeypatch, client):
    """POST /listings/{id}/save returns 422 when saved is missing."""
    _resident(monkeypatch)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/save",
        json={},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_browse_listings_success(monkeypatch, client):
    """GET /marketplace/listings returns a page of cards."""
    _resident(monkeypatch)

    async def fake_list(_self, **kwargs):
        assert kwargs["query"].q == "table"
        assert "unit_id" not in kwargs
        return {"items": [{"id": LISTING_ID, "title": "Study table"}], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings",
        fake_list,
    )
    response = await client.get("/v1/marketplace/listings", params={"q": "table"})
    payload = assert_success(response)
    assert payload["total"] == 1
    assert payload["data"][0]["title"] == "Study table"


@pytest.mark.asyncio
async def test_browse_listings_fails_for_unknown_category(monkeypatch, client):
    """GET /listings returns 422 when the category is not in the catalog."""
    _resident(monkeypatch)

    async def fake_list(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="marketplace.errors.invalid_category",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings",
        fake_list,
    )
    response = await client.get(
        "/v1/marketplace/listings",
        params={"category": "not-a-catalog-slug"},
    )
    assert_error(response, 422)


@pytest.mark.asyncio
async def test_listing_detail_success(monkeypatch, client):
    """GET /marketplace/listings/{id} returns the listing drawer."""
    _resident(monkeypatch)

    async def fake_get(_self, *, contact_id, listing_id):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        return {"id": LISTING_ID, "title": "Study table", "status": "live"}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing",
        fake_get,
    )
    response = await client.get(f"/v1/marketplace/listings/{LISTING_ID}")
    payload = assert_success(response)
    assert payload["data"]["id"] == LISTING_ID
    assert payload["message"] == "Listing retrieved successfully."


@pytest.mark.asyncio
async def test_list_saved_listings_success(monkeypatch, client):
    """GET /marketplace/saved returns bookmarked live cards."""
    _resident(monkeypatch)

    async def fake_saved(_self, *, contact_id, page, page_size):
        assert contact_id == CONTACT_ID
        assert page == 1
        assert page_size == 20
        return {"items": [{"id": LISTING_ID, "saved": True}], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_saved",
        fake_saved,
    )
    response = await client.get("/v1/marketplace/saved")
    payload = assert_success(response)
    assert payload["total"] == 1
    assert payload["data"][0]["id"] == LISTING_ID


@pytest.mark.asyncio
async def test_my_listings_success(monkeypatch, client):
    """GET /marketplace/me/listings returns the seller dashboard."""
    _resident(monkeypatch)

    async def fake_mine(_self, *, contact_id, status, page, page_size):
        assert contact_id == CONTACT_ID
        assert status == "live"
        assert page == 1
        assert page_size == 20
        return {"items": [{"id": LISTING_ID, "status": "live"}], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.my_listings",
        fake_mine,
    )
    response = await client.get("/v1/marketplace/me/listings", params={"status": "live"})
    payload = assert_success(response)
    assert payload["data"][0]["status"] == "live"


@pytest.mark.asyncio
async def test_my_listings_fails_for_committee_status(monkeypatch, client):
    """GET /me/listings rejects a status that is not a seller tab."""
    _resident(monkeypatch)
    response = await client.get(
        "/v1/marketplace/me/listings",
        params={"status": "removed_by_committee"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_listing_success(monkeypatch, client):
    """PATCH /listings/{id} updates title while the listing stays live."""
    _resident(monkeypatch)

    async def fake_update(_self, *, contact_id, listing_id, body):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        assert body.unit_id == UNIT_ID
        assert body.title == "Study table"
        return {"id": LISTING_ID, "title": "Study table", "status": "live"}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.update_listing",
        fake_update,
    )
    response = await client.patch(
        f"/v1/marketplace/listings/{LISTING_ID}",
        json={"unit_id": UNIT_ID, "title": "Study table"},
    )
    payload = assert_success(response)
    assert payload["data"]["title"] == "Study table"


@pytest.mark.asyncio
async def test_update_listing_fails_when_status_is_in_the_body(monkeypatch, client):
    """PATCH cannot change status."""
    _resident(monkeypatch)
    response = await client.patch(
        f"/v1/marketplace/listings/{LISTING_ID}",
        json={"unit_id": UNIT_ID, "status": "sold"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remove_listing_success(monkeypatch, client):
    """POST /listings/{id}/remove takes a live listing down."""
    _resident(monkeypatch)

    async def fake_remove(_self, *, contact_id, listing_id, unit_id, removal_note):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        assert unit_id == UNIT_ID
        assert removal_note == "No longer selling"
        return {"id": LISTING_ID, "status": "removed", "removal_note": removal_note}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing",
        fake_remove,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/remove",
        json={"unit_id": UNIT_ID, "removal_note": "No longer selling"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "removed"


@pytest.mark.asyncio
async def test_remove_listing_fails_without_a_note(monkeypatch, client):
    """POST /remove returns 422 when removal_note is empty."""
    _resident(monkeypatch)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/remove",
        json={"unit_id": UNIT_ID, "removal_note": ""},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remove_listing_fails_when_not_live(monkeypatch, client):
    """POST /remove returns 422 when the listing is not live."""
    _resident(monkeypatch)

    async def fake_remove(_self, *, contact_id, listing_id, unit_id, removal_note):
        del _self, contact_id, listing_id, unit_id, removal_note
        raise ValidationException(
            message_key="marketplace.errors.not_live",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.remove_listing",
        fake_remove,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/remove",
        json={"unit_id": UNIT_ID, "removal_note": "Taken down"},
    )
    assert_error(response, 422)


@pytest.mark.asyncio
async def test_mark_sold_success(monkeypatch, client):
    """POST /mark-sold records the buyer."""
    _resident(monkeypatch)

    async def fake_sold(_self, *, contact_id, listing_id, body):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        assert body.unit_id == UNIT_ID
        assert body.sold_type.value == "inside"
        assert str(body.buyer_contact_id) == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        return {"id": LISTING_ID, "buyer_name": "Asha K.", "sold_type": "inside"}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.mark_sold",
        fake_sold,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/mark-sold",
        json={
            "unit_id": UNIT_ID,
            "sold_type": "inside",
            "buyer_contact_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        },
    )
    payload = assert_success(response)
    assert payload["data"]["buyer_name"] == "Asha K."
    assert payload["message"] == "Listing marked as sold."


@pytest.mark.asyncio
async def test_mark_sold_fails_when_buyer_is_the_seller(monkeypatch, client):
    """POST /mark-sold returns 422 when the seller is chosen as buyer."""
    _resident(monkeypatch)

    async def fake_sold(_self, *, contact_id, listing_id, body):
        del _self, contact_id, listing_id, body
        raise ValidationException(
            message_key="marketplace.errors.buyer_is_seller",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.mark_sold",
        fake_sold,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/mark-sold",
        json={
            "unit_id": UNIT_ID,
            "sold_type": "inside",
            "buyer_contact_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        },
    )
    assert_error(response, 422)


@pytest.mark.asyncio
async def test_mark_sold_outside_success(monkeypatch, client):
    """POST /mark-sold with sold_type outside needs no buyer contact."""
    _resident(monkeypatch)

    async def fake_sold(_self, *, contact_id, listing_id, body):
        assert contact_id == CONTACT_ID
        assert body.sold_type.value == "outside"
        assert body.buyer_contact_id is None
        return {"id": LISTING_ID, "sold_type": "outside", "buyer_name": None}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.mark_sold",
        fake_sold,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/mark-sold",
        json={"unit_id": UNIT_ID, "sold_type": "outside"},
    )
    payload = assert_success(response)
    assert payload["data"]["sold_type"] == "outside"


@pytest.mark.asyncio
async def test_mark_sold_inside_missing_buyer_returns_422(monkeypatch, client):
    """POST /mark-sold returns 422 when inside is chosen without buyer_contact_id."""
    _resident(monkeypatch)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/mark-sold",
        json={"unit_id": UNIT_ID, "sold_type": "inside"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_resident_lifecycle_then_admin_remove(monkeypatch, client):
    """Resident create → publish → browse, then staff take the listing down."""
    _resident(monkeypatch)
    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.marketplace_admin"
    )
    resident = "apps.user_service.app.services.marketplace_service.MarketplaceService"
    admin = "apps.user_service.app.services.marketplace_admin_service.MarketplaceAdminService"

    async def fake_create(_self, *, contact_id, body):
        del _self
        assert contact_id == CONTACT_ID
        assert body.unit_id == UNIT_ID
        return {"id": LISTING_ID, "status": "draft"}

    async def fake_publish(_self, *, contact_id, listing_id):
        del _self
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        return {"id": LISTING_ID, "status": "live"}

    async def fake_list(_self, **kwargs):
        del _self, kwargs
        return {"items": [{"id": LISTING_ID, "status": "live"}], "total": 1}

    async def fake_admin_list(_self, *, query):
        del _self
        assert query.project_id is None
        return {
            "items": [{"id": LISTING_ID, "status": "live", "can_remove": True}],
            "total": 1,
        }

    async def fake_admin_remove(_self, *, listing_id, removal_note, project_id=None):
        del _self, project_id
        assert listing_id == LISTING_ID
        assert removal_note == "Photos didn't match"
        return {"id": LISTING_ID, "status": "removed"}

    monkeypatch.setattr(f"{resident}.create_listing", fake_create)
    monkeypatch.setattr(f"{resident}.publish", fake_publish)
    monkeypatch.setattr(f"{resident}.list_listings", fake_list)
    monkeypatch.setattr(f"{admin}.list_listings_admin", fake_admin_list)
    monkeypatch.setattr(f"{admin}.remove_listing_admin", fake_admin_remove)

    created = await client.post(
        "/v1/marketplace/listings",
        json={"unit_id": UNIT_ID, "category": "electronics", "subtype": "mobiles_tablets"},
    )
    assert_success(created, status_code=201)
    assert created.json()["data"]["status"] == "draft"

    published = await client.post(f"/v1/marketplace/listings/{LISTING_ID}/publish")
    assert_success(published)

    browsed = await client.get("/v1/marketplace/listings")
    assert_success(browsed)
    assert browsed.json()["data"][0]["id"] == LISTING_ID

    admin_list = await client.get("/v1/marketplace/admin/listings")
    assert_success(admin_list)
    assert admin_list.json()["data"][0]["can_remove"] is True

    removed = await client.post(
        f"/v1/marketplace/admin/listings/{LISTING_ID}/remove",
        json={"removal_note": "Photos didn't match"},
    )
    payload = assert_success(removed)
    assert payload["data"]["status"] == "removed"
