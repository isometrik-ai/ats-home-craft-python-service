"""HTTP tests for resident buy and sell routes."""

from __future__ import annotations

import pytest

from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
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

    async def fake_publish(_self, *, contact_id, listing_id, body):
        assert contact_id == CONTACT_ID
        assert listing_id == LISTING_ID
        assert body.rules_accepted is True
        return {
            "id": LISTING_ID,
            "published_at": "2026-10-07T00:00:00Z",
            "expires_at": "2026-11-06T00:00:00Z",
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.publish",
        fake_publish,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/publish",
        json={"unit_id": UNIT_ID, "rules_accepted": True},
    )
    payload = assert_success(response)
    assert payload["message"] == "Listing is live."
    assert payload["data"]["id"] == LISTING_ID


@pytest.mark.asyncio
async def test_publish_fails_when_rules_are_not_accepted(monkeypatch, client):
    """Publish without rules_accepted returns 422 from the service."""
    _resident(monkeypatch)

    async def fake_publish(_self, *, contact_id, listing_id, body):
        del _self, contact_id, listing_id
        assert body.rules_accepted is False
        raise ValidationException(
            message_key="marketplace.errors.rules_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.publish",
        fake_publish,
    )
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/publish",
        json={"unit_id": UNIT_ID, "rules_accepted": False},
    )
    assert_error(response, status_code=422, message_fragment="rules")


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
        assert kwargs["query"] == "table"
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
