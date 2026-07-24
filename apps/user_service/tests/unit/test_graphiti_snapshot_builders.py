"""Unit tests for Graphiti snapshot builder helpers."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Importing graphiti_snapshot_builders loads user_service.services package init.
sys.modules.setdefault("aiokafka", MagicMock())

from libs.shared_utils.graphiti_crm_models import (  # noqa: E402
    CompanySnapshot,
    ContactSnapshot,
    LeadSnapshot,
)
from libs.shared_utils.graphiti_snapshot_builders import (  # noqa: E402
    _addresses_from_prepared,
    _base_metadata,
    _custom_fields_from_resolved,
    _linked_companies_from_prepared,
    _linked_leads_from_prepared,
    _notes_from_raw,
    _phones_from_display,
    _social_pages_from_prepared,
    _tombstone_snapshot,
    _websites_from_additional,
    build_company_snapshot,
    build_contact_snapshot,
    build_lead_snapshot,
)


def test_base_metadata_truncates_long_fields() -> None:
    """Metadata should truncate display name, email, and tag fields."""
    metadata = _base_metadata(
        entity_type="contact",
        entity_id="c-1",
        organization_id="org-1",
        status="active",
        display_name="x" * 300,
        primary_email="e" * 400,
        updated_at=123,
        related_company_ids="a" * 3000,
        related_contact_ids="b" * 3000,
        tags="t" * 3000,
    )

    assert len(metadata.display_name) == 200
    assert len(metadata.primary_email) == 320
    assert len(metadata.related_company_ids) == 2000
    assert metadata.schema_version >= 1
    assert metadata.source == "crm"


def test_phones_from_display_skips_non_dict_entries() -> None:
    """Phone parser should ignore invalid entries."""
    phones = _phones_from_display(
        [
            {"phone_number": "555", "phone_isd_code": "+1", "label": "mobile", "is_primary": True},
            "bad",
        ]
    )

    assert len(phones) == 1
    assert phones[0].phone_number == "555"
    assert phones[0].is_primary is True


def test_notes_from_raw_parses_titles_and_content() -> None:
    """Notes parser should extract title and content from JSON list."""
    notes = _notes_from_raw([{"title": " Follow up ", "content": " Call back "}])

    assert len(notes) == 1
    assert notes[0].title == "Follow up"
    assert notes[0].content == "Call back"


def test_custom_fields_from_resolved_validates_dict_nodes() -> None:
    """Resolved custom fields should validate dict nodes only."""
    resolved = _custom_fields_from_resolved(
        [
            {"label": "Industry", "field_key": "industry", "value": "Tech"},
            "skip-me",
        ]
    )

    assert len(resolved) == 1
    assert resolved[0].label == "Industry"
    assert resolved[0].field_key == "industry"


def test_tombstone_snapshot_variants() -> None:
    """Tombstones should build entity-specific snapshot models."""
    contact = _tombstone_snapshot(
        entity_type="contact",
        entity_id="c-1",
        organization_id="org-1",
        display_name="Deleted contact",
    )
    company = _tombstone_snapshot(
        entity_type="company",
        entity_id="co-1",
        organization_id="org-1",
        display_name="Deleted company",
    )
    lead = _tombstone_snapshot(
        entity_type="lead",
        entity_id="l-1",
        organization_id="org-1",
        display_name="Deleted lead",
    )

    assert isinstance(contact, ContactSnapshot)
    assert isinstance(company, CompanySnapshot)
    assert isinstance(lead, LeadSnapshot)
    assert contact.metadata.status == "deleted"
    assert company.metadata.status == "deleted"
    assert lead.metadata.status == "deleted"


def test_linked_companies_from_prepared() -> None:
    """Linked companies should map id/company_id and primary flag."""
    linked = _linked_companies_from_prepared(
        {
            "companies": [
                {
                    "id": "co-1",
                    "name": "Acme",
                    "industry": "Tech",
                    "is_primary": True,
                }
            ]
        }
    )

    assert len(linked) == 1
    assert linked[0].company_id == "co-1"
    assert linked[0].name == "Acme"
    assert linked[0].is_primary is True


def test_linked_leads_from_prepared() -> None:
    """Linked leads should map stage and amount fields."""
    linked = _linked_leads_from_prepared(
        {
            "leads": [
                {
                    "id": "lead-1",
                    "name": "Deal",
                    "stage_name": "Qualified",
                    "stage_id": "stage-1",
                    "amount": 1000,
                }
            ]
        }
    )

    assert len(linked) == 1
    assert linked[0].lead_id == "lead-1"
    assert linked[0].stage_name == "Qualified"
    assert linked[0].amount == 1000


def test_addresses_from_prepared() -> None:
    """Address parser should map core address fields."""
    addresses = _addresses_from_prepared(
        {
            "addresses": [
                {
                    "address_line1": "1 Main",
                    "city": "Austin",
                    "state": "TX",
                    "country": "US",
                    "postal_code": "78701",
                    "is_primary": True,
                }
            ]
        }
    )

    assert len(addresses) == 1
    assert addresses[0].city == "Austin"
    assert addresses[0].is_primary is True


def test_websites_from_additional() -> None:
    """Website parser should read url/type from additional_data."""
    websites = _websites_from_additional(
        {"websites": [{"url": " https://example.com ", "type": "work", "is_primary": True}]}
    )

    assert len(websites) == 1
    assert websites[0].url == "https://example.com"
    assert websites[0].type == "work"


def test_social_pages_from_prepared() -> None:
    """Social page parser should accept url or link keys."""
    pages = _social_pages_from_prepared(
        {"social_pages": [{"platform": "linkedin", "link": "https://linkedin.com/in/test"}]}
    )

    assert len(pages) == 1
    assert pages[0].platform == "linkedin"
    assert pages[0].url == "https://linkedin.com/in/test"


@pytest.mark.asyncio
async def test_build_contact_snapshot_missing_returns_tombstone() -> None:
    """Missing contacts should return a tombstone snapshot."""
    db_connection = MagicMock()
    with patch("libs.shared_utils.graphiti_snapshot_builders.ContactsRepository") as repo_cls:
        repo_cls.return_value.get_contact_details = AsyncMock(return_value=None)

        snapshot = await build_contact_snapshot(
            db_connection,
            organization_id="org-1",
            contact_id="missing",
        )

    assert isinstance(snapshot, ContactSnapshot)
    assert snapshot.display_name == "Deleted contact"
    assert snapshot.metadata.status == "deleted"


@pytest.mark.asyncio
async def test_build_contact_snapshot_deleted_status_returns_tombstone() -> None:
    """Deleted contacts should return a tombstone with the resolved name."""
    db_connection = MagicMock()
    with (
        patch("libs.shared_utils.graphiti_snapshot_builders.ContactsRepository") as repo_cls,
        patch(
            "libs.shared_utils.graphiti_snapshot_builders._build_contact_full_name",
            return_value="Jane Doe",
        ),
    ):
        repo_cls.return_value.get_contact_details = AsyncMock(
            return_value={"status": "deleted", "first_name": "Jane", "last_name": "Doe"}
        )

        snapshot = await build_contact_snapshot(
            db_connection,
            organization_id="org-1",
            contact_id="c-1",
        )

    assert snapshot.display_name == "Jane Doe"
    assert snapshot.metadata.status == "deleted"


@pytest.mark.asyncio
async def test_build_company_snapshot_active_builds_full_snapshot() -> None:
    """Active companies should build a populated company snapshot."""
    db_connection = MagicMock()
    updated_at = datetime(2026, 7, 1, tzinfo=UTC)
    with (
        patch("libs.shared_utils.graphiti_snapshot_builders.CompaniesRepository") as repo_cls,
        patch(
            "libs.shared_utils.graphiti_snapshot_builders._prepare_company_row",
            return_value={"custom_fields": [], "contacts": [], "addresses": [], "notes": []},
        ),
        patch(
            "libs.shared_utils.graphiti_snapshot_builders._resolve_entity_custom_fields_for_snapshot",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "libs.shared_utils.graphiti_snapshot_builders._extract_company_phone_numbers_and_display",
            return_value=([], []),
        ),
        patch(
            "libs.shared_utils.graphiti_snapshot_builders._normalize_tags",
            return_value=["vip"],
        ),
    ):
        repo_cls.return_value.get_company_details = AsyncMock(
            return_value={
                "status": "active",
                "name": "Acme Corp",
                "email": "info@acme.com",
                "updated_at": updated_at,
                "contacts": [{"id": "c-1"}],
                "tags": ["vip"],
            }
        )

        snapshot = await build_company_snapshot(
            db_connection,
            organization_id="org-1",
            company_id="co-1",
        )

    assert isinstance(snapshot, CompanySnapshot)
    assert snapshot.name == "Acme Corp"
    assert snapshot.metadata.primary_email == "info@acme.com"
    assert snapshot.updated_at_db == updated_at


@pytest.mark.asyncio
async def test_build_lead_snapshot_builds_linked_entities() -> None:
    """Lead snapshots should include linked contacts and companies."""
    db_connection = MagicMock()
    with patch("libs.shared_utils.graphiti_snapshot_builders.LeadRepository") as repo_cls:
        repo_cls.return_value.get_lead_detail_with_contacts_by_id = AsyncMock(
            return_value={
                "name": "Big Deal",
                "stage_name": "Proposal",
                "stage_id": "stage-2",
                "contacts": [
                    {
                        "contact_id": "c-1",
                        "contact_name": "Jane Doe",
                        "contact_email": "jane@example.com",
                    }
                ],
                "companies": [{"company_id": "co-1", "name": "Acme"}],
                "updated_at": datetime(2026, 7, 1, tzinfo=UTC),
            }
        )

        snapshot = await build_lead_snapshot(
            db_connection,
            organization_id="org-1",
            lead_id="lead-1",
        )

    assert isinstance(snapshot, LeadSnapshot)
    assert snapshot.name == "Big Deal"
    assert len(snapshot.linked_contacts) == 1
    assert len(snapshot.linked_companies) == 1
    assert snapshot.linked_companies[0].company_id == "co-1"
