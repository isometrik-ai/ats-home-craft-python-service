"""Unit tests for TypesenseIndexService document builders."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services import typesense_index_service as tis


def test_dedupe_string_list_fields():
    """String list fields are deduplicated in place."""
    doc = {"tags": ["a", "b", "a", None], "phones": [{"id": "1"}]}
    tis._dedupe_string_list_fields(doc)
    assert doc["tags"] == ["a", "b"]
    assert doc["phones"] == [{"id": "1"}]


def test_normalize_phone_entry_dict_and_literal():
    """Phone entries accept dicts and Python-literal strings."""
    assert tis._normalize_phone_entry({"phone_number": "123"}) == {"phone_number": "123"}
    parsed = tis._normalize_phone_entry("{'phone_number': '999'}")
    assert parsed == {"phone_number": "999"}
    assert tis._normalize_phone_entry("not-a-dict") is None


def test_build_contact_full_name():
    """Full name joins prefix, first, middle, and last."""
    name = tis._build_contact_full_name(
        {
            "prefix": "Dr.",
            "first_name": "Jane",
            "middle_name": "Q",
            "last_name": "Doe",
        }
    )
    assert name == "Dr. Jane Q Doe"


def test_extract_phone_numbers_and_display():
    """Phones extract E.164-ish numbers and display payloads."""
    numbers, display = tis._extract_phone_numbers_and_display(
        {
            "phones": [
                {"phone_number": "9876543210", "phone_isd_code": "+91"},
                "{'phone_number': '5551234', 'phone_isd_code': '+1'}",
            ]
        }
    )
    assert "+919876543210" in numbers
    assert "+15551234" in numbers
    assert len(display) == 2


def test_extract_contact_company_linkage():
    """Contact company linkage extracts ids and names."""
    ids, names = tis._extract_contact_company_linkage(
        {"companies": [{"company_id": "co-1", "name": "Acme"}]}
    )
    assert ids == ["co-1"]
    assert names == ["Acme"]


def test_extract_contact_skills_and_work_history():
    """Skills and work history facets parse JSON-like lists."""
    skills = tis._extract_contact_skills({"skills": ["Python", "  ", "Go"]})
    companies, titles = tis._extract_contact_work_history_facets(
        {"work_history": [{"company_name": "Acme", "title": "Engineer"}]}
    )
    assert skills == ["Python", "Go"]
    assert companies == ["Acme"]
    assert titles == ["Engineer"]


def test_extract_created_updated_timestamps():
    """Created/updated timestamps convert to unix seconds."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    created, updated = tis._extract_created_updated({"created_at_dt": ts, "updated_at_dt": ts})
    assert created == int(ts.timestamp())
    assert updated == int(ts.timestamp())


@pytest.mark.asyncio
async def test_build_contact_document(monkeypatch):
    """_build_contact_document validates and shapes contact docs."""

    async def fake_get_contact_details(self, *, contact_id: str, organization_id: str):
        del self, contact_id, organization_id
        return {
            "id": "c-1",
            "organization_id": "org-1",
            "status": "active",
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "Jane@Example.com",
            "phones": [],
            "companies": [],
            "tags": [],
            "created_at_dt": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at_dt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }

    async def fake_custom_facets(**_kwargs):
        return ([], [])

    monkeypatch.setattr(
        tis.ContactsRepository,
        "__init__",
        lambda self, conn: None,
    )
    monkeypatch.setattr(
        tis.ContactsRepository,
        "get_contact_details",
        fake_get_contact_details,
    )
    monkeypatch.setattr(
        tis,
        "_extract_contact_custom_field_facets",
        fake_custom_facets,
    )

    doc = await tis._build_contact_document(
        conn=MagicMock(),
        contact_id="c-1",
        organization_id="org-1",
    )
    assert doc is not None
    assert doc["id"] == "c-1"
    assert doc["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_index_contacts_background_upserts(monkeypatch):
    """index_contacts_background builds docs and bulk upserts."""

    async def fake_build(**_kwargs):
        return {"id": "c-1", "organization_id": "org-1", "full_name": "Jane Doe"}

    mock_typesense = MagicMock()
    mock_typesense.upsert_documents_bulk = AsyncMock()

    monkeypatch.setattr(tis, "_build_contact_document", fake_build)
    monkeypatch.setattr(
        tis.TypesenseService,
        "from_settings",
        lambda **kwargs: mock_typesense,
    )

    with patch.object(tis, "get_pool", AsyncMock(return_value=MagicMock())):
        with patch.object(tis, "AcquireConnection") as acquire:
            acquire.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            await tis.index_contacts_background([("c-1", "org-1")])

    mock_typesense.upsert_documents_bulk.assert_awaited_once()
    docs = mock_typesense.upsert_documents_bulk.await_args.args[0]
    assert docs[0]["id"] == "c-1"


@pytest.mark.asyncio
async def test_delete_contact_background_swallows_errors(monkeypatch):
    """delete_contact_background logs but does not raise on failure."""

    mock_typesense = MagicMock()
    mock_typesense.delete_document = AsyncMock(side_effect=RuntimeError("down"))

    monkeypatch.setattr(
        tis.TypesenseService,
        "from_settings",
        lambda **kwargs: mock_typesense,
    )

    await tis.delete_contact_background("c-1")
    mock_typesense.delete_document.assert_awaited_once_with("c-1")


def test_extract_contact_education_social_websites_addresses():
    """Education, social, websites, and address facets parse nested lists."""
    institutions, degrees = tis._extract_contact_education_facets(
        {
            "educational_history": [
                {"school": "MIT", "degree": "BS", "field_of_study": "CS"},
            ]
        }
    )
    assert institutions == ["MIT"]
    assert degrees == ["BS"]

    urls = tis._extract_contact_social_urls(
        {"social_pages": [{"url": "https://linkedin.com/in/jane"}]}
    )
    assert urls == ["https://linkedin.com/in/jane"]

    websites = tis._extract_contact_websites(
        {"additional_data": {"websites": ["https://jane.dev", {"url": "https://blog.dev"}]}}
    )
    assert "https://jane.dev" in websites
    assert "https://blog.dev" in websites

    cities, states, countries, postal = tis._extract_contact_address_facets(
        {
            "addresses": [
                {
                    "city": "SF",
                    "state": "CA",
                    "country": "US",
                    "postal_code": "94104",
                }
            ]
        }
    )
    assert cities == ["SF"]
    assert states == ["CA"]
    assert countries == ["US"]
    assert postal == ["94104"]


def test_extract_company_phone_and_contacts_fields():
    """Company phone and embedded contacts fields are extracted."""
    numbers, display = tis._extract_company_phone_numbers_and_display(
        {"phones": [{"phone_number": "555", "phone_isd_code": "+1"}]}
    )
    assert numbers == ["+1555"]
    assert len(display) == 1

    contacts, names, titles, emails, phones = tis._extract_company_contacts_fields(
        {
            "contacts": [
                {
                    "id": "p-1",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "title": "CEO",
                    "email": "Jane@Co.com",
                    "is_primary": True,
                    "phones": [{"phone_number": "999", "phone_isd_code": "+91"}],
                }
            ]
        }
    )
    assert contacts[0]["full_name"] == "Jane Doe"
    assert names == ["Jane Doe"]
    assert titles == ["CEO"]
    assert emails == ["jane@co.com"]
    assert "+91999" in phones


def test_extract_company_address_products_key_people():
    """Company address, product, and key-people facets parse correctly."""
    cities, states, countries, postal = tis._extract_company_address_facets(
        {"addresses": [{"city": "NYC", "region": "NY", "country": "US", "zip": "10001"}]}
    )
    assert cities == ["NYC"]
    assert states == ["NY"]
    assert countries == ["US"]
    assert postal == ["10001"]

    assert tis._extract_company_product_names({"products": [{"name": "Widget"}]}) == ["Widget"]
    assert tis._extract_company_key_people_names({"key_people": [{"name": "Alice"}]}) == ["Alice"]


@pytest.mark.asyncio
async def test_build_company_document(monkeypatch):
    """_build_company_document validates and shapes company docs."""

    async def fake_get_company_details(self, *, company_id: str, organization_id: str):
        del self, company_id, organization_id
        return {
            "id": "co-1",
            "organization_id": "org-1",
            "status": "active",
            "name": "Acme",
            "industry": "Tech",
            "email": "Hi@Acme.com",
            "phones": [],
            "contacts": [],
            "tags": [],
            "description": "Widgets",
            "created_at_dt": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updated_at_dt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }

    async def fake_custom_facets(**_kwargs):
        return (["tier"], ["gold"])

    monkeypatch.setattr(tis.CompaniesRepository, "__init__", lambda self, conn: None)
    monkeypatch.setattr(tis.CompaniesRepository, "get_company_details", fake_get_company_details)
    monkeypatch.setattr(tis, "_extract_company_custom_field_facets", fake_custom_facets)

    doc = await tis._build_company_document(
        conn=MagicMock(),
        company_id="co-1",
        organization_id="org-1",
    )
    assert doc is not None
    assert doc["id"] == "co-1"
    assert doc["email"] == "hi@acme.com"
    assert doc["custom_field_keys"] == ["tier"]


@pytest.mark.asyncio
async def test_index_companies_background_upserts(monkeypatch):
    """index_companies_background builds docs and bulk upserts."""

    async def fake_build(**_kwargs):
        return {"id": "co-1", "organization_id": "org-1", "name": "Acme"}

    mock_typesense = MagicMock()
    mock_typesense.upsert_documents_bulk = AsyncMock()

    monkeypatch.setattr(tis, "_build_company_document", fake_build)
    monkeypatch.setattr(tis.TypesenseService, "from_settings", lambda **kwargs: mock_typesense)

    with patch.object(tis, "get_pool", AsyncMock(return_value=MagicMock())):
        with patch.object(tis, "AcquireConnection") as acquire:
            acquire.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            await tis.index_companies_background([("co-1", "org-1")])

    mock_typesense.upsert_documents_bulk.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_company_background_swallows_errors(monkeypatch):
    """delete_company_background logs but does not raise on failure."""
    mock_typesense = MagicMock()
    mock_typesense.delete_document = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(tis.TypesenseService, "from_settings", lambda **kwargs: mock_typesense)
    await tis.delete_company_background("co-1")
    mock_typesense.delete_document.assert_awaited_once_with("co-1")


@pytest.mark.asyncio
async def test_index_contacts_background_skips_empty_refs():
    """index_contacts_background returns early for empty input."""
    await tis.index_contacts_background([])


@pytest.mark.asyncio
async def test_build_contact_document_returns_none_when_missing(monkeypatch):
    """_build_contact_document returns None when contact is not found."""

    async def fake_get_contact_details(self, *, contact_id: str, organization_id: str):
        del self, contact_id, organization_id
        return None

    monkeypatch.setattr(tis.ContactsRepository, "__init__", lambda self, conn: None)
    monkeypatch.setattr(tis.ContactsRepository, "get_contact_details", fake_get_contact_details)

    doc = await tis._build_contact_document(
        conn=MagicMock(),
        contact_id="missing",
        organization_id="org-1",
    )
    assert doc is None


def test_normalize_phone_entry_blank_string():
    """Blank phone strings normalize to None."""
    assert tis._normalize_phone_entry("   ") is None


def test_extract_phone_numbers_skips_blank_numbers():
    """Phone extraction ignores entries without dialable numbers."""
    numbers, display = tis._extract_phone_numbers_and_display(
        {"phones": [{"phone_number": ""}, {"phone_number": "555", "phone_isd_code": "+1"}]}
    )
    assert numbers == ["+1555"]
    assert len(display) == 2


def test_extract_company_phone_numbers_skips_invalid():
    """Company phone extraction ignores invalid list entries."""
    numbers, display = tis._extract_company_phone_numbers_and_display(
        {"phones": [{"phone_number": "555", "phone_isd_code": "+1"}, "x"]}
    )
    assert numbers == ["+1555"]
    assert len(display) == 1


def test_extract_created_updated_without_timestamps():
    """Missing timestamps default to zero."""
    created, updated = tis._extract_created_updated({})
    assert created == 0
    assert updated == 0


@pytest.mark.asyncio
async def test_extract_contact_custom_field_facets(monkeypatch):
    """Contact custom field facets delegate to CustomFieldService."""
    from apps.user_service.app.schemas.custom_fields import CustomFieldResponse

    async def fake_get_custom_fields_list(self, entity_type, *, organization_id):
        del self, entity_type, organization_id
        return (
            [
                CustomFieldResponse(
                    id="f1",
                    field_name="Tier",
                    field_key="tier",
                    description=None,
                    field_type="text",
                    show_on_create=True,
                    show_on_detail=False,
                    is_required=False,
                    type_config={},
                    sort_order=0,
                    is_active=True,
                    entity_type="contact",
                    parent_id=None,
                    sub_fields=[],
                )
            ],
            1,
        )

    monkeypatch.setattr(
        tis.CustomFieldService,
        "get_custom_fields_list",
        fake_get_custom_fields_list,
    )
    monkeypatch.setattr(
        tis.CustomFieldService,
        "field_cells_typesense_facets",
        lambda cells, id_to_def: (["tier"], ["gold"]),
    )

    keys, values = await tis._extract_contact_custom_field_facets(
        conn=MagicMock(),
        organization_id="org-1",
        details={"custom_fields": [{"field_id": "f1", "value": "gold"}]},
    )
    assert keys == ["tier"]
    assert values == ["gold"]


@pytest.mark.asyncio
async def test_extract_company_custom_field_facets_empty():
    """Company custom field facets return empty lists when no cells."""
    keys, values = await tis._extract_company_custom_field_facets(
        conn=MagicMock(),
        organization_id="org-1",
        details={"custom_fields": []},
    )
    assert keys == []
    assert values == []


@pytest.mark.asyncio
async def test_index_contacts_background_skips_failed_docs(monkeypatch):
    """index_contacts_background logs build failures and skips bad docs."""

    async def fake_build(**kwargs):
        if kwargs.get("contact_id") == "bad":
            raise RuntimeError("build failed")
        return {"id": kwargs.get("contact_id"), "organization_id": "org-1"}

    mock_typesense = MagicMock()
    mock_typesense.upsert_documents_bulk = AsyncMock()

    monkeypatch.setattr(tis, "_build_contact_document", fake_build)
    monkeypatch.setattr(tis.TypesenseService, "from_settings", lambda **kwargs: mock_typesense)

    with patch.object(tis, "get_pool", AsyncMock(return_value=MagicMock())):
        with patch.object(tis, "AcquireConnection") as acquire:
            acquire.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            await tis.index_contacts_background([("good", "org-1"), ("bad", "org-1")])

    docs = mock_typesense.upsert_documents_bulk.await_args.args[0]
    assert len(docs) == 1
    assert docs[0]["id"] == "good"


@pytest.mark.asyncio
async def test_extract_company_custom_field_facets(monkeypatch):
    """Company custom field facets delegate to CustomFieldService."""
    from apps.user_service.app.schemas.custom_fields import CustomFieldResponse

    async def fake_get_custom_fields_list(self, entity_type, *, organization_id):
        del self, entity_type, organization_id
        return (
            [
                CustomFieldResponse(
                    id="f1",
                    field_name="Segment",
                    field_key="segment",
                    description=None,
                    field_type="text",
                    show_on_create=True,
                    show_on_detail=False,
                    is_required=False,
                    type_config={},
                    sort_order=0,
                    is_active=True,
                    entity_type="company",
                    parent_id=None,
                    sub_fields=[],
                )
            ],
            1,
        )

    monkeypatch.setattr(
        tis.CustomFieldService,
        "get_custom_fields_list",
        fake_get_custom_fields_list,
    )
    monkeypatch.setattr(
        tis.CustomFieldService,
        "field_cells_typesense_facets",
        lambda cells, id_to_def: (["segment"], ["enterprise"]),
    )

    keys, values = await tis._extract_company_custom_field_facets(
        conn=MagicMock(),
        organization_id="org-1",
        details={"custom_fields": [{"field_id": "f1", "value": "enterprise"}]},
    )
    assert keys == ["segment"]
    assert values == ["enterprise"]


def test_extract_company_phone_numbers_non_list():
    """Company phone extraction returns empty lists when phones is not a list."""
    numbers, display = tis._extract_company_phone_numbers_and_display({"phones": {"bad": "shape"}})
    assert numbers == []
    assert display == []


def test_extract_contact_phone_numbers_non_list():
    """Contact phone extraction returns empty lists when phones is not a list."""
    numbers, display = tis._extract_phone_numbers_and_display({"phones": {"bad": "shape"}})
    assert numbers == []
    assert display == []


def test_normalize_phone_entry_invalid_literal():
    """Invalid string phone entries normalize to None."""
    assert tis._normalize_phone_entry("{not valid}") is None


@pytest.mark.asyncio
async def test_extract_contact_custom_field_facets_empty_cells():
    """Contact custom field facets return empty when cells are not a list."""
    keys, values = await tis._extract_contact_custom_field_facets(
        conn=MagicMock(),
        organization_id="org-1",
        details={"custom_fields": "bad"},
    )
    assert keys == []
    assert values == []


def test_extract_contact_company_linkage_skips_invalid_entries():
    """Contact company linkage ignores non-dict entries."""
    ids, names = tis._extract_contact_company_linkage(
        {"companies": ["bad", {"company_id": "co-1", "name": "Acme"}]}
    )
    assert ids == ["co-1"]
    assert names == ["Acme"]


@pytest.mark.asyncio
async def test_index_companies_background_skips_empty_refs():
    """index_companies_background returns early for empty input."""
    await tis.index_companies_background([])


@pytest.mark.asyncio
async def test_index_companies_background_skips_failed_docs(monkeypatch):
    """index_companies_background logs build failures and skips bad docs."""

    async def fake_build(**kwargs):
        if kwargs.get("company_id") == "bad":
            raise RuntimeError("build failed")
        return {"id": kwargs.get("company_id"), "organization_id": "org-1", "name": "Acme"}

    mock_typesense = MagicMock()
    mock_typesense.upsert_documents_bulk = AsyncMock()

    monkeypatch.setattr(tis, "_build_company_document", fake_build)
    monkeypatch.setattr(tis.TypesenseService, "from_settings", lambda **kwargs: mock_typesense)

    with patch.object(tis, "get_pool", AsyncMock(return_value=MagicMock())):
        with patch.object(tis, "AcquireConnection") as acquire:
            acquire.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            await tis.index_companies_background([("good", "org-1"), ("bad", "org-1")])

    docs = mock_typesense.upsert_documents_bulk.await_args.args[0]
    assert len(docs) == 1
    assert docs[0]["id"] == "good"


def test_extract_contact_skills_skips_non_strings():
    """Skills extraction ignores non-string entries."""
    assert tis._extract_contact_skills({"skills": ["Python", 42, ""]}) == ["Python"]


def test_extract_work_history_alternate_field_names():
    """Work history accepts company/title alias keys."""
    companies, titles = tis._extract_contact_work_history_facets(
        {"work_history": [{"company": "Acme", "position": "Engineer"}]}
    )
    assert companies == ["Acme"]
    assert titles == ["Engineer"]


def test_extract_education_institution_aliases():
    """Education facets parse institution/university aliases."""
    institutions, degrees = tis._extract_contact_education_facets(
        {"educational_history": [{"university": "MIT", "qualification": "MS"}]}
    )
    assert institutions == ["MIT"]
    assert degrees == ["MS"]


def test_extract_contact_social_urls_link_alias():
    """Social URL extraction accepts link/profile_url aliases."""
    urls = tis._extract_contact_social_urls(
        {"social_pages": [{"link": "https://linkedin.com/in/jane"}]}
    )
    assert urls == ["https://linkedin.com/in/jane"]


def test_extract_contact_websites_string_and_dict_items():
    """Website extraction accepts plain strings and dict entries."""
    websites = tis._extract_contact_websites(
        {"additional_data": {"websites": ["https://a.com", {"website": "https://b.com"}]}}
    )
    assert "https://a.com" in websites
    assert "https://b.com" in websites


def test_extract_contact_address_zip_aliases():
    """Address facets accept zip_code and zip aliases."""
    cities, states, countries, postal = tis._extract_contact_address_facets(
        {"addresses": [{"city": "NYC", "region": "NY", "country": "US", "zip": "10001"}]}
    )
    assert postal == ["10001"]
    assert states == ["NY"]


def test_extract_company_contacts_skips_invalid_entries():
    """Company embedded contacts ignore non-dict rows."""
    contacts, names, titles, emails, phones = tis._extract_company_contacts_fields(
        {"contacts": ["bad", {"first_name": "Jane", "last_name": "Doe", "email": "j@co.com"}]}
    )
    assert len(contacts) == 1
    assert names == ["Jane Doe"]
    assert emails == ["j@co.com"]


@pytest.mark.asyncio
async def test_build_company_document_returns_none_when_missing(monkeypatch):
    """_build_company_document returns None when company is not found."""

    async def fake_get_company_details(self, *, company_id: str, organization_id: str):
        del self, company_id, organization_id
        return None

    monkeypatch.setattr(tis.CompaniesRepository, "__init__", lambda self, conn: None)
    monkeypatch.setattr(tis.CompaniesRepository, "get_company_details", fake_get_company_details)

    doc = await tis._build_company_document(
        conn=MagicMock(),
        company_id="missing",
        organization_id="org-1",
    )
    assert doc is None


def test_extract_helpers_skip_non_dict_rows():
    """Facet helpers ignore malformed rows in list fields."""
    assert tis._extract_contact_work_history_facets({"work_history": ["bad"]}) == ([], [])
    assert tis._extract_contact_education_facets({"educational_history": [None]}) == ([], [])
    assert tis._extract_contact_social_urls({"social_pages": [123]}) == []
    assert tis._extract_contact_websites({"additional_data": {"websites": ["", {"url": ""}]}}) == []
    cities, _, _, postal = tis._extract_contact_address_facets({"addresses": ["bad"]})
    assert cities == [] and postal == []
    assert tis._extract_company_product_names({"products": ["bad"]}) == []
    assert tis._extract_company_key_people_names({"key_people": [None]}) == []


def test_extract_company_phone_skips_blank_number():
    """Company phone extraction skips entries without numbers."""
    numbers, display = tis._extract_company_phone_numbers_and_display(
        {"phones": ["{'phone_number': ''}", {"phone_number": "555", "phone_isd_code": "+1"}]}
    )
    assert numbers == ["+1555"]
    assert len(display) == 2


def test_extract_contact_phone_skips_unparseable_string_entry():
    """Contact phone extraction skips string entries that cannot be parsed."""
    numbers, display = tis._extract_phone_numbers_and_display(
        {"phones": ["not-a-phone-dict", {"phone_number": "555", "phone_isd_code": "+1"}]}
    )
    assert numbers == ["+1555"]
    assert len(display) == 1


def test_extract_contact_websites_skips_unknown_item_types():
    """Website extraction ignores unsupported item types."""
    websites = tis._extract_contact_websites(
        {"additional_data": {"websites": [123, {"link": "https://c.com"}]}}
    )
    assert websites == ["https://c.com"]
