"""Unit tests for email template variable validation helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.email_templates import (
    EmailTemplateVariableDefinition,
)
from apps.user_service.app.schemas.enums import FieldType
from apps.user_service.app.services.email_template_variable_validation import (
    EmailTemplateVariableValidator,
    _normalize_address_input,
    collect_renderable_variables,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_normalize_address_input_maps_line_aliases():
    """Address aliases line1/line2 map to storage keys."""
    normalized = _normalize_address_input({"line1": "1 Main", "line2": "Suite 2"})
    assert normalized["address_line1"] == "1 Main"
    assert normalized["address_line2"] == "Suite 2"


def test_collect_renderable_variables_skips_objects():
    """Object nodes expand to scalar/list children only."""
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="profile",
            field_name="Profile",
            field_type=FieldType.OBJECT,
            default_value=None,
            sub_fields=[
                EmailTemplateVariableDefinition(
                    variable_key="first_name",
                    field_name="First Name",
                    field_type=FieldType.TEXT,
                    default_value="Ada",
                ),
                EmailTemplateVariableDefinition(
                    variable_key="tags",
                    field_name="Tags",
                    field_type=FieldType.LIST,
                    default_value=None,
                    sub_fields=[
                        EmailTemplateVariableDefinition(
                            variable_key="tag",
                            field_name="Tag",
                            field_type=FieldType.TEXT,
                            default_value="vip",
                        )
                    ],
                ),
            ],
        )
    ]

    renderable = collect_renderable_variables(variables)

    assert [node.variable_key for node in renderable] == ["first_name", "tags"]


def test_validate_default_rejects_container_defaults():
    """Object/list variables cannot carry default values."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())

    with pytest.raises(ValidationException) as exc_info:
        validator.validate_default_for_field_type(
            "profile",
            FieldType.OBJECT,
            {},
            is_required=False,
            default_value={"name": "Ada"},
        )
    assert (
        exc_info.value.message_key
        == "email_templates.errors.default_value_not_allowed_on_container"
    )


def test_validate_default_required_scalar():
    """Required scalar variables must provide default_value."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())

    with pytest.raises(ValidationException) as exc_info:
        validator.validate_default_for_field_type(
            "brand",
            FieldType.TEXT,
            {},
            is_required=True,
            default_value=None,
        )
    assert exc_info.value.message_key == "email_templates.errors.default_value_required"


def test_validate_default_for_text_field():
    """Scalar defaults are coerced through CustomFieldService."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())
    validator._custom_field_service._coerce_field_value = MagicMock(return_value="Acme")

    result = validator.validate_default_for_field_type(
        "brand",
        FieldType.TEXT,
        {},
        is_required=False,
        default_value=" Acme ",
    )

    assert result == "Acme"
    validator._custom_field_service._coerce_field_value.assert_called_once()


def test_validate_list_default_value_wraps_scalar():
    """List defaults accept a scalar and wrap it in a list."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())
    validator.validate_default_for_field_type = MagicMock(return_value="vip")

    result = validator.validate_list_default_value(
        "tags",
        EmailTemplateVariableDefinition(
            variable_key="tag",
            field_name="Tag",
            field_type=FieldType.TEXT,
            default_value=None,
        ),
        "vip",
    )

    assert result == ["vip"]


def test_resolve_runtime_variable_values_unknown_key():
    """Unknown runtime keys are rejected."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="brand",
            field_name="Brand",
            field_type=FieldType.TEXT,
            default_value="Acme",
        )
    ]

    with pytest.raises(ValidationException) as exc_info:
        validator.resolve_runtime_variable_values(variables, {"missing": "x"})
    assert exc_info.value.message_key == "email_templates.errors.unknown_variable_key"


def test_resolve_runtime_variable_values_uses_defaults():
    """Runtime resolution falls back to template defaults."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())
    validator.validate_default_for_field_type = MagicMock(return_value="Acme")
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="brand",
            field_name="Brand",
            field_type=FieldType.TEXT,
            default_value="Acme",
        ),
        EmailTemplateVariableDefinition(
            variable_key="code",
            field_name="Code",
            field_type=FieldType.TEXT,
            is_required=False,
            default_value=None,
        ),
    ]

    resolved = validator.resolve_runtime_variable_values(variables, {})

    assert resolved == {"brand": "Acme", "code": None}


def test_validate_variable_tree_defaults_walks_nested_nodes():
    """Tree validation validates scalar leaves and list defaults."""
    validator = EmailTemplateVariableValidator(db_connection=MagicMock())
    validator.validate_default_for_field_type = MagicMock(return_value="Acme")
    validator.validate_list_default_value = MagicMock(return_value=["vip"])

    validator.validate_variable_tree_defaults(
        [
            EmailTemplateVariableDefinition(
                variable_key="brand",
                field_name="Brand",
                field_type=FieldType.TEXT,
                default_value="Acme",
            ),
            EmailTemplateVariableDefinition(
                variable_key="tags",
                field_name="Tags",
                field_type=FieldType.LIST,
                default_value=["vip"],
                sub_fields=[
                    EmailTemplateVariableDefinition(
                        variable_key="tag",
                        field_name="Tag",
                        field_type=FieldType.TEXT,
                        default_value=None,
                    )
                ],
            ),
        ]
    )

    validator.validate_default_for_field_type.assert_called_once()
    validator.validate_list_default_value.assert_called_once()
