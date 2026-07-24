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
