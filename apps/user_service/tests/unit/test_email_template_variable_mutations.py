"""Unit tests for email template variable mutations."""

import pytest

from apps.user_service.app.schemas.email_templates import (
    EmailTemplateVariableAddRequest,
    EmailTemplateVariableUpdateRequest,
    EmailTemplateVariablesMutation,
)
from apps.user_service.app.schemas.enums import FieldType
from apps.user_service.app.services.email_template_service import EmailTemplateService
from libs.shared_utils.http_exceptions import ValidationException


def _brand_node(variable_id: str = "var-1") -> dict:
    return {
        "id": variable_id,
        "variable_key": "brand",
        "field_name": "Brand",
        "field_type": "text",
        "type_config": {},
        "is_required": False,
        "default_value": "Acme",
        "sort_order": 0,
        "sub_fields": [],
    }


def test_ensure_storage_ids_for_legacy_nodes():
    """Legacy stored nodes without id receive a server-generated id."""
    result = EmailTemplateService._ensure_storage_variable_ids(  # pylint: disable=protected-access
        [{"variable_key": "brand", "field_name": "Brand", "field_type": "text", "sub_fields": []}]
    )
    assert result[0]["id"]
    assert result[0]["variable_key"] == "brand"


def test_backend_assign_ignores_client_id():
    """Persisting from client input always replaces ids with new backend values."""
    client_id = "00000000-0000-0000-0000-000000000099"
    result = EmailTemplateService._assign_backend_variable_ids(  # pylint: disable=protected-access
        [
            {
                "id": client_id,
                "variable_key": "brand",
                "field_name": "Brand",
                "field_type": "text",
                "sub_fields": [],
            }
        ]
    )
    assert result[0]["id"] != client_id


def test_remove_variable_by_id():
    """Remove drops the node from the tree."""
    mutation = EmailTemplateVariablesMutation(remove=["var-1"])
    result = EmailTemplateService._apply_variable_mutations(  # pylint: disable=protected-access
        [_brand_node()], mutation
    )
    assert result == []


def test_update_variable_default_value():
    """Update patches fields on an existing id."""
    mutation = EmailTemplateVariablesMutation(
        update=[
            EmailTemplateVariableUpdateRequest(
                id="var-1",
                default_value="House of Apps",
            )
        ]
    )
    result = EmailTemplateService._apply_variable_mutations(  # pylint: disable=protected-access
        [_brand_node()], mutation
    )
    assert result[0]["default_value"] == "House of Apps"


def test_add_root_variable():
    """Add without parent_id appends a root variable."""
    mutation = EmailTemplateVariablesMutation(
        add=[
            EmailTemplateVariableAddRequest(
                parent_id=None,
                variable_key="code",
                field_name="Code",
                field_type=FieldType.NUMBER,
                default_value=42,
            )
        ]
    )
    result = EmailTemplateService._apply_variable_mutations(  # pylint: disable=protected-access
        [_brand_node()], mutation
    )
    assert len(result) == 2
    assert result[1]["variable_key"] == "code"
    assert result[1]["id"]


def test_remove_cascades_to_descendants():
    """Removing a parent removes nested variables."""
    tree = [
        {
            "id": "obj-1",
            "variable_key": "profile",
            "field_name": "Profile",
            "field_type": "object",
            "type_config": {},
            "is_required": False,
            "default_value": None,
            "sort_order": 0,
            "sub_fields": [
                {
                    "id": "child-1",
                    "variable_key": "first_name",
                    "field_name": "First",
                    "field_type": "text",
                    "type_config": {},
                    "is_required": True,
                    "default_value": None,
                    "sort_order": 0,
                    "sub_fields": [],
                }
            ],
        }
    ]
    mutation = EmailTemplateVariablesMutation(remove=["obj-1"])
    result = EmailTemplateService._apply_variable_mutations(  # pylint: disable=protected-access
        tree, mutation
    )
    assert result == []


def test_unknown_variable_id_raises():
    """Update/remove against missing ids fails."""
    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._apply_variable_mutations(  # pylint: disable=protected-access
            [_brand_node()],
            EmailTemplateVariablesMutation(
                update=[EmailTemplateVariableUpdateRequest(id="missing", default_value="x")]
            ),
        )
    assert exc_info.value.message_key == "email_templates.errors.variable_id_not_found"
