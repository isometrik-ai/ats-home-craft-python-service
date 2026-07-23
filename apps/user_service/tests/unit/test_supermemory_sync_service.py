"""Unit tests for Supermemory CRM sync helpers."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import (
    ClientEventType,
    CompanyEventType,
    ContactEventType,
)
from apps.user_service.app.schemas.enums import EntityType as CustomFieldEntityType
from apps.user_service.app.schemas.enums import (
    LeadEventType,
)
from apps.user_service.app.services.supermemory_sync_service import (
    _build_contact_content,
    _contact_address_lines,
    _custom_field_value_display,
    _format_education_bullets,
    _format_note_bullets,
    _format_phone_bullets,
    _format_resolved_custom_field_bullets,
    _format_scalar_date,
    _format_social_bullets,
    _format_website_bullets,
    _format_work_history_bullets,
    _linked_company_lines,
    _linked_lead_lines,
    _normalize_tags,
    _optional_kv_line,
    _parse_json_dict,
    _parse_json_list,
    _prepare_company_row,
    _prepare_contact_row,
    _resolve_entity_custom_fields_for_snapshot,
    _section,
    _tags_csv,
    _unix_ts,
    custom_id_for_entity,
    resolve_sync_targets,
)
from libs.shared_utils.graphiti_service import container_tag_for_organization


def test_container_tag_for_organization() -> None:
    """Organization id maps to container tag prefix."""
    assert container_tag_for_organization("abc-123") == "org_abc-123"


def test_custom_id_for_entity() -> None:
    """Entity type and id form stable custom id."""
    assert custom_id_for_entity("contact", "c1") == "crm:contact:c1"


def test_resolve_sync_targets_contact_updated() -> None:
    """Contact updated events map to contact target."""
    targets = resolve_sync_targets(
        event_type=ContactEventType.UPDATED.value,
        aggregate_id="contact-1",
        payload={"module": "contacts"},
    )
    assert targets == [("contact", "contact-1")]


def test_resolve_sync_targets_company_created() -> None:
    """Company created events map to company target."""
    targets = resolve_sync_targets(
        event_type=CompanyEventType.CREATED.value,
        aggregate_id="company-1",
        payload=None,
    )
    assert targets == [("company", "company-1")]


def test_resolve_sync_targets_lead_deleted() -> None:
    """Lead deleted events map to lead target."""
    targets = resolve_sync_targets(
        event_type=LeadEventType.DELETED.value,
        aggregate_id="lead-1",
        payload=None,
    )
    assert targets == [("lead", "lead-1")]


def test_resolve_sync_skips_enrichment_req() -> None:
    """Enrichment-requested events do not enqueue sync targets."""
    targets = resolve_sync_targets(
        event_type=ContactEventType.ENRICHMENT_REQUESTED.value,
        aggregate_id="contact-1",
        payload=None,
    )
    assert not targets


def test_normalize_tags_dedupes_and_strips() -> None:
    """Tags are normalized, deduplicated case-insensitively, and trimmed."""
    assert _normalize_tags([" VIP ", "vip", "Referral", ""]) == ["VIP", "Referral"]


def test_tags_csv_joins_for_metadata() -> None:
    """Tags list becomes comma-separated metadata string."""
    assert _tags_csv(["VIP", "Referral"]) == "VIP,Referral"


def test_resolve_sync_targets_client_created_company() -> None:
    """Client created with company client_type maps to company."""
    targets = resolve_sync_targets(
        event_type=ClientEventType.CREATED.value,
        aggregate_id="company-1",
        payload={"client_type": "companies"},
    )
    assert targets == [("company", "company-1")]


def test_prepare_contact_row_parses_stringified_phones() -> None:
    """JSON string columns for phones and notes are parsed to structures."""
    row = _prepare_contact_row(
        {
            "phones": (
                '[{"phone_number": "9510314715", "phone_isd_code": "+91", '
                '"label": "mobile", "is_primary": true}]'
            ),
            "notes": '[{"title": "Intake", "content": "Follow up"}]',
        }
    )
    phones_raw = row["phones"]
    notes_raw = row["notes"]
    assert isinstance(phones_raw, list) and len(phones_raw) == 1
    assert isinstance(notes_raw, list) and len(notes_raw) == 1
    phone_entry = phones_raw[0]
    note_entry = notes_raw[0]
    assert isinstance(phone_entry, dict)
    assert isinstance(note_entry, dict)
    phone_dict = dict(phone_entry)
    note_dict = dict(note_entry)
    assert phone_dict["phone_number"] == "9510314715"
    assert note_dict["title"] == "Intake"


def test_format_phone_bullets_human_readable() -> None:
    """Phone list is formatted with ISD, primary flag, and label."""
    details = _prepare_contact_row(
        {
            "phones": [
                {
                    "phone_number": "9510314715",
                    "phone_isd_code": "+91",
                    "label": "mobile",
                    "is_primary": True,
                }
            ]
        }
    )
    lines = _format_phone_bullets(details)
    assert lines == ["+919510314715 (primary) — mobile"]


def test_format_note_bullets() -> None:
    """Note dicts become title: content lines."""
    lines = _format_note_bullets([{"title": "Intake", "content": "Follow up next week"}])
    assert lines == ["Intake: Follow up next week"]


def test_custom_field_value_display_uses_labels() -> None:
    """Resolved custom fields render yes/no and dropdown values with labels."""
    lines = _format_resolved_custom_field_bullets(
        [
            {
                "label": "Health checkup",
                "field_key": "health_checkup",
                "type": "yes_no",
                "value": True,
            },
            {
                "label": "Insurance Company",
                "field_key": "insurance_company",
                "type": "dropdown",
                "value": "ICICI",
            },
        ]
    )
    assert lines == ["Health checkup: Yes", "Insurance Company: ICICI"]


def test_contact_content_has_dob_notes_sites() -> None:
    """Contact markdown snapshot includes profile, notes, and websites sections."""
    content = _build_contact_content(
        _prepare_contact_row(
            {
                "id": "c1",
                "first_name": "Rohit",
                "last_name": "Marthak",
                "email": "rohit@appscrip.co",
                "title": "Engineer",
                "date_of_birth": "2004-06-28",
                "status": "active",
                "enrichment_done": False,
                "notes": [{"title": "Intake", "content": "Met at summit"}],
                "additional_data": {
                    "websites": [{"url": "https://example.com", "type": "personal"}],
                    "preferred_language": "en",
                },
                "tags": ["vip"],
                "companies": [],
                "leads": [],
                "addresses": [],
                "phones": [],
            }
        ),
        custom_field_lines=["Health checkup: Yes"],
    )
    assert "Date of birth: 2004-06-28" in content
    assert "## Notes" in content
    assert "Intake: Met at summit" in content
    assert "## Websites" in content
    assert "https://example.com" in content
    assert "Preferred language: en" in content
    assert "Health checkup: Yes" in content
    assert "```json" not in content


def test_optional_kv_line_omits_empty() -> None:
    """Test that optional_kv_line returns None for empty values."""
    assert _optional_kv_line("Title", None) is None
    assert _optional_kv_line("Title", "") is None
    assert _optional_kv_line("Title", "   ") is None
    assert _optional_kv_line("Title", "Engineer") == "Title: Engineer"


def test_contact_content_skips_empty_profile() -> None:
    """Empty optional profile keys are omitted from contact markdown."""
    content = _build_contact_content(
        _prepare_contact_row(
            {
                "id": "c1",
                "first_name": "A",
                "last_name": "B",
                "email": "a@x.com",
                "status": "active",
                "companies": [],
                "leads": [],
                "addresses": [],
            }
        )
    )
    assert "n/a" not in content
    assert "Title:" not in content
    assert "Date of birth:" not in content


def test_resolve_sync_targets_client_created_contact() -> None:
    """Client created with contacts client_type maps to contact."""
    targets = resolve_sync_targets(
        event_type=ClientEventType.CREATED.value,
        aggregate_id="contact-1",
        payload={"client_type": "contacts"},
    )
    assert targets == [("contact", "contact-1")]


def test_resolve_sync_targets_unknown_event() -> None:
    """Unknown events return empty target list."""
    assert resolve_sync_targets(event_type="noop", aggregate_id="x", payload={}) == []


def test_unix_ts_from_datetime_and_iso() -> None:
    """Timestamps coerce from datetime objects and ISO strings."""
    dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert _unix_ts(dt) == int(dt.timestamp())
    assert _unix_ts("2024-01-15T12:00:00Z") == int(dt.timestamp())
    assert _unix_ts(None) == 0
    assert _unix_ts("not-a-date") == 0


def test_section_and_empty_body() -> None:
    """Markdown sections omit blank bodies."""
    assert _section("Profile", "hello") == "## Profile\nhello\n\n"
    assert _section("Profile", "   ") == ""


def test_optional_kv_line_yes_no_and_numeric() -> None:
    """Optional kv supports yes/no and numeric values."""
    assert _optional_kv_line("Active", True, yes_no=True) == "Active: Yes"
    assert _optional_kv_line("Score", 42) == "Score: 42"
    assert _optional_kv_line("Flag", True) == "Flag: True"


def test_normalize_tags_non_list_and_non_string_items() -> None:
    """Non-list tags and invalid items are ignored."""
    assert _normalize_tags('["a", "b"]') == ["a", "b"]
    assert _normalize_tags({"bad": True}) == []
    assert _normalize_tags([1, " valid "]) == ["valid"]


def test_tags_csv_empty() -> None:
    """Empty tags list yields empty csv."""
    assert _tags_csv([]) == ""


def test_parse_json_helpers() -> None:
    """JSON list/dict helpers handle strings and invalid values."""
    assert _parse_json_list('[{"a": 1}]') == [{"a": 1}]
    assert _parse_json_list(None) == []
    assert _parse_json_list(["already", "list"]) == ["already", "list"]
    assert _parse_json_dict('{"k": "v"}') == {"k": "v"}
    assert _parse_json_dict(None) == {}


def test_format_scalar_date() -> None:
    """Dates format from date objects and strings."""
    assert _format_scalar_date(datetime(2024, 6, 28)) == "2024-06-28"
    assert _format_scalar_date("2024-06-28") == "2024-06-28"
    assert _format_scalar_date(None) == ""


def test_format_website_and_social_bullets() -> None:
    """Websites and social links render as bullet lines."""
    details = _prepare_contact_row(
        {
            "additional_data": {
                "websites": [{"url": "https://a.com", "type": "work", "is_primary": True}]
            },
            "social_pages": [{"platform": "LinkedIn", "url": "https://linkedin.com/in/x"}],
        }
    )
    websites = _format_website_bullets(details)
    social = _format_social_bullets(details)
    assert any("https://a.com" in line for line in websites)
    assert any("LinkedIn" in line for line in social)


def test_format_work_history_and_education() -> None:
    """Work history and education entries become readable bullets."""
    work = _format_work_history_bullets(
        [{"company_name": "Acme", "title": "Engineer", "current": True}]
    )
    education = _format_education_bullets(
        [{"degree": "BSc", "institution": "MIT"}, {"school": "Local High"}]
    )
    assert work == ["Engineer works at Acme"]
    assert "BSc, MIT" in education
    assert "Local High" in education


def test_custom_field_value_display_variants() -> None:
    """Custom field nodes render stale, composite, and multi-value shapes."""
    assert _custom_field_value_display({"_stale": True, "old_value": "old"}) == (
        "(outdated value) old"
    )
    composite = _custom_field_value_display({"sub_fields": [{"label": "City", "value": "NYC"}]})
    multi = _custom_field_value_display({"items": [{"value": "A"}, {"value": "B"}]})
    assert composite == "City: NYC"
    assert multi == "A; B"


def test_format_resolved_custom_field_bullets_skips_empty() -> None:
    """Empty custom field values are omitted."""
    lines = _format_resolved_custom_field_bullets([{"label": "Empty", "value": ""}, "not-a-dict"])
    assert lines == []


def test_linked_company_and_lead_lines() -> None:
    """Linked entities format with primary flags and stage/amount."""
    companies = _linked_company_lines([{"name": "Acme", "industry": "Tech", "is_primary": True}])
    leads = _linked_lead_lines([{"name": "Deal", "stage_name": "Won", "amount": 1000}])
    assert companies == ["Acme (primary) — Tech"]
    assert leads == ["Deal — stage: Won — amount: 1000"]


def test_contact_address_lines() -> None:
    """Addresses join parts and mark primary."""
    lines = _contact_address_lines(
        [
            {
                "address_line1": "1 Main",
                "city": "NYC",
                "is_primary": True,
            }
        ]
    )
    assert lines == ["1 Main, NYC (primary)"]


def test_prepare_company_row_parses_json_columns() -> None:
    """Company row normalizes JSON string columns."""
    row = _prepare_company_row({"phones": "[]", "websites": '[{"url": "https://co.com"}]'})
    assert isinstance(row["phones"], list)
    assert isinstance(row["websites"], list)
    assert isinstance(row["additional_data"], dict)


def test_contact_content_includes_linked_sections_and_updated_at() -> None:
    """Contact markdown includes companies, leads, skills, and updated timestamp."""
    content = _build_contact_content(
        _prepare_contact_row(
            {
                "id": "c1",
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "status": "active",
                "companies": [{"name": "Acme", "industry": "SaaS"}],
                "leads": [{"name": "Big Deal", "stage_name": "Open"}],
                "addresses": [{"address_line1": "42 Road", "city": "LA"}],
                "skills": ["Python", "SQL"],
                "work_history": [{"company_name": "OldCo", "title": "Dev", "current": False}],
                "educational_history": [{"degree": "MS", "university": "State U"}],
                "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            }
        )
    )
    assert "## Companies" in content
    assert "## Linked leads" in content
    assert "## Skills" in content
    assert "## Work history" in content
    assert "Last updated (DB):" in content


def test_format_note_bullets_title_only() -> None:
    """Notes with only title or content still render."""
    assert _format_note_bullets([{"title": "Follow up", "content": ""}]) == ["Follow up"]
    assert _format_note_bullets([{"title": "", "content": "Call back"}]) == ["Call back"]


def test_format_phone_bullets_skips_empty_number() -> None:
    """Phones without numbers are omitted."""
    details = _prepare_contact_row({"phones": [{"phone_number": "", "label": "home"}]})
    assert _format_phone_bullets(details) == []


def test_custom_field_stale_without_old_value() -> None:
    """Stale custom fields without old_value show generic outdated marker."""
    assert _custom_field_value_display({"_stale": True}) == "(outdated)"


def test_linked_lead_lines_empty_bits_skipped() -> None:
    """Lead lines omit rows with no displayable fields."""
    assert _linked_lead_lines([{"name": "", "stage_name": ""}]) == []


def test_contact_address_lines_skips_non_dict() -> None:
    """Non-dict address entries are ignored."""
    assert _contact_address_lines(["bad", {"city": "LA"}]) == ["LA"]


@pytest.mark.asyncio
async def test_resolve_entity_custom_fields_for_snapshot() -> None:
    """Custom fields are resolved via CustomFieldService."""
    conn = MagicMock()
    stored = [{"field_id": "f1", "value": "x"}]
    resolved_node = {"label": "Field", "value": "x"}

    with patch(
        "apps.user_service.app.services.supermemory_sync_service.CustomFieldService"
    ) as cfs_cls:
        cfs = cfs_cls.return_value
        cfs.get_custom_fields_list = AsyncMock(return_value=([MagicMock(id="f1")], None))
        cfs.resolve_fields_for_read = MagicMock(return_value=[resolved_node, "skip"])

        result = await _resolve_entity_custom_fields_for_snapshot(
            conn,
            organization_id="org-1",
            entity_type=CustomFieldEntityType.CONTACT,
            stored_custom_fields=stored,
        )

    assert result == [resolved_node]
    cfs.get_custom_fields_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_entity_custom_fields_empty_roots() -> None:
    """Empty stored custom fields skip service lookup."""
    conn = MagicMock()
    result = await _resolve_entity_custom_fields_for_snapshot(
        conn,
        organization_id="org-1",
        entity_type=CustomFieldEntityType.CONTACT,
        stored_custom_fields=[],
    )
    assert result == []
