"""Unit tests for ExternalVariablesService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import EntityType, FieldType
from apps.user_service.app.services.external_variables_service import (
    ExternalVariablesService,
    normalize_variable_key,
)
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


def _ctx() -> UserContext:
    """Build user context for external variable tests."""
    return UserContext(user_id="user-1", email="user@example.com", organization_id=ORG_ID)


def test_normalize_variable_key():
    """Variable keys are normalized for lookup."""
    assert normalize_variable_key(" First Name ") == "first_name"
    assert normalize_variable_key("Phone#1") == "phone1"


def test_build_field_type_index():
    """Field type index includes raw and normalized keys."""
    index = ExternalVariablesService._build_field_type_index(
        [
            {"variable_key": "First Name", "field_type": FieldType.TEXT.value},
            {"variable_key": "score", "field_type": FieldType.NUMBER.value},
        ]
    )
    assert index["First Name"] == FieldType.TEXT.value
    assert index["first_name"] == FieldType.TEXT.value


def test_keys_to_resolve_defaults_to_catalog():
    """Empty variable key list resolves full catalog."""
    definitions = [{"variable_key": "name"}, {"variable_key": "email"}]
    assert ExternalVariablesService._keys_to_resolve(None, definitions) == ["name", "email"]
    assert ExternalVariablesService._keys_to_resolve([" phone "], definitions) == [" phone "]


def test_custom_field_definition_to_variable():
    """Custom field definitions map to catalog entries."""
    defn = SimpleNamespace(
        id="cf-1",
        field_key="budget",
        field_name="Budget",
        field_type=FieldType.CURRENCY.value,
        description="Annual budget",
        is_required=True,
        is_active=True,
    )
    mapped = ExternalVariablesService._custom_field_definition_to_variable(defn)
    assert mapped["variable_key"] == "budget"
    assert mapped["source"] == "custom"
    assert mapped["field_id"] == "cf-1"


def test_resolve_contact_full_name_and_phone():
    """Derived contact variables resolve from nested structures."""
    details = {
        "prefix": "Dr.",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "phones": [
            {"phone_number": "999", "phone_isd_code": "+1", "is_primary": True},
            {"phone_number": "888", "phone_isd_code": "+44"},
        ],
        "addresses": [
            {
                "is_primary": True,
                "address_line1": "1 Analytical Engine Rd",
                "city": "London",
                "state": "",
                "postal_code": "SW1",
                "country": "UK",
            }
        ],
    }
    assert ExternalVariablesService._resolve_contact_full_name(details) == "Dr. Ada Lovelace"
    assert (
        ExternalVariablesService._resolve_primary_phone_field(details, field="phone_number")
        == "999"
    )
    assert (
        ExternalVariablesService._resolve_primary_phone_field(details, field="phone_isd_code")
        == "+1"
    )
    address = ExternalVariablesService._resolve_primary_contact_address(details)
    assert address["address_line1"] == "1 Analytical Engine Rd"


def test_resolve_contact_field_value_precedence():
    """Resolution prefers details, additional_data, then custom fields."""
    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    details = {"email": "top@example.com", "additional_data": {"nickname": "Ace"}}
    additional_data = {"nickname": "FromAdditional"}

    assert (
        service._resolve_contact_field_value(
            details=details,
            additional_data=additional_data,
            custom_field_value_by_key={"score": 10},
            raw_key="email",
        )
        == "top@example.com"
    )
    assert (
        service._resolve_contact_field_value(
            details=details,
            additional_data=additional_data,
            custom_field_value_by_key={"score": 10},
            raw_key="nickname",
        )
        == "FromAdditional"
    )
    assert (
        service._resolve_contact_field_value(
            details=details,
            additional_data={},
            custom_field_value_by_key={"score": 10},
            raw_key="score",
        )
        == 10
    )


@pytest.mark.asyncio
async def test_get_variable_definitions_merges_fixed_and_custom():
    """Variable definitions include fixed and active scalar custom fields."""
    custom_field = SimpleNamespace(
        id="cf-1",
        field_key="tier",
        field_name="Tier",
        field_type=FieldType.TEXT.value,
        is_active=True,
        is_required=False,
        description=None,
    )
    fake_custom_fields = AsyncMock()
    fake_custom_fields.get_custom_fields_list = AsyncMock(return_value=([custom_field], 1))
    fake_custom_fields.resolve_fields_for_read = MagicMock(return_value=[])

    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    service._custom_field_service = fake_custom_fields

    definitions = await service.get_variable_definitions(EntityType.CONTACT)
    keys = {item["variable_key"] for item in definitions}
    assert "tier" in keys
    assert any(item.get("source") == "fixed" for item in definitions)


@pytest.mark.asyncio
async def test_resolve_contact_field_values_by_phone():
    """Phone lookup resolves requested keys with coercion."""
    contact_details = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "phones": [{"phone_number": "5551234", "phone_isd_code": "+1", "is_primary": True}],
        "custom_fields": [],
        "additional_data": {},
    }
    fake_contacts = AsyncMock()
    fake_contacts.get_contact_details_by_phone = AsyncMock(return_value=contact_details)

    fake_custom_fields = AsyncMock()
    fake_custom_fields.get_custom_fields_list = AsyncMock(return_value=([], 0))
    fake_custom_fields.resolve_fields_for_read = MagicMock(return_value=[])

    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    service._custom_field_service = fake_custom_fields

    with patch(
        "apps.user_service.app.services.contacts_service.ContactsService",
        return_value=fake_contacts,
    ):
        items = await service.resolve_contact_field_values_by_phone(
            phone_number="5551234",
            variable_keys=["name", "phone_number"],
        )

    assert items == [
        {"variable_key": "name", "variable_value": "Jordan Lee"},
        {"variable_key": "phone_number", "variable_value": "5551234"},
    ]


def test_normalize_variable_key_empty_and_special_chars():
    """Empty and punctuation-only keys normalize to empty string."""
    assert normalize_variable_key("") == ""
    assert normalize_variable_key(None) == ""
    assert normalize_variable_key("###") == ""


def test_build_field_type_index_skips_empty_normalized_key():
    """Keys that normalize to empty are still indexed by raw key only."""
    index = ExternalVariablesService._build_field_type_index(
        [{"variable_key": "###", "field_type": FieldType.TEXT.value}]
    )
    assert index["###"] == FieldType.TEXT.value
    assert " " not in index


def test_select_primary_phone_dict_edge_cases():
    """Primary phone selection handles empty lists and non-dict rows."""
    assert ExternalVariablesService._select_primary_phone_dict(None) is None
    assert ExternalVariablesService._select_primary_phone_dict([]) is None
    assert ExternalVariablesService._select_primary_phone_dict(["bad"]) is None
    phones = [
        "not-a-dict",
        {"phone_number": "111", "is_primary": False},
        {"phone_number": "222"},
    ]
    assert ExternalVariablesService._select_primary_phone_dict(phones)["phone_number"] == "111"


def test_resolve_primary_phone_field_empty_values():
    """Phone field resolver returns None for missing or blank values."""
    details = {"phones": [{"phone_number": "  ", "phone_isd_code": "+1"}]}
    assert (
        ExternalVariablesService._resolve_primary_phone_field(details, field="phone_number") is None
    )
    assert ExternalVariablesService._resolve_primary_phone_field({}, field="phone_number") is None
    assert (
        ExternalVariablesService._resolve_primary_phone_field(
            {"phones": [{"phone_number": "555"}]},
            field="phone_isd_code",
        )
        is None
    )


def test_resolve_primary_contact_address_edge_cases():
    """Address resolver picks first dict when no primary is flagged."""
    assert ExternalVariablesService._resolve_primary_contact_address({}) is None
    assert ExternalVariablesService._resolve_primary_contact_address({"addresses": []}) is None
    assert ExternalVariablesService._resolve_primary_contact_address({"addresses": ["bad"]}) is None
    details = {
        "addresses": [
            "bad",
            {"address_line1": "First Fallback", "city": "Austin", "country": "US"},
        ]
    }
    address = ExternalVariablesService._resolve_primary_contact_address(details)
    assert address["address_line1"] == "First Fallback"


def test_resolve_contact_derived_value_branches():
    """Derived resolver covers phone ISD, address, and unknown keys."""
    details = {
        "phones": [{"phone_number": "555", "phone_isd_code": "+91", "is_primary": True}],
        "addresses": [{"is_primary": True, "address_line1": "Main", "country": "IN"}],
    }
    assert (
        ExternalVariablesService._resolve_contact_derived_value(details, "phone_isd_code") == "+91"
    )
    assert (
        ExternalVariablesService._resolve_contact_derived_value(details, "address")["city"] is None
    )
    assert ExternalVariablesService._resolve_contact_derived_value(details, "unknown") is None


def test_build_custom_field_value_map_skips_invalid_cells():
    """Custom field map ignores malformed cells and blank keys."""
    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    service._custom_field_service.resolve_fields_for_read = MagicMock(
        return_value=[
            "not-a-dict",
            {"field_key": "", "value": "skip"},
            {"field_key": "###", "value": "skip-empty-norm"},
            {"field_key": "Tier", "value": "Gold"},
        ]
    )
    result = service._build_custom_field_value_map(
        stored_custom_fields=[],
        id_to_def={},
    )
    assert result == {"tier": "Gold"}


def test_build_variable_definitions_filters_inactive_and_non_scalar():
    """Only active scalar custom fields appear in the catalog."""
    active = SimpleNamespace(
        id="cf-1",
        field_key="score",
        field_name="Score",
        field_type=FieldType.NUMBER.value,
        is_active=True,
        is_required=False,
        description=None,
    )
    inactive = SimpleNamespace(
        id="cf-2",
        field_key="hidden",
        field_name="Hidden",
        field_type=FieldType.TEXT.value,
        is_active=False,
        is_required=False,
        description=None,
    )
    object_field = SimpleNamespace(
        id="cf-3",
        field_key="nested",
        field_name="Nested",
        field_type=FieldType.OBJECT.value,
        is_active=True,
        is_required=False,
        description=None,
    )
    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    definitions = service._build_variable_definitions(
        EntityType.CONTACT,
        [active, inactive, object_field],
    )
    custom_keys = {item["variable_key"] for item in definitions if item.get("source") == "custom"}
    assert custom_keys == {"score"}


def test_resolve_contact_field_value_from_details_key():
    """Top-level details keys take precedence over additional_data."""
    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    assert (
        service._resolve_contact_field_value(
            details={"nickname": "TopLevel"},
            additional_data={"nickname": "Additional"},
            custom_field_value_by_key={},
            raw_key="nickname",
        )
        == "TopLevel"
    )


@pytest.mark.asyncio
async def test_resolve_contact_field_values_by_phone_non_dict_additional_data():
    """Non-dict additional_data is treated as empty during phone resolution."""
    contact_details = {
        "first_name": "Sam",
        "additional_data": "not-a-dict",
        "custom_fields": [],
    }
    fake_contacts = AsyncMock()
    fake_contacts.get_contact_details_by_phone = AsyncMock(return_value=contact_details)
    fake_custom_fields = AsyncMock()
    fake_custom_fields.get_custom_fields_list = AsyncMock(return_value=([], 0))
    fake_custom_fields.resolve_fields_for_read = MagicMock(return_value=[])

    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    service._custom_field_service = fake_custom_fields

    with patch(
        "apps.user_service.app.services.contacts_service.ContactsService",
        return_value=fake_contacts,
    ):
        items = await service.resolve_contact_field_values_by_phone(
            phone_number="5550000",
            variable_keys=["name"],
        )

    assert items == [{"variable_key": "name", "variable_value": "Sam"}]


@pytest.mark.asyncio
async def test_load_entity_context_builds_id_index():
    """load_entity_context caches definitions and id lookup map."""
    custom_field = SimpleNamespace(
        id="cf-99",
        field_key="tier",
        field_name="Tier",
        field_type=FieldType.TEXT.value,
        is_active=True,
        is_required=False,
        description=None,
    )
    fake_custom_fields = AsyncMock()
    fake_custom_fields.get_custom_fields_list = AsyncMock(return_value=([custom_field], 1))

    service = ExternalVariablesService(db_connection=MagicMock(), user_context=_ctx())
    service._custom_field_service = fake_custom_fields

    context = await service.load_entity_context(EntityType.LEAD)

    assert context.id_to_def["cf-99"] is custom_field
    assert any(defn["variable_key"] == "tier" for defn in context.definitions)
