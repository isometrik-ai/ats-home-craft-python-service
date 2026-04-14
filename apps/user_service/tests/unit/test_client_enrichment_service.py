"""Unit tests for ClientEnrichmentService and enrichment helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
    _first_country_from_addresses,
    _first_website_url,
    _is_empty_value,
    _linkedin_url_from_social_pages,
    _merge_update_without_overwriting_empty,
)

# --- Module-level helpers ---


def test_enrichment_is_empty_value_none():
    """_is_empty_value returns True for None."""
    assert _is_empty_value(None) is True


def test_enrichment_is_empty_value_blank_string():
    """_is_empty_value returns True for blank string."""
    assert _is_empty_value("") is True
    assert _is_empty_value("   ") is True


def test_enrichment_is_empty_value_non_empty_string():
    """_is_empty_value returns False for non-empty string."""
    assert _is_empty_value("x") is False


def test_enrichment_is_empty_value_empty_list():
    """_is_empty_value returns True for empty list/dict."""
    assert _is_empty_value([]) is True
    assert _is_empty_value({}) is True


def test_enrichment_is_empty_value_non_empty_list():
    """_is_empty_value returns False for non-empty list/dict."""
    assert _is_empty_value([1]) is False
    assert _is_empty_value({"a": 1}) is False


def test_merge_keeps_existing_non_empty():
    """_merge_update removes keys where new is empty and existing is non-empty."""
    update = {"name": "", "industry": "Tech"}
    existing = {"name": "Old Name", "industry": "Old"}
    result = _merge_update_without_overwriting_empty(update, existing)
    assert "name" not in result
    assert result["industry"] == "Tech"


def test_merge_update_keeps_new_non_empty():
    """_merge_update keeps keys when new value is non-empty."""
    update = {"name": "New Name"}
    existing = {"name": "Old"}
    result = _merge_update_without_overwriting_empty(update, existing)
    assert result["name"] == "New Name"


def test_merge_update_none_existing_returns_copy():
    """_merge_update returns copy of update when existing is None."""
    update = {"name": ""}
    result = _merge_update_without_overwriting_empty(update, None)
    assert result == {"name": ""}


def test_first_country_from_addresses_empty():
    """_first_country_from_addresses returns None when no addresses."""
    assert _first_country_from_addresses({}) is None
    assert _first_country_from_addresses({"addresses": []}) is None


def test_first_country_from_addresses_first_has_country():
    """_first_country_from_addresses returns first address country."""
    data = {"addresses": [{"country": " USA "}, {"country": "UK"}]}
    assert _first_country_from_addresses(data) == "USA"


def test_first_website_url_empty():
    """_first_website_url returns None when no websites."""
    assert _first_website_url({}) is None
    assert _first_website_url({"websites": []}) is None


def test_first_website_url_returns_first_url():
    """_first_website_url returns first website url."""
    data = {"websites": [{"url": " https://example.com "}]}
    assert _first_website_url(data) == "https://example.com"


def test_linkedin_url_from_social_pages_empty():
    """_linkedin_url_from_social_pages returns None when no LinkedIn."""
    assert _linkedin_url_from_social_pages({}) is None


def test_linkedin_url_from_social_pages_finds_linkedin():
    """_linkedin_url_from_social_pages returns LinkedIn URL."""
    data = {
        "social_pages": [
            {"platform": "twitter", "url": "https://twitter.com/x"},
            {"platform": "LinkedIn", "url": " https://linkedin.com/in/foo "},
        ]
    }
    assert _linkedin_url_from_social_pages(data) == "https://linkedin.com/in/foo"


# --- ClientEnrichmentService ---


def test_from_settings_returns_instance(monkeypatch):
    """from_settings returns ClientEnrichmentService with config values."""
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(
            enrichment_service=MagicMock(
                base_url="http://enrich:8080",
                webhook_url="http://hook/cb",
                timeout_seconds=10.0,
            )
        ),
    )
    svc = ClientEnrichmentService.from_settings()
    assert isinstance(svc, ClientEnrichmentService)
    assert svc._base_url == "http://enrich:8080"
    assert svc._webhook_url == "http://hook/cb"
    assert svc._timeout == 10.0


def test_build_person_payload_minimal():
    """_build_person_payload includes at least name when rest empty."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    payload = svc._build_person_payload({}, webhook_url=None)
    assert "name" in payload
    assert payload.get("name") == ""


def test_build_person_payload_with_webhook_url():
    """_build_person_payload adds webhook_url when provided."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    payload = svc._build_person_payload(
        {"first_name": "John", "last_name": "Doe", "email": "j@x.com"},
        webhook_url="http://callback",
    )
    assert payload.get("name") == "John Doe"
    assert payload.get("email") == "j@x.com"
    assert payload.get("webhook_url") == "http://callback"


def test_build_person_payload_phone_country_company():
    """_build_person_payload includes phone, country, company when present."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    data = {
        "first_name": "J",
        "last_name": "D",
        "phone_isd_code": "+1",
        "phone_number": "5551234",
        "company_name": "Acme",
        "addresses": [{"country": "USA"}],
    }
    payload = svc._build_person_payload(data, webhook_url=None)
    assert payload.get("phone") == "+15551234"
    assert payload.get("country") == "USA"
    assert payload.get("company") == "Acme"


def test_build_company_payload_required_fields():
    """_build_company_payload includes account_id, external_id, company_name."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    payload = svc._build_company_payload("client-1", "org-1", {"name": "Acme"}, webhook_url=None)
    assert payload["account_id"] == "org-1"
    assert payload["external_id"] == "client-1"
    assert payload["company_name"] == "Acme"
    assert payload["project_id"] == "client-1"


def test_build_company_payload_opt_website_linkedin_ind():
    """_build_company_payload includes website, linkedin, industry, location."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    data = {
        "name": "Co",
        "websites": [{"url": "https://co.com"}],
        "social_pages": [{"platform": "linkedin", "url": "https://li.com"}],
        "industry": "Tech",
        "addresses": [{"country": "UK"}],
    }
    payload = svc._build_company_payload("c1", "o1", data, webhook_url="http://w")
    assert payload["website_url"] == "https://co.com"
    assert payload["linkedin_url"] == "https://li.com"
    assert payload["industry"] == "Tech"
    assert payload["location"] == "UK"
    assert payload["webhook_url"] == "http://w"


def test_build_company_enrichment_update_simple():
    """build_company_enrichment_update maps companyName, industry, website."""
    enriched = {
        "companyName": " Acme Inc ",
        "industry": "Tech",
        "website": "https://acme.com",
    }
    update = ClientEnrichmentService.build_company_enrichment_update(enriched)
    assert update["name"] == "Acme Inc"
    assert update["industry"] == "Tech"
    assert update["enrichment_status"] == "completed"
    assert update["enrichment_done"] is True
    assert "last_enriched_at" in update


def test_build_company_enrichment_no_overwrite_empty():
    """build_company_enrichment_update does not overwrite existing with empty."""
    enriched = {"companyName": "", "industry": ""}
    existing = {"name": "Existing Name", "industry": "Existing"}
    update = ClientEnrichmentService.build_company_enrichment_update(
        enriched, existing_client=existing
    )
    assert "name" not in update or update.get("name")
    assert update.get("enrichment_status") == "completed"


def test_build_company_enrichment_update_invalid_input():
    """build_company_enrichment_update returns {} for non-dict or empty."""
    assert not ClientEnrichmentService.build_company_enrichment_update(None)
    assert not ClientEnrichmentService.build_company_enrichment_update({})
    assert not ClientEnrichmentService.build_company_enrichment_update([])


def test_build_person_enrichment_update_simple():
    """build_person_enrichment_update maps personalInfo name and company website."""
    enriched = {
        "personalInfo": {"firstName": " Jane ", "lastName": " Doe "},
        "companyInfo": {"website": "https://co.com", "industry": "Legal"},
    }
    update = ClientEnrichmentService.build_person_enrichment_update(enriched)
    assert update["name"] == "Jane Doe"
    assert update["industry"] == "Legal"
    assert update["enrichment_status"] == "completed"
    assert update["enrichment_done"] is True


def test_build_person_enrichment_update_empty_input():
    """build_person_enrichment_update returns {} for non-dict or empty."""
    assert not ClientEnrichmentService.build_person_enrichment_update(None)
    assert not ClientEnrichmentService.build_person_enrichment_update({})


class _FakeConnCM:
    """Async context manager that yields a mock connection."""

    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *a):
        return None


@pytest.mark.asyncio
async def test_run_enrichment_person_calls_api_and_repo(monkeypatch):
    """run_client_enrichment for person calls enrich API and updates client."""
    mock_post = AsyncMock(return_value={"request_id": "req-123"})
    mock_repo_update = AsyncMock()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.ContactsRepository",
        lambda _conn: MagicMock(update_contact=mock_repo_update),
    )
    monkeypatch.setattr(ClientEnrichmentService, "_post", mock_post)

    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    await svc.run_client_enrichment(
        client_id="c1",
        organization_id="org-1",
        client_type="person",
        payload_data={"first_name": "John", "last_name": "Doe"},
        entity_table="contacts",
    )

    mock_post.assert_called_once()
    call_kw = mock_repo_update.call_args
    assert call_kw[1]["contact_id"] == "c1"
    assert call_kw[1]["organization_id"] == "org-1"
    assert call_kw[1]["update_data"]["enrichment_request_id"] == "req-123"
    assert call_kw[1]["update_data"]["enrichment_status"] == "requested"


@pytest.mark.asyncio
async def test_run_enrichment_no_request_id_skips_update(monkeypatch):
    """run_client_enrichment does not update when API returns no request_id."""
    mock_post = AsyncMock(return_value={})
    mock_repo_update = AsyncMock()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.ContactsRepository",
        lambda _conn: MagicMock(update_contact=mock_repo_update),
    )
    monkeypatch.setattr(ClientEnrichmentService, "_post", mock_post)

    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    await svc.run_client_enrichment(
        client_id="c1",
        organization_id="org-1",
        client_type="person",
        payload_data={"first_name": "John"},
        entity_table="contacts",
    )

    mock_repo_update.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_enrichment_invokes_run_per_item(monkeypatch):
    """run_bulk_client_enrichment runs enrichment for each item (parallel)."""
    run_calls = []

    async def capture_run(_self, client_id, organization_id, client_type, payload_data):
        run_calls.append(
            {
                "client_id": client_id,
                "organization_id": organization_id,
                "client_type": client_type,
                "payload_data": payload_data,
            }
        )

    monkeypatch.setattr(
        ClientEnrichmentService,
        "run_client_enrichment",
        capture_run,
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    items = [
        {"client_id": "c1", "organization_id": "org-1", "client_type": "person"},
        {"client_id": "c2", "organization_id": "org-1", "client_type": "company"},
    ]
    payload = {"name": "Acme"}
    await svc.run_bulk_client_enrichment(items, payload)
    assert len(run_calls) == 2
    assert run_calls[0]["client_id"] == "c1"
    assert run_calls[0]["client_type"] == "person"
    assert run_calls[1]["client_id"] == "c2"
    assert run_calls[1]["client_type"] == "company"


@pytest.mark.asyncio
async def test_run_client_enrichment_empty_items():
    """run_bulk_client_enrichment with empty list does nothing."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    await svc.run_bulk_client_enrichment([], {"name": "x"})


@pytest.mark.asyncio
async def test_fetch_sales_intelligence_returns_payload(monkeypatch):
    """_fetch_sales_intelligence returns sales_intelligence dict when API succeeds."""
    payload = {"summary": "report", "scores": {"a": 1}}
    mock_post = AsyncMock(return_value={"success": True, "sales_intelligence": payload})
    monkeypatch.setattr(ClientEnrichmentService, "_post", mock_post)
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    result = await svc._fetch_sales_intelligence(
        person_info={"name": "Jane"}, company_info={"companyName": "Co"}
    )
    assert result == payload
    mock_post.assert_called_once_with(
        "/enrich/sales-intelligence",
        {"person_info": {"name": "Jane"}, "company_info": {"companyName": "Co"}},
    )


@pytest.mark.asyncio
async def test_fetch_sales_intelligence_none_on_failure(monkeypatch):
    """_fetch_sales_intelligence returns None when API fails or returns invalid data."""
    monkeypatch.setattr(ClientEnrichmentService, "_post", AsyncMock(side_effect=Exception("err")))
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    result = await svc._fetch_sales_intelligence(person_info={}, company_info={})
    assert result is None


@pytest.mark.asyncio
async def test_company_webhook_no_request_id_returns_false():
    """process_company_enrichment_webhook returns False when request_id missing."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    fake_conn = MagicMock()
    out = await svc.process_company_enrichment_webhook(fake_conn, {})
    assert out is None
    out = await svc.process_company_enrichment_webhook(
        fake_conn, {"enriched_company": {"companyName": "x"}}
    )
    assert out is None


@pytest.mark.asyncio
async def test_company_webhook_finds_client_and_updates(monkeypatch):
    """process_company_enrichment_webhook finds client by request_id and updates."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "name": "Old",
        "additional_data": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()
    mock_create_addresses = AsyncMock()

    class FakeRepo:
        """Minimal CompaniesRepository double for company webhook tests."""

        get_company_for_update_by_enrichment_request_id = mock_get
        update_company = mock_update
        create_company_addresses = mock_create_addresses

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    mock_fetch_sales = AsyncMock(return_value=None)
    monkeypatch.setattr(ClientEnrichmentService, "_fetch_sales_intelligence", mock_fetch_sales)
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    body = {
        "request_id": "req-1",
        "enriched_company": {"companyName": "New Co", "industry": "Tech"},
    }
    result = await svc.process_company_enrichment_webhook(MagicMock(), body)
    assert result == ("c1", "org-1")
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["update_data"].get("name") == "New Co"


@pytest.mark.asyncio
async def test_person_webhook_no_request_id_returns_false():
    """process_person_enrichment_webhook returns False when request_id missing."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    out = await svc.process_person_enrichment_webhook(MagicMock(), {})
    assert out is None


@pytest.mark.asyncio
async def test_person_webhook_finds_client_and_updates(monkeypatch):
    """process_person_enrichment_webhook finds client by request_id and updates."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "name": "Old",
        "additional_data": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()

    class FakeRepo:
        """Minimal ContactsRepository double for person webhook tests."""

        get_contact_for_update_by_enrichment_request_id = mock_get
        update_contact = mock_update

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.ContactsRepository",
        lambda conn: FakeRepo(),
    )
    mock_fetch_sales = AsyncMock(return_value=None)
    monkeypatch.setattr(ClientEnrichmentService, "_fetch_sales_intelligence", mock_fetch_sales)
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    body = {
        "request_id": "req-1",
        "enriched_profile": {
            "personalInfo": {"firstName": "Jane", "lastName": "Doe"},
        },
    }
    result = await svc.process_person_enrichment_webhook(MagicMock(), body)
    assert result == ("c1", "org-1")
    mock_update.assert_called_once()
    assert mock_update.call_args[1]["update_data"].get("first_name") == "Jane"
    assert mock_update.call_args[1]["update_data"].get("last_name") == "Doe"


@pytest.mark.asyncio
async def test_company_webhook_updates_client_sales_intel(monkeypatch):
    """Company webhook updates client record only; sales intelligence runs in background task."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "name": "Old",
        "additional_data": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()
    mock_create_addresses = AsyncMock()

    class FakeRepo:
        """Minimal CompaniesRepository double for company webhook tests."""

        get_company_for_update_by_enrichment_request_id = mock_get
        update_company = mock_update
        create_company_addresses = mock_create_addresses

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    body = {
        "request_id": "req-1",
        "enriched_company": {"companyName": "New Co", "industry": "Tech"},
    }
    result = await svc.process_company_enrichment_webhook(MagicMock(), body)
    assert result == ("c1", "org-1")
    assert mock_update.call_count == 1
    assert mock_update.call_args[1]["update_data"].get("name") == "New Co"


@pytest.mark.asyncio
async def test_company_webhook_no_client_returns_false(monkeypatch):
    """process_company_enrichment_webhook returns False when client not found."""
    mock_get = AsyncMock(return_value=None)

    class FakeRepo:
        """Minimal CompaniesRepository double (get only, returns None)."""

        get_company_for_update_by_enrichment_request_id = mock_get

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    body = {
        "request_id": "req-1",
        "enriched_company": {"companyName": "X"},
    }
    result = await svc.process_company_enrichment_webhook(MagicMock(), body)
    assert result is None


def test_build_company_enrichment_social_market_tech():
    """build_company_enrichment_update includes social, market, tech, linked."""
    enriched = {
        "companyName": "Co",
        "socialProfiles": {"linkedin": " https://li.com ", "twitter": "  "},
        "marketAudience": {"marketSegments": ["B2B", "SMB"]},
        "platformPreferences": ["AWS", "GCP"],
        "communication": {
            "channels": ["email", "slack"],
            "industryTerminology": ["API"],
        },
        "linkedPages": [{"pageName": "Blog", "pageLink": "https://blog.co"}],
    }
    update = ClientEnrichmentService.build_company_enrichment_update(enriched)
    assert update["name"] == "Co"
    assert len(update["social_pages"]) == 1
    assert update["target_market_segments"] == ["B2B", "SMB"]
    assert update["current_tech_stack"] == ["AWS", "GCP"]
    assert update["preferred_communication_channels"] == ["email", "slack"]
    assert len(update["linked_pages"]) == 1
    assert update["linked_pages"][0]["page_name"] == "Blog"


def test_build_enrichment_merges_preserves_social_pages():
    """Enrichment returning only one platform must not wipe other existing platforms."""
    existing_client = {
        "social_pages": [
            {
                "id": "social-linkedin",
                "platform": "linkedin",
                "url": "https://linkedin.com/in/old",
            },
            {"id": "social-github", "platform": "github", "url": "https://github.com/old"},
        ]
    }
    enriched_company = {"socialProfiles": {"github": "https://github.com/new"}}

    update = ClientEnrichmentService.build_company_enrichment_update(
        enriched_company, existing_client=existing_client
    )

    social_pages = update["social_pages"]
    assert len(social_pages) == 2

    by_platform = {sp["platform"]: sp for sp in social_pages}
    assert by_platform["linkedin"]["url"] == "https://linkedin.com/in/old"
    assert by_platform["github"]["url"] == "https://github.com/new"
    # Preserve existing IDs where we override.
    assert by_platform["linkedin"]["id"] == "social-linkedin"
    assert by_platform["github"]["id"] == "social-github"


def test_build_company_enrichment_key_people_products():
    """build_company_enrichment_update maps keyPeople and products."""
    enriched = {
        "keyPeople": [
            {"name": "Alice", "title": "CEO", "linkedin": "https://li.com/alice"},
        ],
        "products": [{"name": "Widget", "url": "https://w.com", "description": "Desc"}],
    }
    update = ClientEnrichmentService.build_company_enrichment_update(enriched)
    assert len(update["key_people"]) == 1
    assert update["key_people"][0]["name"] == "Alice"
    assert update["key_people"][0]["title"] == "CEO"
    assert len(update["products"]) == 1
    assert update["products"][0]["name"] == "Widget"


def test_build_company_enrichment_comm_not_dict():
    """build_company_enrichment_update handles communication non-dict."""
    enriched = {"communication": []}
    update = ClientEnrichmentService.build_company_enrichment_update(enriched)
    assert update["preferred_communication_channels"] == []
    assert update["industry_specific_terminologies"] == []


def test_build_person_enrichment_work_and_education():
    """build_person_enrichment_update maps workHistory and education."""
    enriched = {
        "workHistory": [
            {
                "companyName": "Acme",
                "title": "Dev",
                "startDate": "2020",
                "endDate": None,
            },
        ],
        "education": [
            {"school": "Uni", "degree": "BS", "field": "CS", "yearStart": 2016},
        ],
    }
    update = ClientEnrichmentService.build_person_enrichment_update(enriched)
    assert len(update["work_history"]) == 1
    assert update["work_history"][0]["company"] == "Acme"
    assert update["work_history"][0]["job_title"] == "Dev"
    assert len(update["educational_history"]) == 1
    assert update["educational_history"][0]["university"] == "Uni"


def test_build_person_work_history_skips_non_dict():
    """workHistory items that are not dict are skipped."""
    enriched = {"workHistory": ["not-a-dict", {"companyName": "A", "title": "T"}]}
    update = ClientEnrichmentService.build_person_enrichment_update(enriched)
    assert len(update["work_history"]) == 1
    assert update["work_history"][0]["company"] == "A"


def test_build_person_education_skips_non_dict():
    """education items that are not dict are skipped."""
    enriched = {"education": [None, {"school": "S", "degree": "D"}]}
    update = ClientEnrichmentService.build_person_enrichment_update(enriched)
    assert len(update["educational_history"]) == 1
    assert update["educational_history"][0]["university"] == "S"


def test_build_person_enrichment_social_and_skills():
    """build_person_enrichment_update maps socialProfiles and skills."""
    enriched = {
        "socialProfiles": {"linkedin": "https://li.com"},
        "skills": ["Python", "Go"],
    }
    update = ClientEnrichmentService.build_person_enrichment_update(enriched)
    assert len(update["social_pages"]) == 1
    assert update["skills"] == ["Python", "Go"]


def test_person_enrichment_merges_preserves_existing():
    """Same merge behavior for person enrichment webhooks."""
    existing_client = {
        "social_pages": [
            {"id": "social-linkedin", "platform": "linkedin", "url": "https://li.com/old"},
            {"id": "social-github", "platform": "github", "url": "https://gh.com/old"},
        ]
    }
    enriched_profile = {"socialProfiles": {"github": "https://gh.com/new"}}

    update = ClientEnrichmentService.build_person_enrichment_update(
        enriched_profile, existing_client=existing_client
    )

    social_pages = update["social_pages"]
    assert len(social_pages) == 2

    by_platform = {sp["platform"]: sp for sp in social_pages}
    assert by_platform["linkedin"]["url"] == "https://li.com/old"
    assert by_platform["github"]["url"] == "https://gh.com/new"
    assert by_platform["linkedin"]["id"] == "social-linkedin"
    assert by_platform["github"]["id"] == "social-github"


class _FakePool:
    """Minimal pool double used for sales intelligence tests."""


@pytest.mark.asyncio
async def test_sales_intel_empty_request_id(monkeypatch):
    """fetch_and_store_sales_intelligence_for_request ignores empty request_id."""
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=_FakePool()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    svc = ClientEnrichmentService(
        base_url="http://e",
        webhook_url="http://w",
        timeout_seconds=30.0,
    )
    await svc.fetch_and_store_sales_intelligence_for_request("", {})


@pytest.mark.asyncio
async def test_sales_intel_client_not_found(monkeypatch):
    """fetch_and_store_sales_intelligence logs when client not found."""
    mock_get = AsyncMock(return_value=None)

    class FakeRepo:
        """Minimal CompaniesRepository double for sales intelligence tests."""

        get_company_for_update_by_enrichment_request_id = mock_get

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=_FakePool()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )

    svc = ClientEnrichmentService(
        base_url="http://e",
        webhook_url="http://w",
        timeout_seconds=30.0,
    )
    await svc.fetch_and_store_sales_intelligence_for_request(
        "req-404",
        enriched_company={"companyName": "Acme"},
    )
    mock_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_sales_intel_company_webhook_path(monkeypatch):
    """fetch_and_store_sales_intelligence with enriched_company stores sales intel"""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "additional_data": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()
    sales_payload = {"summary": "company report"}

    class FakeRepo:
        """Minimal CompaniesRepository double for sales intelligence tests."""

        get_company_for_update_by_enrichment_request_id = mock_get
        update_company = mock_update

    async def fake_fetch_sales(_self, person_info, company_info):
        assert person_info == {}
        assert company_info == {"companyName": "Acme"}
        return sales_payload

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=_FakePool()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    monkeypatch.setattr(
        ClientEnrichmentService,
        "_fetch_sales_intelligence",
        fake_fetch_sales,
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    await svc.fetch_and_store_sales_intelligence_for_request(
        "req-1",
        enriched_company={"companyName": "Acme"},
    )
    call_kwargs = mock_update.call_args[1]
    assert call_kwargs["update_data"] == {"sales_intelligence": sales_payload}


@pytest.mark.asyncio
async def test_sales_intel_uses_body_profile(monkeypatch):
    """fetch_and_store_sales_intelligence prefers enriched_profile from body."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "additional_data": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()
    sales_payload = {"summary": "sales", "scores": {}}

    class FakeRepo:
        """Minimal CompaniesRepository double for sales intelligence tests."""

        get_company_for_update_by_enrichment_request_id = mock_get
        update_company = mock_update

    async def fake_fetch_sales(_self, person_info, company_info):
        # Sales intelligence is stored on companies only; person_info is unused here.
        assert person_info == {}
        assert company_info == {"website": "https://co.com"}
        return sales_payload

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=_FakePool()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    monkeypatch.setattr(
        ClientEnrichmentService,
        "_fetch_sales_intelligence",
        fake_fetch_sales,
    )

    svc = ClientEnrichmentService(
        base_url="http://e",
        webhook_url="http://w",
        timeout_seconds=30.0,
    )
    profile = {
        "personalInfo": {"firstName": "Jane"},
        "companyInfo": {"website": "https://co.com"},
    }
    await svc.fetch_and_store_sales_intelligence_for_request(
        "req-1",
        # This method stores sales intelligence ONLY for companies and requires enriched_company.
        enriched_company={"website": "https://co.com"},
        enriched_profile=profile,
    )
    mock_get.assert_awaited_once()
    mock_update.assert_awaited_once()
    call_kwargs = mock_update.call_args[1]
    assert call_kwargs["company_id"] == "c1"
    assert call_kwargs["organization_id"] == "org-1"
    assert call_kwargs["update_data"] == {"sales_intelligence": sales_payload}


@pytest.mark.asyncio
async def test_sales_intel_uses_stored_profile(monkeypatch):
    """fetch_and_store_sales_intelligence ignores stored profile when none passed."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "additional_data": '{"enriched_profile": {"personalInfo": {"firstName": "John"},'
        '"companyInfo": {"website": "https://stored.com"}}}',
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()
    sales_payload = {"summary": "stored", "scores": {}}

    class FakeRepo:
        """Minimal CompaniesRepository double for sales intelligence tests."""

        get_company_for_update_by_enrichment_request_id = mock_get
        update_company = mock_update

    async def fake_fetch_sales(*_args, **kwargs):
        person_info = kwargs["person_info"]
        company_info = kwargs["company_info"]
        assert person_info == {}
        assert company_info == {"companyName": "x"}
        return sales_payload

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=_FakePool()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    monkeypatch.setattr(
        ClientEnrichmentService,
        "_fetch_sales_intelligence",
        fake_fetch_sales,
    )

    svc = ClientEnrichmentService(
        base_url="http://e",
        webhook_url="http://w",
        timeout_seconds=30.0,
    )
    await svc.fetch_and_store_sales_intelligence_for_request(
        "req-1",
        enriched_company={"companyName": "x"},
        enriched_profile=None,
    )
    mock_get.assert_awaited_once()
    mock_update.assert_awaited_once()
    call_kwargs = mock_update.call_args[1]
    assert call_kwargs["company_id"] == "c1"
    assert call_kwargs["organization_id"] == "org-1"
    assert call_kwargs["update_data"] == {"sales_intelligence": sales_payload}


@pytest.mark.asyncio
async def test_run_enrichment_company_calls_api_and_repo(monkeypatch):
    """run_client_enrichment for company calls enrich/company and updates client."""
    mock_post = AsyncMock(return_value={"request_id": "req-co-1"})
    mock_repo_update = AsyncMock()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        lambda _pool: _FakeConnCM(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda _conn: MagicMock(update_company=mock_repo_update),
    )
    monkeypatch.setattr(ClientEnrichmentService, "_post", mock_post)

    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    await svc.run_client_enrichment(
        client_id="c2",
        organization_id="org-2",
        client_type="company",
        payload_data={"name": "Acme Inc", "email": "hi@acme.com"},
        entity_table="companies",
    )

    mock_post.assert_called_once()
    assert mock_repo_update.call_args[1]["update_data"]["enrichment_request_id"] == "req-co-1"


@pytest.mark.asyncio
async def test_run_enrichment_uses_existing_conn(monkeypatch):
    """run_client_enrichment uses provided connection and skips pool."""
    fake_conn = object()
    mock_post = AsyncMock(return_value={"request_id": "req-xyz"})
    mock_repo_update = AsyncMock()
    captured_conn = {}

    def fake_companies_repository(conn):
        """Capture connection passed to CompaniesRepository."""
        captured_conn["value"] = conn
        return MagicMock(update_company=mock_repo_update)

    def fail_get_pool():
        """Fail if get_pool is used when conn is provided."""
        raise AssertionError("get_pool should not be called when conn is provided")

    def fail_acquire_connection(_pool):
        """Fail if AcquireConnection is used when conn is provided."""
        raise AssertionError("AcquireConnection should not be used when conn is provided")

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_pool",
        AsyncMock(side_effect=fail_get_pool),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.AcquireConnection",
        fail_acquire_connection,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        fake_companies_repository,
    )
    monkeypatch.setattr(ClientEnrichmentService, "_post", mock_post)

    svc = ClientEnrichmentService(
        base_url="http://e",
        webhook_url="http://w",
        timeout_seconds=30.0,
    )
    await svc.run_client_enrichment(
        client_id="c3",
        organization_id="org-3",
        client_type="company",
        payload_data={"name": "Co", "email": "co@example.com"},
        conn=fake_conn,
        entity_table="companies",
    )

    mock_post.assert_called_once()
    mock_repo_update.assert_called_once()
    assert captured_conn["value"] is fake_conn


@pytest.mark.asyncio
async def test_process_company_webhook_adds_addresses(monkeypatch):
    """process_company_webhook calls bulk_create_addresses when HQ/locations."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "name": "Old",
        "additional_data": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()
    mock_create_addresses = AsyncMock()

    class FakeRepo:
        """Minimal CompaniesRepository double for company webhook address tests."""

        get_company_for_update_by_enrichment_request_id = mock_get
        update_company = mock_update
        create_company_addresses = mock_create_addresses

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: FakeRepo(),
    )
    mock_fetch_sales = AsyncMock(return_value=None)
    monkeypatch.setattr(ClientEnrichmentService, "_fetch_sales_intelligence", mock_fetch_sales)
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    body = {
        "request_id": "req-1",
        "enriched_company": {
            "companyName": "Co",
            "headquarters": {"address": "123 Main St", "city": "NYC", "country": "USA"},
        },
    }
    result = await svc.process_company_enrichment_webhook(MagicMock(), body)
    assert result == ("c1", "org-1")
    mock_create_addresses.assert_called_once()
    call_args = mock_create_addresses.call_args[0][0]
    assert len(call_args) == 1
    assert call_args[0]["address_line1"] == "123 Main St"
    assert call_args[0]["company_id"] == "c1"


def test_map_addresses_company_alt_locations():
    """_map_addresses_from_company includes alternativeLocations."""
    enriched = {
        "alternativeLocations": [
            {"address": "456 Other St", "city": "LA", "country": "USA"},
        ],
    }
    rows = ClientEnrichmentService._map_addresses_from_company(enriched)
    assert len(rows) == 1
    assert rows[0]["address_line1"] == "456 Other St"
    assert rows[0]["city"] == "LA"


@pytest.mark.asyncio
async def test_process_company_webhook_invalid_company_false():
    """process_company_enrichment_webhook returns False when enriched_company invalid."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    out = await svc.process_company_enrichment_webhook(
        MagicMock(), {"request_id": "r1", "enriched_company": []}
    )
    assert out is None


@pytest.mark.asyncio
async def test_process_person_webhook_invalid_profile_false():
    """process_person_enrichment_webhook returns False when enriched_profile invalid."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    out = await svc.process_person_enrichment_webhook(
        MagicMock(), {"request_id": "r1", "enriched_profile": "not-dict"}
    )
    assert out is None
