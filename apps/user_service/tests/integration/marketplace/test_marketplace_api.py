"""HTTP tests for resident and committee buy and sell routes."""

from __future__ import annotations

import pytest

from apps.user_service.app.app_instance import app
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

CONTACT_ID = "contact-1"
UNIT_ID = "unit-1"
LISTING_ID = "listing-1"
REPORT_ID = "report-1"

_HOME = {
    "project_name": "ATS Nobility",
    "tower_count": 6,
    "draft": {"id": LISTING_ID, "title": "Study table", "missing": ["media"]},
    "recent": [],
    "live_count": 2,
}


def _resident(monkeypatch) -> None:
    async def fake_extract(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(), {"id": CONTACT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.api.marketplace.extract_onboarding_contact_context",
        fake_extract,
    )


def _staff(monkeypatch) -> None:
    async def fake_extract(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context()

    async def allow(**kwargs):
        del kwargs

    monkeypatch.setattr(
        "apps.user_service.app.api.marketplace.extract_user_context",
        fake_extract,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.marketplace.ensure_staff_project_access_for_context",
        allow,
    )


@pytest.mark.asyncio
async def test_catalog_success_uses_the_real_catalog(monkeypatch, client):
    """GET /marketplace/catalog returns the eight categories."""
    _resident(monkeypatch)
    response = await client.get("/v1/marketplace/catalog")
    payload = assert_success(response)
    names = [item["name"] for item in payload["data"]["categories"]]
    assert names[0] == "Furniture"
    assert len(names) == 8


@pytest.mark.asyncio
async def test_home_success(monkeypatch, client):
    """GET /marketplace/home returns the society header, draft, and recent posts."""
    _resident(monkeypatch)

    async def fake_home(_self, *, contact_id, unit_id):
        assert contact_id == CONTACT_ID
        assert unit_id == UNIT_ID
        return _HOME

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.home",
        fake_home,
    )
    response = await client.get("/v1/marketplace/home", params={"unit_id": UNIT_ID})
    payload = assert_success(response)
    assert payload["data"]["project_name"] == "ATS Nobility"
    assert payload["data"]["draft"]["missing"] == ["media"]
    assert payload["data"]["live_count"] == 2


@pytest.mark.asyncio
async def test_home_fails_without_unit_id(monkeypatch, client):
    """GET /marketplace/home returns 422 when unit_id is missing."""
    _resident(monkeypatch)
    response = await client.get("/v1/marketplace/home")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_home_fails_when_the_flat_is_not_assigned(monkeypatch, client):
    """GET /marketplace/home returns 422 through the real service when the flat is unknown."""
    _resident(monkeypatch)
    response = await client.get("/v1/marketplace/home", params={"unit_id": UNIT_ID})
    assert_error(response, status_code=422, message_fragment="not assigned")


@pytest.mark.asyncio
async def test_create_listing_success(monkeypatch, client):
    """POST /marketplace/listings starts a draft."""
    _resident(monkeypatch)

    async def fake_create(_self, *, contact_id, body):
        assert contact_id == CONTACT_ID
        assert body.category == "Electronics"
        return {"id": LISTING_ID, "status": "draft", "category": "Electronics"}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.create_listing",
        fake_create,
    )
    response = await client.post(
        "/v1/marketplace/listings",
        json={"unit_id": UNIT_ID, "category": "Electronics"},
    )
    payload = assert_success(response, status_code=201)
    assert payload["data"]["status"] == "draft"


@pytest.mark.asyncio
async def test_create_listing_fails_without_a_category(monkeypatch, client):
    """POST /marketplace/listings returns 422 when category is missing."""
    _resident(monkeypatch)
    response = await client.post("/v1/marketplace/listings", json={"unit_id": UNIT_ID})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_listing_fails_for_an_unknown_category(monkeypatch, client):
    """POST /marketplace/listings returns 422 when the category is not in the catalog."""
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

    async def fake_get(_self, *, contact_id, listing_id, unit_id):
        del _self, contact_id, listing_id, unit_id
        raise NotFoundException(
            message_key="marketplace.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_listing",
        fake_get,
    )
    response = await client.get(
        f"/v1/marketplace/listings/{LISTING_ID}",
        params={"unit_id": UNIT_ID},
    )
    assert_error(response, status_code=404, message_fragment="could not be found")


@pytest.mark.asyncio
async def test_publish_action_success(monkeypatch, client):
    """POST /listings/{id}/actions with publish returns the live window."""
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
        f"/v1/marketplace/listings/{LISTING_ID}/actions",
        json={"unit_id": UNIT_ID, "action": "publish", "rules_accepted": True},
    )
    payload = assert_success(response)
    assert payload["message"] == "Listing is live."
    assert payload["data"]["id"] == LISTING_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,message",
    [
        ("remove", "Listing moved back to drafts."),
        ("restore", "Listing is live again."),
        ("renew", "Listing renewed for 30 days."),
        ("relist", "Listing is live again."),
    ],
)
async def test_listing_actions_success(monkeypatch, client, action, message):
    """Remove, restore, renew, and relist share the actions route."""
    _resident(monkeypatch)
    called = {}

    async def fake_remove(_self, *, contact_id, listing_id, unit_id):
        called["name"] = "remove"
        assert contact_id == CONTACT_ID and listing_id == LISTING_ID and unit_id == UNIT_ID
        return {"id": LISTING_ID, "status": "draft"}

    async def fake_restore(_self, *, contact_id, listing_id, unit_id):
        del contact_id, listing_id, unit_id
        called["name"] = "restore"
        return {"id": LISTING_ID, "status": "live"}

    async def fake_renew(_self, *, contact_id, listing_id, unit_id):
        del contact_id, listing_id, unit_id
        called["name"] = "renew"
        return {"id": LISTING_ID}

    async def fake_relist(_self, *, contact_id, listing_id, unit_id):
        del contact_id, listing_id, unit_id
        called["name"] = "relist"
        return {"id": LISTING_ID}

    service = "apps.user_service.app.services.marketplace_service.MarketplaceService"
    monkeypatch.setattr(f"{service}.remove_listing", fake_remove)
    monkeypatch.setattr(f"{service}.restore_listing", fake_restore)
    monkeypatch.setattr(f"{service}.renew", fake_renew)
    monkeypatch.setattr(f"{service}.relist", fake_relist)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/actions",
        json={"unit_id": UNIT_ID, "action": action},
    )
    payload = assert_success(response)
    assert payload["message"] == message
    assert called["name"] == action


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
        f"/v1/marketplace/listings/{LISTING_ID}/actions",
        json={"unit_id": UNIT_ID, "action": "publish"},
    )
    assert_error(response, status_code=422, message_fragment="rules")


@pytest.mark.asyncio
async def test_action_fails_for_an_unknown_action(monkeypatch, client):
    """POST /listings/{id}/actions returns 422 for an unknown action."""
    _resident(monkeypatch)
    response = await client.post(
        f"/v1/marketplace/listings/{LISTING_ID}/actions",
        json={"unit_id": UNIT_ID, "action": "boost"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_save_and_unsave(monkeypatch, client):
    """PUT /listings/{id}/save bookmarks or clears the bookmark."""
    _resident(monkeypatch)
    seen = []

    async def fake_save(_self, *, contact_id, listing_id, unit_id):
        seen.append(("save", contact_id, listing_id, unit_id))

    async def fake_unsave(_self, *, contact_id, listing_id, unit_id):
        seen.append(("unsave", contact_id, listing_id, unit_id))

    service = "apps.user_service.app.services.marketplace_service.MarketplaceService"
    monkeypatch.setattr(f"{service}.save", fake_save)
    monkeypatch.setattr(f"{service}.unsave", fake_unsave)

    saved = await client.put(
        f"/v1/marketplace/listings/{LISTING_ID}/save",
        json={"unit_id": UNIT_ID, "saved": True},
    )
    assert_success(saved)
    assert saved.json()["message"] == "Listing saved."

    cleared = await client.put(
        f"/v1/marketplace/listings/{LISTING_ID}/save",
        json={"unit_id": UNIT_ID, "saved": False},
    )
    assert_success(cleared)
    assert cleared.json()["message"] == "Listing removed from saved."
    assert seen[0][0] == "save"
    assert seen[1][0] == "unsave"


@pytest.mark.asyncio
async def test_save_fails_without_the_flag(monkeypatch, client):
    """PUT /listings/{id}/save returns 422 when saved is missing."""
    _resident(monkeypatch)
    response = await client.put(
        f"/v1/marketplace/listings/{LISTING_ID}/save",
        json={"unit_id": UNIT_ID},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_browse_listings_success(monkeypatch, client):
    """GET /marketplace/listings returns a page of cards."""
    _resident(monkeypatch)

    async def fake_list(_self, **kwargs):
        assert kwargs["unit_id"] == UNIT_ID
        assert kwargs["query"] == "table"
        return {"items": [{"id": LISTING_ID, "title": "Study table"}], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.list_listings",
        fake_list,
    )
    response = await client.get(
        "/v1/marketplace/listings",
        params={"unit_id": UNIT_ID, "q": "table"},
    )
    payload = assert_success(response)
    assert payload["total"] == 1
    assert payload["data"][0]["title"] == "Study table"


@pytest.mark.asyncio
async def test_review_uphold_and_dismiss(monkeypatch, client):
    """POST /reports/{id}/review upholds with a note and dismisses without one."""
    _staff(monkeypatch)

    async def fake_get(_self, *, report_id):
        assert report_id == REPORT_ID
        return {
            "id": REPORT_ID,
            "project_id": "project-1",
            "status": "open",
            "listing_id": LISTING_ID,
        }

    async def fake_uphold(_self, *, report_id, removal_note, reviewer_user_id):
        assert report_id == REPORT_ID
        assert removal_note == "Item was already sold"
        assert reviewer_user_id == "test-user-id"
        return {"id": REPORT_ID, "status": "upheld"}

    async def fake_dismiss(_self, *, report_id, reviewer_user_id):
        assert report_id == REPORT_ID
        assert reviewer_user_id == "test-user-id"
        return {"id": REPORT_ID, "status": "dismissed"}

    service = "apps.user_service.app.services.marketplace_service.MarketplaceService"
    monkeypatch.setattr(f"{service}.get_report", fake_get)
    monkeypatch.setattr(f"{service}.uphold_report", fake_uphold)
    monkeypatch.setattr(f"{service}.dismiss_report", fake_dismiss)

    upheld = await client.post(
        f"/v1/marketplace/reports/{REPORT_ID}/review",
        json={"decision": "uphold", "removal_note": "Item was already sold"},
    )
    assert_success(upheld)
    assert upheld.json()["data"]["status"] == "upheld"

    dismissed = await client.post(
        f"/v1/marketplace/reports/{REPORT_ID}/review",
        json={"decision": "dismiss"},
    )
    assert_success(dismissed)
    assert dismissed.json()["data"]["status"] == "dismissed"


@pytest.mark.asyncio
async def test_review_uphold_fails_without_a_note(monkeypatch, client):
    """Uphold without a removal note returns 422 and does not take the listing down."""
    _staff(monkeypatch)

    async def fake_get(_self, *, report_id):
        del report_id
        return {"id": REPORT_ID, "project_id": "project-1"}

    async def fake_uphold(_self, **kwargs):
        del _self, kwargs
        raise AssertionError("uphold must not run without a note")

    service = "apps.user_service.app.services.marketplace_service.MarketplaceService"
    monkeypatch.setattr(f"{service}.get_report", fake_get)
    monkeypatch.setattr(f"{service}.uphold_report", fake_uphold)
    response = await client.post(
        f"/v1/marketplace/reports/{REPORT_ID}/review",
        json={"decision": "uphold", "removal_note": "   "},
    )
    assert_error(response, status_code=422, message_fragment="note")


@pytest.mark.asyncio
async def test_review_fails_without_moderate_access(monkeypatch, client):
    """POST /reports/{id}/review returns 403 when the staff member cannot moderate."""
    _staff(monkeypatch)

    async def deny(**kwargs):
        del kwargs
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

    async def fake_get(_self, *, report_id):
        del report_id
        return {"id": REPORT_ID, "project_id": "project-1"}

    monkeypatch.setattr(
        "apps.user_service.app.api.marketplace.ensure_staff_project_access_for_context",
        deny,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.get_report",
        fake_get,
    )
    response = await client.post(
        f"/v1/marketplace/reports/{REPORT_ID}/review",
        json={"decision": "dismiss"},
    )
    assert_error(response, status_code=403)


@pytest.mark.asyncio
async def test_feedback_patterns_fails_without_a_society(monkeypatch, client):
    """GET /feedback-patterns returns 403 when the staff member has no society to view."""
    _staff(monkeypatch)
    response = await client.get("/v1/marketplace/feedback-patterns")
    assert_error(response, status_code=403)


@pytest.mark.asyncio
async def test_feedback_patterns_success(monkeypatch, client):
    """GET /feedback-patterns returns sellers with repeated trouble."""
    _staff(monkeypatch)

    async def _conn():
        class _Rows:
            async def fetch(self, *_args, **_kwargs):
                return [{"project_id": "project-1"}]

            async def fetchval(self, *_args, **_kwargs):
                return None

        yield _Rows()

    app.dependency_overrides[db_conn] = _conn

    async def fake_patterns(_self):
        return [{"seller_contact_id": CONTACT_ID, "had_trouble_count": 3}]

    monkeypatch.setattr(
        "apps.user_service.app.services.marketplace_service.MarketplaceService.feedback_patterns",
        fake_patterns,
    )
    response = await client.get("/v1/marketplace/feedback-patterns")
    payload = assert_success(response)
    assert payload["data"][0]["had_trouble_count"] == 3
