"""Unit tests for Supermemory CRM sync helpers."""

from apps.user_service.app.schemas.enums import (
    ClientEventType,
    CompanyEventType,
    ContactEventType,
    LeadEventType,
)
from apps.user_service.app.services.supermemory_sync_service import (
    _build_contact_content,
    _format_note_bullets,
    _format_phone_bullets,
    _format_resolved_custom_field_bullets,
    _normalize_tags,
    _optional_kv_line,
    _prepare_contact_row,
    _tags_csv,
    container_tag_for_organization,
    custom_id_for_entity,
    resolve_sync_targets,
)


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
