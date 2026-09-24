"""Integration tests for companies endpoints."""

from unittest.mock import AsyncMock

import pytest

from apps.user_service.tests.integration.helpers import (
    admin_context,
    patch_companies_project_access_denied,
    patch_ensure_companies_or_resident_project_access,
)
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_COMPANIES_API = "apps.user_service.app.api.companies"

COMPANY_ID = "company-1"
PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"


def _companies_base() -> str:
    return f"/v1/projects/{PROJECT_ID}/companies"


_FAKE_COMPANY_SUMMARY = {
    "id": COMPANY_ID,
    "organization_id": "org-123",
    "status": "active",
    "name": "Acme Corp",
    "industry": "Technology",
    "profile_photo_url": None,
    "email": "info@acme.example",
    "phones": [],
    "contacts": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_COMPANY_DETAILS = {
    **_FAKE_COMPANY_SUMMARY,
    "portal_access": False,
    "primary_contact_id": None,
    "tags": [],
    "websites": [],
    "billing_preferences": {},
    "social_pages": [],
    "linked_pages": [],
    "products": [],
    "key_people": [],
    "custom_fields": [],
    "additional_data": {},
    "notes": [],
    "addresses": [],
    "target_market_segments": [],
    "current_tech_stack": [],
    "preferred_communication_channels": [],
    "industry_specific_terminologies": [],
    "description": None,
}


@pytest.mark.asyncio
async def test_create_company(monkeypatch, client):
    """POST /companies creates a company."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_create_company(_self, body):
        del _self
        assert body.name == "Acme Corp"
        return {
            "company_id": COMPANY_ID,
            "old_data": None,
            "new_data": {"name": "Acme Corp"},
            "created_entities": [],
            "enrichment_targets": [],
        }

    async def fake_create_lifecycle_events(**_kwargs):
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.create_company",
        fake_create_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.companies.CompaniesService.create_company",
        fake_create_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.companies."
        "CompaniesService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_typesense_indexing_for_created_entities",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.schedule_enrichment",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.audit_logger.log_audit_event",
        AsyncMock(return_value=None),
    )

    res = await client.post(
        f"{_companies_base()}",
        json={"name": "Acme Corp"},
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_list_companies(monkeypatch, client):
    """POST /companies/list returns paginated companies."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_list_companies(
        _self,
        *,
        search=None,
        status=None,
        dropdown_filters=None,
        project_id=None,
        page=1,
        page_size=20,
    ):
        del _self, search, status, dropdown_filters, project_id
        assert page == 1
        assert page_size == 20
        return {"items": [_FAKE_COMPANY_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.list_companies",
        fake_list_companies,
    )

    res = await client.post(
        f"{_companies_base()}/list",
        json={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == COMPANY_ID
    assert body["data"][0]["name"] == "Acme Corp"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_companies_empty(monkeypatch, client):
    """POST /companies/list returns empty collection."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_list_companies(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.list_companies",
        fake_list_companies,
    )

    res = await client.post(
        f"{_companies_base()}/list",
        json={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_company_details(monkeypatch, client):
    """GET /companies/{company_id} returns company details."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_get_company_details(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return _FAKE_COMPANY_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.get_company_details",
        fake_get_company_details,
    )

    res = await client.get(f"{_companies_base()}/{COMPANY_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == COMPANY_ID
    assert body["data"]["name"] == "Acme Corp"
    assert body["data"]["status"] == "active"


@pytest.mark.asyncio
async def test_update_company(monkeypatch, client):
    """PATCH /companies/{company_id} updates a company."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_update_company(_self, *, company_id: str, body):
        del _self
        assert company_id == COMPANY_ID
        assert body.name == "Updated Corp"
        return {
            "old_data": _FAKE_COMPANY_DETAILS,
            "new_data": {**_FAKE_COMPANY_DETAILS, "name": "Updated Corp"},
            "contacts_delta": {},
        }

    async def fake_create_lifecycle_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": COMPANY_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.update_company",
        fake_update_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_lifecycle_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_company_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"{_companies_base()}/{COMPANY_ID}",
        json={"name": "Updated Corp"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_delete_company(monkeypatch, client):
    """DELETE /companies/{company_id} soft-deletes a company."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_soft_delete(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return {
            "old_data": _FAKE_COMPANY_DETAILS,
            "new_data": {**_FAKE_COMPANY_DETAILS, "status": "deleted"},
        }

    async def fake_create_lifecycle_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-2", "aggregate_id": COMPANY_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.soft_delete_company",
        fake_soft_delete,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_lifecycle_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.typesense_index_service.delete_company_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.delete(f"{_companies_base()}/{COMPANY_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_company_with_lead(monkeypatch, client):
    """POST /companies publishes lead lifecycle event when a lead is created."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_create_company(_self, body):
        del _self, body
        return {
            "company_id": COMPANY_ID,
            "old_data": None,
            "new_data": {"name": "Acme Corp"},
            "created_entities": [],
            "enrichment_targets": [],
            "created_lead_id": "lead-1",
        }

    async def fake_create_lifecycle_events(**_kwargs):
        return []

    async def fake_lead_event(_self, **_kwargs):
        del _self
        return {"event_type": "leads.created", "aggregate_id": "lead-1"}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.create_company",
        fake_create_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService."
        "create_lead_created_lifecycle_event",
        fake_lead_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_typesense_indexing_for_created_entities",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.schedule_enrichment",
        lambda **_kwargs: None,
    )

    res = await client.post(f"{_companies_base()}", json={"name": "Acme Corp"})
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_get_company_activity(monkeypatch, client):
    """GET /companies/activity/{id}/ returns audit activity."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_get_company_details(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return _FAKE_COMPANY_DETAILS

    async def fake_get_activity(_self, *, company_id: str, limit: int, offset: int):
        del _self, company_id, limit, offset
        return ([{"action": "UPDATE"}], 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.get_company_details",
        fake_get_company_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_company_activity",
        fake_get_activity,
    )

    res = await client.get(f"{_companies_base()}/activity/{COMPANY_ID}/")
    body = assert_success(res, 200)
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_company_activity_empty_with_total(monkeypatch, client):
    """GET /companies/activity/{id}/ handles empty page with non-zero total."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_get_company_details(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return _FAKE_COMPANY_DETAILS

    async def fake_get_activity(_self, *, company_id: str, limit: int, offset: int):
        del _self, company_id, limit, offset
        return ([], 5)

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.get_company_details",
        fake_get_company_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_company_activity",
        fake_get_activity,
    )

    res = await client.get(
        f"{_companies_base()}/activity/{COMPANY_ID}/",
        params={"page": 2, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 5


@pytest.mark.asyncio
async def test_get_company_activity_empty_no_data(monkeypatch, client):
    """GET /companies/activity/{id}/ returns no_data when total is zero."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_get_company_details(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return _FAKE_COMPANY_DETAILS

    async def fake_get_activity(_self, *, company_id: str, limit: int, offset: int):
        del _self, company_id, limit, offset
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.get_company_details",
        fake_get_company_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_company_activity",
        fake_get_activity,
    )

    res = await client.get(f"{_companies_base()}/activity/{COMPANY_ID}/")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_search_companies(monkeypatch, client):
    """GET /companies/search returns Typesense hits."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_search_companies(_self, **kwargs):
        del _self, kwargs
        return {"items": [_FAKE_COMPANY_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.search_companies",
        fake_search_companies,
    )

    res = await client.get(f"{_companies_base()}/search", params={"query": "acme"})
    body = assert_success(res, 200)
    assert body["total"] == 1
    assert body["data"][0]["id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_search_companies_empty(monkeypatch, client):
    """GET /companies/search returns empty collection."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_search_companies(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.search_companies",
        fake_search_companies,
    )

    res = await client.get(f"{_companies_base()}/search", params={"query": "missing"})
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_enrich_company(monkeypatch, client):
    """POST /companies/{id}/enrich triggers enrichment."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.companies.require_client_enrichment_enabled",
        lambda: None,
    )

    async def fake_get_company_details(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return {
            **_FAKE_COMPANY_DETAILS,
            "addresses": [{"country": "US"}],
            "profile_photo_url": "https://example.com/logo.png",
        }

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_type": "companies.enrichment_requested"}

    enrichment_service = type(
        "S",
        (),
        {"run_client_enrichment": staticmethod(lambda **_k: None)},
    )()

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.get_company_details",
        fake_get_company_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service."
        "ClientEnrichmentService.from_settings",
        lambda: enrichment_service,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.post(f"{_companies_base()}/{COMPANY_ID}/enrich")
    assert_success(res, 202)


@pytest.mark.asyncio
async def test_update_company_with_contact_association(monkeypatch, client):
    """PATCH /companies/{id} emits contact association lifecycle events."""

    patch_ensure_companies_or_resident_project_access(
        monkeypatch, "apps.user_service.app.api.companies"
    )

    async def fake_update_company(_self, *, company_id: str, body):
        del _self, body
        assert company_id == COMPANY_ID
        return {
            "old_data": _FAKE_COMPANY_DETAILS,
            "new_data": {**_FAKE_COMPANY_DETAILS, "name": "Updated Corp"},
            "contacts_delta": {
                "affected_contact_ids": ["contact-1", "contact-2"],
                "created_contact_id": "contact-2",
            },
        }

    async def fake_create_lifecycle_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": COMPANY_ID}

    async def fake_create_lifecycle_events(_self, *, items, topics):
        del _self, topics
        return [
            {"event_id": f"evt-{idx}", "aggregate_id": item["aggregate_id"]}
            for idx, item in enumerate(items)
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.update_company",
        fake_update_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_lifecycle_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_events",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_company_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"{_companies_base()}/{COMPANY_ID}",
        json={"name": "Updated Corp"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_list_companies_forwards_project_id(monkeypatch, client):
    """POST list passes path project_id into the service layer."""

    patch_ensure_companies_or_resident_project_access(monkeypatch, _COMPANIES_API)
    captured: dict = {}

    async def fake_list_companies(_self, **kwargs):
        del _self
        captured.update(kwargs)
        return {"items": [_FAKE_COMPANY_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.list_companies",
        fake_list_companies,
    )

    res = await client.post(
        f"{_companies_base()}/list",
        json={"page": 1, "page_size": 20},
    )
    assert_success(res, 200)
    assert captured.get("project_id") == PROJECT_ID


@pytest.mark.asyncio
async def test_get_company_details_rejects_forbidden_project_access(monkeypatch, client):
    """GET details returns 403 when project role lacks permission."""

    patch_companies_project_access_denied(monkeypatch, _COMPANIES_API)

    res = await client.get(f"{_companies_base()}/{COMPANY_ID}")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_create_company_rejects_forbidden_project_access(monkeypatch, client):
    """POST create returns 403 when project access is denied."""

    patch_companies_project_access_denied(monkeypatch, _COMPANIES_API)

    res = await client.post(f"{_companies_base()}", json={"name": "Acme Corp"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_company_details_rejects_company_not_linked_to_project(monkeypatch, client):
    """GET details returns 404 when the company is not on the project."""

    async def fake_access(**kwargs):
        del kwargs
        return admin_context()

    async def fake_not_in_project(self, **kwargs):
        del self, kwargs
        raise NotFoundException(
            message_key="companies.errors.company_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        f"{_COMPANIES_API}.ensure_companies_or_resident_project_access",
        fake_access,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.ensure_company_in_project",
        fake_not_in_project,
    )

    res = await client.get(f"{_companies_base()}/{COMPANY_ID}")
    assert_error(res, 404)


@pytest.mark.asyncio
async def test_update_company_rejects_company_not_linked_to_project(monkeypatch, client):
    """PATCH returns 404 when the company is not linked to the project."""

    async def fake_access(**kwargs):
        del kwargs
        return admin_context()

    async def fake_not_in_project(self, **kwargs):
        del self, kwargs
        raise NotFoundException(
            message_key="companies.errors.company_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        f"{_COMPANIES_API}.ensure_companies_or_resident_project_access",
        fake_access,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.ensure_company_in_project",
        fake_not_in_project,
    )

    res = await client.patch(
        f"{_companies_base()}/{COMPANY_ID}",
        json={"name": "Updated Corp"},
    )
    assert_error(res, 404)


@pytest.mark.asyncio
async def test_create_company_rejects_without_project_contact_link(monkeypatch, client):
    """POST create returns 422 when project-scoped create validation fails."""

    async def fake_access(**kwargs):
        del kwargs
        return admin_context()

    async def reject_validate(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="companies.errors.project_scoped_create_requires_contact",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        f"{_COMPANIES_API}.ensure_companies_or_resident_project_access",
        fake_access,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.validate_project_scoped_create",
        reject_validate,
    )

    res = await client.post(f"{_companies_base()}", json={"name": "Acme Corp"})
    assert_error(res, 422)


@pytest.mark.asyncio
async def test_create_company_rejects_missing_name(monkeypatch, client):
    """POST create returns 422 when required name is omitted."""

    patch_ensure_companies_or_resident_project_access(monkeypatch, _COMPANIES_API)

    res = await client.post(f"{_companies_base()}", json={})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_search_companies_rejects_short_query(monkeypatch, client):
    """GET search returns 422 when query is shorter than 2 characters."""

    patch_ensure_companies_or_resident_project_access(monkeypatch, _COMPANIES_API)

    res = await client.get(f"{_companies_base()}/search", params={"query": "a"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_legacy_companies_route_not_found(client):
    """Unscoped /v1/companies routes are no longer registered."""

    res = await client.get(f"/v1/companies/{COMPANY_ID}")
    assert res.status_code == 404
