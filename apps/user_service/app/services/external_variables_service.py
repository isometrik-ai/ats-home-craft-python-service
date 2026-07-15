"""Catalog and resolution of scalar variables for external integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import EntityType
from apps.user_service.app.schemas.external_clients import (
    ENTITY_FIXED_VARIABLE_DEFINITIONS,
    SCALAR_ENTITY_VARIABLE_FIELD_TYPES,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.external_variable_coercion import (
    coerce_external_variable_value,
)
from apps.user_service.app.utils.common_utils import UserContext

_CONTACT_DERIVED_VARIABLE_KEYS = frozenset(
    {"name", "phone_number", "phone_isd_code", "address"},
)
_ADDRESS_FIELD_KEYS = (
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "country",
)


@dataclass(frozen=True)
class EntityVariableContext:
    """Cached variable catalog and custom-field lookup maps for one entity type."""

    definitions: list[dict[str, Any]]
    field_type_by_key: dict[str, str]
    id_to_def: dict[str, Any]


def normalize_variable_key(raw_key: str) -> str:
    """Normalize variable keys for consistent lookups (strip/lower/underscore/alnum)."""
    key = (raw_key or "").strip().lower()
    key = key.replace(" ", "_")
    return "".join(ch for ch in key if ch.isalnum() or ch == "_")


class ExternalVariablesService:
    """Build variable catalogs and resolve contact variable values for integrations."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self._custom_field_service = CustomFieldService(
            db_connection=db_connection,
            user_context=user_context,
        )

    async def load_entity_context(self, entity_type: EntityType) -> EntityVariableContext:
        """Load custom field definitions once and build the variable catalog."""
        field_definitions, _ = await self._custom_field_service.get_custom_fields_list(entity_type)
        definitions = self._build_variable_definitions(entity_type, field_definitions)
        return EntityVariableContext(
            definitions=definitions,
            field_type_by_key=self._build_field_type_index(definitions),
            id_to_def={str(defn.id): defn for defn in (field_definitions or [])},
        )

    async def get_variable_definitions(self, entity_type: EntityType) -> list[dict[str, Any]]:
        """Return fixed and active scalar custom field variables for an entity type."""
        context = await self.load_entity_context(entity_type)
        return context.definitions

    async def resolve_contact_field_values_by_phone(
        self,
        *,
        phone_number: str,
        variable_keys: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Resolve contact variable values for the first match on phone number."""
        from apps.user_service.app.services.contacts_service import ContactsService

        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        details = await contacts_service.get_contact_details_by_phone(phone_number=phone_number)
        context = await self.load_entity_context(EntityType.CONTACT)

        keys_to_resolve = self._keys_to_resolve(variable_keys, context.definitions)
        additional_data = details.get("additional_data")
        if not isinstance(additional_data, dict):
            additional_data = {}

        custom_field_values = self._build_custom_field_value_map(
            stored_custom_fields=details.get("custom_fields"),
            id_to_def=context.id_to_def,
        )

        items: list[dict[str, str]] = []
        for raw_key in keys_to_resolve:
            value = self._resolve_contact_field_value(
                details=details,
                additional_data=additional_data,
                custom_field_value_by_key=custom_field_values,
                raw_key=raw_key,
            )
            normalized_key = normalize_variable_key(raw_key)
            field_type = context.field_type_by_key.get(raw_key) or context.field_type_by_key.get(
                normalized_key
            )
            items.append(
                {
                    "variable_key": raw_key,
                    "variable_value": coerce_external_variable_value(
                        value,
                        field_type=field_type,
                    ),
                }
            )
        return items

    def _build_variable_definitions(
        self,
        entity_type: EntityType,
        field_definitions: list[Any],
    ) -> list[dict[str, Any]]:
        """Merge fixed and scalar custom field definitions for an entity type."""
        fixed_variables = [
            {
                **definition,
                "source": "fixed",
                "is_required": False,
                "field_id": None,
            }
            for definition in ENTITY_FIXED_VARIABLE_DEFINITIONS.get(entity_type, ())
        ]
        custom_variables = [
            self._custom_field_definition_to_variable(defn)
            for defn in field_definitions or []
            if getattr(defn, "is_active", True)
            and str(getattr(defn, "field_type", "") or "") in SCALAR_ENTITY_VARIABLE_FIELD_TYPES
        ]
        return [*fixed_variables, *custom_variables]

    @staticmethod
    def _build_field_type_index(definitions: list[dict[str, Any]]) -> dict[str, str]:
        """Build raw and normalized variable_key -> field_type lookup."""
        field_type_by_key: dict[str, str] = {}
        for definition in definitions:
            variable_key = str(definition["variable_key"])
            field_type = str(definition["field_type"])
            field_type_by_key[variable_key] = field_type
            normalized_key = normalize_variable_key(variable_key)
            if normalized_key:
                field_type_by_key.setdefault(normalized_key, field_type)
        return field_type_by_key

    @staticmethod
    def _keys_to_resolve(
        variable_keys: list[str] | None,
        definitions: list[dict[str, Any]],
    ) -> list[str]:
        """Return explicit keys or the full catalog when none were requested."""
        keys_to_resolve = [key for key in (variable_keys or []) if str(key or "").strip()]
        if keys_to_resolve:
            return keys_to_resolve
        return [str(defn["variable_key"]) for defn in definitions]

    @staticmethod
    def _custom_field_definition_to_variable(defn: Any) -> dict[str, Any]:
        """Map a scalar custom field definition to a catalog entry."""
        return {
            "variable_key": defn.field_key,
            "field_name": defn.field_name,
            "field_type": defn.field_type,
            "source": "custom",
            "description": getattr(defn, "description", None),
            "is_required": bool(getattr(defn, "is_required", False)),
            "field_id": str(defn.id),
        }

    def _build_custom_field_value_map(
        self,
        *,
        stored_custom_fields: Any,
        id_to_def: dict[str, Any],
    ) -> dict[str, Any]:
        """Build normalized-key -> scalar value map from resolved custom fields."""
        resolved_custom_fields = self._custom_field_service.resolve_fields_for_read(
            stored_custom_fields,
            id_to_def,
        )
        custom_field_value_by_key: dict[str, Any] = {}
        for cell in resolved_custom_fields or []:
            if not isinstance(cell, dict):
                continue
            field_key = str(cell.get("field_key") or "")
            if not field_key:
                continue
            norm_key = normalize_variable_key(field_key)
            if not norm_key:
                continue
            custom_field_value_by_key[norm_key] = cell.get("value")
        return custom_field_value_by_key

    def _resolve_contact_field_value(
        self,
        *,
        details: dict[str, Any],
        additional_data: dict[str, Any],
        custom_field_value_by_key: dict[str, Any],
        raw_key: str,
    ) -> Any:
        """Resolve a variable value from details, additional_data, then custom fields."""
        normalized_key = normalize_variable_key(raw_key)

        value: Any = None
        if normalized_key and normalized_key in details:
            value = details.get(normalized_key)

        if value is None and normalized_key:
            for candidate in (
                raw_key,
                (raw_key or "").strip(),
                (raw_key or "").strip().lower(),
                normalized_key,
            ):
                if candidate in additional_data:
                    value = additional_data.get(candidate)
                    break

        if value is None and normalized_key:
            value = custom_field_value_by_key.get(normalized_key)

        if value is None and normalized_key in _CONTACT_DERIVED_VARIABLE_KEYS:
            value = self._resolve_contact_derived_value(details, normalized_key)

        return value

    @staticmethod
    def _resolve_contact_derived_value(details: dict[str, Any], normalized_key: str) -> Any:
        """Resolve computed contact variables not stored as top-level columns."""
        if normalized_key == "name":
            return ExternalVariablesService._resolve_contact_full_name(details)
        if normalized_key == "phone_number":
            return ExternalVariablesService._resolve_primary_phone_field(
                details,
                field="phone_number",
            )
        if normalized_key == "phone_isd_code":
            return ExternalVariablesService._resolve_primary_phone_field(
                details,
                field="phone_isd_code",
            )
        if normalized_key == "address":
            return ExternalVariablesService._resolve_primary_contact_address(details)
        return None

    @staticmethod
    def _resolve_contact_full_name(details: dict[str, Any]) -> str | None:
        """Build full name from prefix, first, middle, and last name."""
        parts = [
            str(details.get(part_key) or "").strip()
            for part_key in ("prefix", "first_name", "middle_name", "last_name")
        ]
        full_name = " ".join(part for part in parts if part)
        return full_name or None

    @staticmethod
    def _select_primary_phone_dict(phones: Any) -> dict[str, Any] | None:
        """Return the primary phone dict if present, else the first phone dict."""
        if not isinstance(phones, list) or not phones:
            return None
        for phone_row in phones:
            if isinstance(phone_row, dict) and phone_row.get("is_primary"):
                return phone_row
        for phone_row in phones:
            if isinstance(phone_row, dict):
                return phone_row
        return None

    @classmethod
    def _resolve_primary_phone_field(cls, details: dict[str, Any], *, field: str) -> str | None:
        """Return a primary-phone scalar field from contact ``phones`` JSON."""
        primary_phone = cls._select_primary_phone_dict(details.get("phones"))
        if not primary_phone:
            return None
        raw_value = primary_phone.get(field)
        if raw_value is None:
            return None
        text = str(raw_value).strip()
        return text or None

    @staticmethod
    def _resolve_primary_contact_address(details: dict[str, Any]) -> dict[str, Any] | None:
        """Return the primary (or first) contact address as an address value object."""
        addresses = details.get("addresses")
        if not isinstance(addresses, list) or not addresses:
            return None

        selected: dict[str, Any] | None = None
        for address_row in addresses:
            if isinstance(address_row, dict) and address_row.get("is_primary"):
                selected = address_row
                break
        if selected is None:
            for address_row in addresses:
                if isinstance(address_row, dict):
                    selected = address_row
                    break
        if selected is None:
            return None

        return {key: selected.get(key) for key in _ADDRESS_FIELD_KEYS}
