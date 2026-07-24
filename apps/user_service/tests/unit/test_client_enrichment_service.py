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
    client_enrichment_enabled,
    require_client_enrichment_enabled,
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


def test_client_enrichment_enabled_reads_settings(monkeypatch):
    """client_enrichment_enabled reflects ENRICHMENT_SERVICE_ENABLED."""
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(enrichment_service=MagicMock(enabled=True)),
    )
    assert client_enrichment_enabled() is True

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(enrichment_service=MagicMock(enabled=False)),
    )
    assert client_enrichment_enabled() is False


def test_require_enrichment_raises_when_disabled(monkeypatch):
    """require_client_enrichment_enabled raises 503 when feature is off."""
    from libs.shared_utils.http_exceptions import ServiceUnavailableException

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.client_enrichment_enabled",
        lambda: False,
    )
    with pytest.raises(ServiceUnavailableException):
        require_client_enrichment_enabled()


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
async def test_run_enrichment_skipped_when_disabled(monkeypatch):
    """run_client_enrichment is a no-op when ENRICHMENT_SERVICE_ENABLED is false."""
    mock_post = AsyncMock(return_value={"request_id": "req-123"})
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.client_enrichment_enabled",
        lambda: False,
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
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_run_enrichment_person_calls_api_and_repo(monkeypatch):
    """run_client_enrichment for person calls enrich API and updates client."""
    mock_post = AsyncMock(return_value={"request_id": "req-123"})
    mock_repo_update = AsyncMock()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.client_enrichment_enabled",
        lambda: True,
    )
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

    async def capture_run(_self, client_id, organization_id, client_type, payload_data, **kwargs):
        run_calls.append(
            {
                "client_id": client_id,
                "organization_id": organization_id,
                "client_type": client_type,
                "payload_data": payload_data,
                **kwargs,
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
    # Enrichment must not change contact names; only enrich non-name fields + metadata.
    assert "first_name" not in (mock_update.call_args[1]["update_data"] or {})
    assert "last_name" not in (mock_update.call_args[1]["update_data"] or {})


@pytest.mark.asyncio
async def test_person_webhook_stores_profile_photo_key(monkeypatch):
    """person webhook stores enrichment profileUrl as R2 object key (best-effort)."""
    existing = {
        "id": "c1",
        "organization_id": "org-1",
        "name": "Old",
        "additional_data": None,
        "profile_photo_url": None,
    }
    mock_get = AsyncMock(return_value=existing)
    mock_update = AsyncMock()

    class FakeRepo:
        """Minimal ContactsRepository double for profile photo tests."""

        get_contact_for_update_by_enrichment_request_id = mock_get
        update_contact = mock_update

    async def fake_store_photo(
        _self,
        *,
        enriched_profile,
        contact_id,
        organization_id,
        existing_profile_photo_url,
    ):
        """Fake profile photo storage function."""
        assert contact_id == "c1"
        assert organization_id == "org-1"
        assert existing_profile_photo_url is None
        assert enriched_profile["personalInfo"]["profileUrl"].startswith("https://")
        return "contacts/org-1/c1/profile_test.jpg"

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.ContactsRepository",
        lambda conn: FakeRepo(),
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.client_enrichment_service."
            "ClientEnrichmentService._maybe_store_profile_photo_from_enrichment"
        ),
        fake_store_photo,
    )
    mock_fetch_sales = AsyncMock(return_value=None)
    monkeypatch.setattr(ClientEnrichmentService, "_fetch_sales_intelligence", mock_fetch_sales)

    svc = ClientEnrichmentService(
        base_url="http://e",
        webhook_url="http://w",
        timeout_seconds=30.0,
    )
    body = {
        "request_id": "req-1",
        "enriched_profile": {
            "personalInfo": {
                "firstName": "Jane",
                "lastName": "Doe",
                "profileUrl": "https://media.licdn.com/dms/image/v2/x.jpg",
            },
        },
    }
    result = await svc.process_person_enrichment_webhook(MagicMock(), body)
    assert result == ("c1", "org-1")
    mock_update.assert_called_once()
    updated_key = mock_update.call_args[1]["update_data"].get("profile_photo_url")
    assert updated_key == "contacts/org-1/c1/profile_test.jpg"


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
        "apps.user_service.app.services.client_enrichment_service.client_enrichment_enabled",
        lambda: True,
    )
    # Avoid logo.dev + R2 when LOGO_DEV_KEY is set in env (would slow tests / hit network).
    monkeypatch.setattr(
        ClientEnrichmentService,
        "_fetch_company_logo_public_url_best_effort",
        AsyncMock(return_value=None),
    )

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

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.client_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        ClientEnrichmentService,
        "_fetch_company_logo_public_url_best_effort",
        AsyncMock(return_value=None),
    )

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


def test_normalize_webhook_update_payload_jsonb():
    """Webhook payload JSONB keys are parsed like PATCH bodies."""
    from apps.user_service.app.services.client_enrichment_service import (
        _normalize_webhook_update_payload,
    )

    payload = {"social_pages": '[{"platform": "linkedin", "url": "https://li.com"}]'}
    _normalize_webhook_update_payload(payload, frozenset({"social_pages"}))
    assert isinstance(payload["social_pages"], list)


def test_merge_social_pages_empty_url_keeps_existing():
    """Empty enrichment url does not override existing social page."""
    from apps.user_service.app.services.client_enrichment_service import (
        _merge_social_pages_by_platform,
    )

    merged = _merge_social_pages_by_platform(
        enriched_social_pages=[{"platform": "linkedin", "url": ""}],
        existing_social_pages=[{"id": "1", "platform": "linkedin", "url": "https://old.com"}],
    )
    assert merged[0]["url"] == "https://old.com"


def test_address_dicts_for_country_lookup_patch_shape():
    """AddressesUpdate dict shape is normalized for country lookup."""
    from apps.user_service.app.services.client_enrichment_service import (
        _address_dicts_for_country_lookup,
        _first_country_from_addresses,
    )

    rows = _address_dicts_for_country_lookup(
        {"add": [{"country": "USA"}], "update": [{"country": "UK"}]}
    )
    assert len(rows) == 2
    assert _first_country_from_addresses({"addresses": rows}) == "USA"


def test_slugify_and_public_r2_url():
    """Logo slugify and public R2 URL helpers behave predictably."""
    from apps.user_service.app.services.client_enrichment_service import (
        _logo_dev_name_image_url,
        _public_r2_url_for_object_key,
        _slugify,
    )

    assert _slugify("Acme Corp!!") == "acme-corp"
    assert _public_r2_url_for_object_key("logos/x.png").endswith("/logos/x.png")
    url = _logo_dev_name_image_url("Acme", "tok123")
    assert "img.logo.dev" in url
    assert "Acme" in url


def test_build_contact_enrichment_update_maps_profile():
    """build_contact_enrichment_update maps skills, work, education, and metadata."""
    enriched = {
        "socialProfiles": {"linkedin": "https://li.com/in/jane"},
        "skills": ["Python"],
        "workHistory": [{"companyName": "Acme", "title": "Dev", "startDate": "2020"}],
        "education": [{"school": "MIT", "degree": "BS"}],
    }
    update = ClientEnrichmentService.build_contact_enrichment_update(enriched)
    assert update["skills"] == ["Python"]
    assert update["enrichment_status"] == "completed"
    assert update["additional_data"]["enriched_profile"] == enriched


def test_is_safe_public_http_url_blocks_private():
    """Profile photo URL guard rejects localhost and private IPs."""
    assert ClientEnrichmentService._is_safe_public_http_url("http://127.0.0.1/x.jpg") is False
    assert ClientEnrichmentService._is_safe_public_http_url("https://cdn.example.com/x.jpg") is True


def test_ext_and_content_type_from_response():
    """Image content-type mapping chooses safe extensions."""
    resp = MagicMock()
    resp.headers = {"content-type": "image/png; charset=binary"}
    ext, content_type = ClientEnrichmentService._ext_and_content_type_from_response(resp)
    assert ext == "png"
    assert content_type == "image/png"


def test_extract_profile_photo_url():
    """Profile photo URL is read from personalInfo.profileUrl."""
    url = ClientEnrichmentService._extract_profile_photo_url(
        {"personalInfo": {"profileUrl": " https://cdn.example.com/p.jpg "}}
    )
    assert url == "https://cdn.example.com/p.jpg"


@pytest.mark.asyncio
async def test_fetch_company_logo_uploads_to_r2(monkeypatch):
    """Company logo fetch downloads from logo.dev and uploads to R2."""
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        return_value=MagicMock(content=b"png-bytes", raise_for_status=MagicMock())
    )
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    mock_r2 = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(enrichment_service=MagicMock(logo_dev_key="tok", timeout_seconds=5.0)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.shared_settings",
        MagicMock(
            cloudflare_r2=MagicMock(bucket_name="bucket", media_url="https://media.example.com")
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.httpx.AsyncClient",
        lambda **kwargs: mock_http,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.get_r2_client",
        lambda: mock_r2,
    )

    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    url = await svc._fetch_company_logo_public_url_best_effort(
        company_id="co-1",
        payload_data={"name": "Acme"},
    )
    assert url == "https://media.example.com/logos/companies/co-1-acme.png"
    mock_r2.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_store_profile_photo_from_enrichment(monkeypatch):
    """Profile photo storage downloads, uploads to R2, and returns object key."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._download_profile_photo = AsyncMock(return_value=(b"img", "jpg", "image/jpeg"))
    svc._upload_profile_photo_to_r2 = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.shared_settings",
        MagicMock(cloudflare_r2=MagicMock(bucket_name="bucket")),
    )

    key = await svc._maybe_store_profile_photo_from_enrichment(
        enriched_profile={"personalInfo": {"profileUrl": "https://cdn.example.com/p.jpg"}},
        contact_id="c-1",
        organization_id="org-1",
        existing_profile_photo_url=None,
    )
    assert key.startswith("contacts/org-1/c-1/profile_")
    svc._upload_profile_photo_to_r2.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_enrichment_unsupported_client_type(monkeypatch):
    """Unsupported client_type is logged and skipped."""
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.client_enrichment_enabled",
        lambda: True,
    )
    mock_post = AsyncMock()
    monkeypatch.setattr(ClientEnrichmentService, "_post", mock_post)
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    await svc.run_client_enrichment(
        client_id="c1",
        organization_id="org-1",
        client_type="unknown",
        payload_data={},
    )
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_persist_enrichment_status_unsupported_table(caplog):
    """Unsupported entity_table logs error and skips repository update."""
    await ClientEnrichmentService._persist_enrichment_status(
        db_conn=MagicMock(),
        entity_table="clients",
        entity_id="c1",
        organization_id="org-1",
        update_data={"enrichment_status": "requested"},
    )


@pytest.mark.asyncio
async def test_bulk_enrichment_filters_invalid_items():
    """Bulk enrichment ignores items missing required keys."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=30.0)
    svc.run_client_enrichment = AsyncMock()
    await svc.run_bulk_client_enrichment([{"client_id": "c1"}], {"name": "x"})
    svc.run_client_enrichment.assert_not_called()


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


@pytest.mark.asyncio
async def test_post_enrichment_request(monkeypatch):
    """_post sends JSON payload and returns parsed response."""
    calls: dict = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"request_id": "req-1", "status": "queued"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls["url"] = url
            calls["json"] = json
            return _Resp()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.httpx.AsyncClient",
        lambda timeout: _Client(),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    data = await svc._post("/enrich", {"name": "Jane"})
    assert data["request_id"] == "req-1"
    assert calls["url"] == "http://e/enrich"


@pytest.mark.asyncio
async def test_fetch_company_logo_missing_token_returns_none(monkeypatch):
    """Logo fetch returns None when logo.dev token is not configured."""
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(enrichment_service=MagicMock(logo_dev_key="")),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    url = await svc._fetch_company_logo_public_url_best_effort(
        company_id="co-1",
        payload_data={"name": "Acme"},
    )
    assert url is None


@pytest.mark.asyncio
async def test_fetch_company_logo_missing_name_returns_none(monkeypatch):
    """Logo fetch returns None when company name is blank."""
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(enrichment_service=MagicMock(logo_dev_key="tok")),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    url = await svc._fetch_company_logo_public_url_best_effort(
        company_id="co-1",
        payload_data={"name": "   "},
    )
    assert url is None


@pytest.mark.asyncio
async def test_store_sales_intelligence_no_data(monkeypatch):
    """_store_sales_intelligence_for_company exits when API returns no payload."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._fetch_sales_intelligence = AsyncMock(return_value=None)
    repo_cls = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        repo_cls,
    )
    await svc._store_sales_intelligence_for_company(
        company_id="co-1",
        organization_id="org-1",
        person_info={},
        company_info={},
        conn=MagicMock(),
    )
    repo_cls.return_value.update_company.assert_not_called()


def test_is_safe_public_http_url_rejects_localhost_subdomain():
    """Localhost subdomains are rejected by SSRF guard."""
    assert ClientEnrichmentService._is_safe_public_http_url("https://app.localhost/x.jpg") is False


def test_ext_and_content_type_defaults_to_jpeg():
    """Unknown content types default to jpeg extension."""
    resp = MagicMock()
    resp.headers = {"content-type": "application/octet-stream"}
    ext, content_type = ClientEnrichmentService._ext_and_content_type_from_response(resp)
    assert ext == "jpg"
    assert content_type == "image/jpeg"


@pytest.mark.asyncio
async def test_maybe_store_profile_photo_skips_existing(monkeypatch):
    """Existing profile photo is not overwritten."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._download_profile_photo = AsyncMock()
    key = await svc._maybe_store_profile_photo_from_enrichment(
        enriched_profile={"personalInfo": {"profileUrl": "https://cdn.example.com/p.jpg"}},
        contact_id="c-1",
        organization_id="org-1",
        existing_profile_photo_url="contacts/org-1/c-1/profile_old.jpg",
    )
    assert key is None
    svc._download_profile_photo.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_store_profile_photo_unsafe_url(monkeypatch):
    """Unsafe profile URLs are skipped."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._download_profile_photo = AsyncMock()
    key = await svc._maybe_store_profile_photo_from_enrichment(
        enriched_profile={"personalInfo": {"profileUrl": "http://127.0.0.1/photo.jpg"}},
        contact_id="c-1",
        organization_id="org-1",
        existing_profile_photo_url=None,
    )
    assert key is None
    svc._download_profile_photo.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_and_store_sales_intelligence_invalid_request_id():
    """fetch_and_store_sales_intelligence ignores blank request ids."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    await svc.fetch_and_store_sales_intelligence_for_request("", enriched_company={"name": "Acme"})


@pytest.mark.asyncio
async def test_fetch_and_store_sales_intelligence_missing_company_payload():
    """fetch_and_store_sales_intelligence requires enriched company dict."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    await svc.fetch_and_store_sales_intelligence_for_request("req-1", enriched_company=None)


@pytest.mark.asyncio
async def test_enrich_person_delegates_to_post(monkeypatch):
    """enrich_person posts to /enrich endpoint."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._post = AsyncMock(return_value={"request_id": "req-2"})
    result = await svc.enrich_person({"name": "Jane"})
    assert result["request_id"] == "req-2"
    svc._post.assert_awaited_once_with("/enrich", {"name": "Jane"})


@pytest.mark.asyncio
async def test_enrich_company_delegates_to_post(monkeypatch):
    """enrich_company posts to /enrich/company endpoint."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._post = AsyncMock(return_value={"request_id": "req-3"})
    payload = {"company_name": "Acme"}
    result = await svc.enrich_company(payload)
    assert result["request_id"] == "req-3"
    svc._post.assert_awaited_once_with("/enrich/company", payload)


@pytest.mark.asyncio
async def test_fetch_company_logo_too_large_returns_none(monkeypatch):
    """Oversized logo.dev payloads are treated as non-fatal failures."""

    class _Resp:
        content = b"x" * (1024 * 1024 + 1)

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            del url
            return _Resp()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.app_settings",
        MagicMock(enrichment_service=MagicMock(logo_dev_key="tok", timeout_seconds=5.0)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.httpx.AsyncClient",
        lambda timeout: _Client(),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    url = await svc._fetch_company_logo_public_url_best_effort(
        company_id="co-1",
        payload_data={"name": "Acme"},
    )
    assert url is None


@pytest.mark.asyncio
async def test_store_sales_intelligence_persists(monkeypatch):
    """_store_sales_intelligence_for_company writes sales_intelligence to company."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._fetch_sales_intelligence = AsyncMock(return_value={"score": 90})
    mock_repo = MagicMock()
    mock_repo.update_company = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.CompaniesRepository",
        lambda conn: mock_repo,
    )
    await svc._store_sales_intelligence_for_company(
        company_id="co-1",
        organization_id="org-1",
        person_info={},
        company_info={"name": "Acme"},
        conn=MagicMock(),
    )
    mock_repo.update_company.assert_awaited_once()


def test_build_person_payload_fallback_name():
    """Person payload falls back to empty name when all identifiers missing."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    payload = svc._build_person_payload({})
    assert payload["name"] == ""


def test_build_company_payload_includes_optional_fields():
    """Company payload includes website, email, industry, and location when present."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    payload = svc._build_company_payload(
        "co-1",
        "org-1",
        {
            "name": "Acme",
            "email": "hi@acme.com",
            "industry": "Tech",
            "websites": [{"url": "https://acme.com"}],
            "addresses": [{"country": "US"}],
            "social_pages": [{"url": "https://linkedin.com/company/acme"}],
        },
    )
    assert payload["company_email"] == "hi@acme.com"
    assert payload["website_url"] == "https://acme.com"
    assert payload["industry"] == "Tech"
    assert payload["location"] == "US"


@pytest.mark.asyncio
async def test_download_profile_photo_success(monkeypatch):
    """_download_profile_photo returns bytes and content type from stream."""

    class _StreamResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"img-bytes"

        @property
        def headers(self):
            return {"content-type": "image/png"}

    class _Client:
        def stream(self, method, url):
            del method, url
            return _StreamResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service.httpx.AsyncClient",
        lambda **kwargs: _Client(),
    )
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    result = await svc._download_profile_photo(remote_url="https://cdn.example.com/p.jpg")
    assert result == (b"img-bytes", "png", "image/png")


@pytest.mark.asyncio
async def test_maybe_store_profile_photo_download_failure(monkeypatch):
    """Profile photo storage returns None when download fails."""
    svc = ClientEnrichmentService(base_url="http://e", webhook_url="http://w", timeout_seconds=5.0)
    svc._download_profile_photo = AsyncMock(side_effect=RuntimeError("network"))
    key = await svc._maybe_store_profile_photo_from_enrichment(
        enriched_profile={"personalInfo": {"profileUrl": "https://cdn.example.com/p.jpg"}},
        contact_id="c-1",
        organization_id="org-1",
        existing_profile_photo_url=None,
    )
    assert key is None
