"""Default-value validation for email template variables."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

from apps.user_service.app.schemas.email_templates import EmailTemplateVariableDefinition
from apps.user_service.app.schemas.enums import FieldType
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_SCALAR_FIELD_TYPES = frozenset(
    {
        FieldType.TEXT,
        FieldType.LONG_TEXT,
        FieldType.RICH_TEXT,
        FieldType.NUMBER,
        FieldType.DATE,
        FieldType.YES_NO,
        FieldType.URL,
        FieldType.DROPDOWN,
        FieldType.RANGE_SLIDER,
        FieldType.CURRENCY,
        FieldType.FILE_UPLOAD,
        FieldType.IMAGE,
        FieldType.ADDRESS,
    }
)


def _normalize_address_input(value: Any) -> Any:
    """Map common address aliases (line1/line2) to custom-field storage keys."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if "line1" in normalized and "address_line1" not in normalized:
        normalized["address_line1"] = normalized.pop("line1")
    if "line2" in normalized and "address_line2" not in normalized:
        normalized["address_line2"] = normalized.pop("line2")
    return normalized


def _field_def_stub(
    field_type: FieldType,
    type_config: dict[str, Any],
    *,
    is_required: bool,
) -> SimpleNamespace:
    """Return a stub field definition for validation."""
    return SimpleNamespace(
        field_type=field_type.value,
        type_config=type_config,
        is_required=is_required,
    )


def collect_renderable_variables(
    variables: list[EmailTemplateVariableDefinition],
) -> list[EmailTemplateVariableDefinition]:
    """Return variable nodes that accept runtime values (scalars and lists)."""
    renderable: list[EmailTemplateVariableDefinition] = []
    queue = list(variables)
    while queue:
        node = queue.pop(0)
        if node.field_type == FieldType.OBJECT:
            queue.extend(node.sub_fields)
            continue
        renderable.append(node)
    return renderable


class EmailTemplateVariableValidator:
    """Validates template variable defaults and runtime values via CustomFieldService."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
    ) -> None:
        """Initialize coercion helpers using the same DB connection as the request."""
        self._custom_field_service = CustomFieldService(
            db_connection=db_connection,
            user_context=user_context,
        )

    def validate_default_for_field_type(
        self,
        variable_key: str,
        field_type: FieldType,
        type_config: dict[str, Any],
        *,
        is_required: bool,
        default_value: Any,
    ) -> Any:
        """Validate and normalize default_value for a scalar variable."""
        if field_type in (FieldType.OBJECT, FieldType.LIST):
            if default_value is not None:
                raise ValidationException(
                    message_key="email_templates.errors.default_value_not_allowed_on_container",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"variable_key": variable_key},
                )
            return None

        if default_value is None:
            if is_required:
                raise ValidationException(
                    message_key="email_templates.errors.default_value_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"variable_key": variable_key},
                )
            return None

        field_def = _field_def_stub(field_type, type_config, is_required=is_required)
        coerced_value = default_value
        if field_type == FieldType.ADDRESS:
            coerced_value = _normalize_address_input(default_value)
        return self._custom_field_service._coerce_field_value(
            variable_key,
            coerced_value,
            field_def,
        )

    def validate_list_default_value(
        self,
        variable_key: str,
        child: EmailTemplateVariableDefinition,
        default_value: Any,
    ) -> Any:
        """Validate list default: JSON array of values matching the single child type."""
        if default_value is None:
            if child.is_required:
                raise ValidationException(
                    message_key="email_templates.errors.default_value_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"variable_key": variable_key},
                )
            return None

        if not isinstance(default_value, list):
            default_value = [default_value]

        child_key = f"{variable_key}[]"
        return [
            self.validate_default_for_field_type(
                child_key,
                child.field_type,
                child.type_config,
                is_required=True,
                default_value=item,
            )
            for item in default_value
        ]

    def resolve_runtime_variable_values(
        self,
        variables: list[EmailTemplateVariableDefinition],
        variable_values: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate runtime values and merge with template defaults."""
        renderable = collect_renderable_variables(variables)
        expected_keys = {node.variable_key for node in renderable}

        unknown_keys = set(variable_values) - expected_keys
        if unknown_keys:
            first_key = sorted(unknown_keys)[0]
            raise ValidationException(
                message_key="email_templates.errors.unknown_variable_key",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"variable_key": first_key},
            )

        resolved: dict[str, Any] = {}
        for node in renderable:
            key = node.variable_key
            if key in variable_values:
                raw_value = variable_values[key]
            elif node.default_value is not None:
                raw_value = node.default_value
            elif node.is_required:
                raise ValidationException(
                    message_key="email_templates.errors.runtime_value_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"variable_key": key},
                )
            else:
                resolved[key] = None
                continue

            if node.field_type == FieldType.LIST:
                if len(node.sub_fields) != 1:
                    raise ValidationException(
                        message_key="custom_fields.errors.list_must_have_exactly_one_child",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
                resolved[key] = self.validate_list_default_value(
                    key, node.sub_fields[0], raw_value
                )
            else:
                resolved[key] = self.validate_default_for_field_type(
                    key,
                    node.field_type,
                    node.type_config,
                    is_required=True,
                    default_value=raw_value,
                )

        return resolved

    def validate_variable_tree_defaults(
        self,
        variables: list[EmailTemplateVariableDefinition],
    ) -> None:
        """Walk variable tree and validate default_value on scalar leaves and lists."""
        queue: list[EmailTemplateVariableDefinition] = list(variables)
        while queue:
            node = queue.pop(0)
            if node.field_type == FieldType.OBJECT:
                queue.extend(node.sub_fields)
                continue
            if node.field_type == FieldType.LIST:
                if len(node.sub_fields) != 1:
                    raise ValidationException(
                        message_key="custom_fields.errors.list_must_have_exactly_one_child",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
                self.validate_list_default_value(
                    node.variable_key,
                    node.sub_fields[0],
                    node.default_value,
                )
                continue
            if node.field_type in _SCALAR_FIELD_TYPES:
                self.validate_default_for_field_type(
                    node.variable_key,
                    node.field_type,
                    node.type_config,
                    is_required=node.is_required,
                    default_value=node.default_value,
                )
