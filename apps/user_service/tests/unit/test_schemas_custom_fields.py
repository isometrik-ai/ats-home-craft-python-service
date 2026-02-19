"""Unit tests for custom fields schemas."""

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.custom_fields import (
    MAX_NESTING_DEPTH,
    CreateCustomFieldRequest,
    FlatFieldUpdateRequest,
    UpdateCustomFieldRequest,
    validate_and_normalize_type_config,
)
from apps.user_service.app.schemas.enums import EntityType, FieldType
from libs.shared_utils.http_exceptions import ValidationException

# ============================================================================
# VALIDATE AND NORMALIZE TYPE CONFIG TESTS
# ============================================================================


def test_validate_type_config_dropdown():
    """Test validate_and_normalize_type_config for dropdown."""
    result = validate_and_normalize_type_config(FieldType.DROPDOWN, {"options": ["a", "b"]})
    assert result == {"options": ["a", "b"]}


def test_validate_type_config_simple_type():
    """Test validate_and_normalize_type_config for simple type."""
    result = validate_and_normalize_type_config(FieldType.TEXT, {})
    assert result == {}


def test_validate_type_config_range_slider():
    """Test validate_and_normalize_type_config for range slider."""
    result = validate_and_normalize_type_config(
        FieldType.RANGE_SLIDER, {"min": 0, "max": 100, "step": 1}
    )
    assert result["min"] == 0
    assert result["max"] == 100


# ============================================================================
# CREATE CUSTOM FIELD REQUEST TESTS
# ============================================================================


def test_create_request_valid():
    """Test valid create custom field request (with optional description)."""
    req = CreateCustomFieldRequest(
        field_name="Test Field",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
        description="Test description",
    )
    assert req.field_name == "Test Field"
    assert req.field_type == FieldType.TEXT
    assert req.description == "Test description"


def test_create_request_field_name_too_long():
    """Test create request with field_name > 200 chars."""
    long_name = "a" * 201
    with pytest.raises(ValidationError):
        CreateCustomFieldRequest(
            field_name=long_name,
            field_type=FieldType.TEXT,
            entity_type=EntityType.CONTACT,
        )


def test_create_request_field_name_empty():
    """Test create request with empty field_name."""
    with pytest.raises(ValidationError):
        CreateCustomFieldRequest(
            field_name="",
            field_type=FieldType.TEXT,
            entity_type=EntityType.CONTACT,
        )


def test_create_request_sub_fields_not_object():
    """Test sub_fields only allowed for object type."""
    with pytest.raises(ValidationException):
        CreateCustomFieldRequest(
            field_name="Test",
            field_type=FieldType.TEXT,
            entity_type=EntityType.CONTACT,
            sub_fields=[CreateCustomFieldRequest(field_name="Sub", field_type=FieldType.TEXT)],
        )


def test_create_request_sub_fields_for_object():
    """Test sub_fields allowed for object type."""
    req = CreateCustomFieldRequest(
        field_name="Parent",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[CreateCustomFieldRequest(field_name="Child", field_type=FieldType.TEXT)],
    )
    assert len(req.sub_fields) == 1


def test_create_request_type_config_normalized():
    """Test type_config is normalized on creation."""
    req = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.DROPDOWN,
        entity_type=EntityType.CONTACT,
        type_config={"options": ["a", "b"]},
    )
    assert req.type_config == {"options": ["a", "b"]}


def test_create_request_type_config_empty_for_text():
    """Test type_config normalized to empty for text type."""
    req = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
        type_config={"invalid": "data"},
    )
    assert req.type_config == {}


# ============================================================================
# NESTING DEPTH VALIDATION TESTS
# ============================================================================


def test_nesting_depth_within_limit():
    """Test nesting depth within MAX_NESTING_DEPTH."""

    # Build nested structure up to MAX_NESTING_DEPTH - 1
    def build_nested(depth):
        if depth == 0:
            return CreateCustomFieldRequest(
                field_name=f"Level{depth}",
                field_type=FieldType.OBJECT,
                entity_type=EntityType.CONTACT,
            )
        parent = build_nested(depth - 1)
        parent.sub_fields = [
            CreateCustomFieldRequest(
                field_name=f"Level{depth}",
                field_type=FieldType.OBJECT,
            )
        ]
        return parent

    req = build_nested(MAX_NESTING_DEPTH - 2)
    # Should not raise
    CreateCustomFieldRequest.validate_nesting_depth_iterative(req)


def test_nesting_depth_exceeds_limit():
    """Test nesting depth exceeding MAX_NESTING_DEPTH raises error."""

    # Build nested structure where deepest level is at depth MAX_NESTING_DEPTH
    def build_chain(current_depth, target_depth):
        if current_depth > target_depth:
            return None
        field = CreateCustomFieldRequest(
            field_name=f"Level{current_depth}",
            field_type=FieldType.OBJECT,
        )
        if current_depth < target_depth:
            child = build_chain(current_depth + 1, target_depth)
            if child:
                field.sub_fields = [child]
        return field

    # Build structure where deepest field is at depth MAX_NESTING_DEPTH (should fail)
    deepest = build_chain(1, MAX_NESTING_DEPTH)
    req = CreateCustomFieldRequest(
        field_name="Root",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[deepest] if deepest else [],
    )
    with pytest.raises(ValidationException) as exc_info:
        CreateCustomFieldRequest.validate_nesting_depth_iterative(req)
    assert "max_nesting_depth_exceeded" in str(exc_info.value.message_key)


def test_nesting_depth_complex_structure():
    """Test nesting depth with complex branching structure."""
    req = CreateCustomFieldRequest(
        field_name="Root",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(
                field_name="Child1",
                field_type=FieldType.OBJECT,
                sub_fields=[
                    CreateCustomFieldRequest(
                        field_name="Grandchild1",
                        field_type=FieldType.TEXT,
                    ),
                    CreateCustomFieldRequest(
                        field_name="Grandchild2",
                        field_type=FieldType.OBJECT,
                        sub_fields=[
                            CreateCustomFieldRequest(
                                field_name="GreatGrandchild",
                                field_type=FieldType.TEXT,
                            )
                        ],
                    ),
                ],
            ),
            CreateCustomFieldRequest(
                field_name="Child2",
                field_type=FieldType.TEXT,
            ),
        ],
    )
    # Should not raise if depth <= MAX_NESTING_DEPTH
    CreateCustomFieldRequest.validate_nesting_depth_iterative(req)


# ============================================================================
# TYPE CONFIG VALIDATION TESTS
# ============================================================================


def test_type_config_dropdown_invalid():
    """Test invalid dropdown type_config raises error."""
    with pytest.raises(ValidationError):
        CreateCustomFieldRequest(
            field_name="Test",
            field_type=FieldType.DROPDOWN,
            entity_type=EntityType.CONTACT,
            type_config={"options": []},  # Empty options
        )


def test_type_config_range_slider_invalid():
    """Test invalid range slider type_config raises error."""
    with pytest.raises(ValidationException):
        CreateCustomFieldRequest(
            field_name="Test",
            field_type=FieldType.RANGE_SLIDER,
            entity_type=EntityType.CONTACT,
            type_config={"min": 100, "max": 50},  # max <= min
        )


def test_type_config_currency_invalid():
    """Test invalid currency type_config raises error."""
    with pytest.raises(ValidationError):
        CreateCustomFieldRequest(
            field_name="Test",
            field_type=FieldType.CURRENCY,
            entity_type=EntityType.CONTACT,
            type_config={"allowed_currencies": []},  # Empty
        )


def test_type_config_file_upload_valid():
    """Test valid file upload type_config (representative config type)."""
    req = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.FILE_UPLOAD,
        entity_type=EntityType.CONTACT,
        type_config={
            "allow_multiple": True,
            "max_files": 5,
            "accepted_file_types": "pdf_only",
        },
    )
    assert req.type_config["allow_multiple"] is True
    assert req.type_config["max_files"] == 5


# ============================================================================
# FLAT FIELD UPDATE REQUEST — type_config required when field_type has config
# ============================================================================


def test_update_range_slider_without_config_raises():
    """Updating field_type to range_slider without type_config must raise."""
    with pytest.raises(ValidationException) as exc_info:
        FlatFieldUpdateRequest(
            id="7be664cd-fc6b-4986-8b28-4c83a8907a65",
            field_name="Company Name",
            field_type=FieldType.RANGE_SLIDER,
        )
    assert "type_config_required_for_field_type" in str(exc_info.value.message_key)


def test_update_range_slider_with_config_succeeds():
    """Updating field_type to range_slider with valid type_config succeeds."""
    req = FlatFieldUpdateRequest(
        id="7be664cd-fc6b-4986-8b28-4c83a8907a65",
        field_type=FieldType.RANGE_SLIDER,
        type_config={"min": 0, "max": 100, "step": 1},
    )
    assert req.type_config["min"] == 0
    assert req.type_config["max"] == 100
    assert req.type_config["step"] == 1


def test_update_simple_type_without_config_succeeds():
    """Updating field_type to text (no config) without type_config is allowed."""
    req = FlatFieldUpdateRequest(
        id="some-id",
        field_type=FieldType.TEXT,
    )
    assert req.type_config == {}


def test_flat_update_config_without_field_type_raises():
    """Sending type_config without field_type must raise (cannot validate type_config)."""
    with pytest.raises(ValidationException) as exc_info:
        FlatFieldUpdateRequest(
            id="7be664cd-fc6b-4986-8b28-4c83a8907a65",
            field_name="Company Name",
            description="Full legal or trading company name",
            is_required=True,
            show_on_create=True,
            show_on_detail=True,
            type_config={},
        )
    assert "field_type_required_for_type_config" in str(exc_info.value.message_key)


def test_update_currency_with_invalid_config_raises():
    """Updating to currency with invalid type_config raises (Pydantic/ValidationException)."""
    with pytest.raises(ValidationError):
        FlatFieldUpdateRequest(
            id="some-id",
            field_type=FieldType.CURRENCY,
            type_config={"allowed_currencies": []},
        )


# ============================================================================
# UPDATE CUSTOM FIELD REQUEST (root) — type_config required when field_type has config
# ============================================================================


def test_root_update_slider_without_type_config_raises():
    """Root update with field_type=range_slider and no type_config must raise."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateCustomFieldRequest(
            field_type=FieldType.RANGE_SLIDER,
        )
    assert "type_config_required_for_field_type" in str(exc_info.value.message_key)


def test_root_update_config_without_field_type_raises():
    """Root update with type_config but no field_type must raise."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateCustomFieldRequest(type_config={})
    assert "field_type_required_for_type_config" in str(exc_info.value.message_key)


# ============================================================================
# DEFAULT VALUES TESTS
# ============================================================================


def test_create_request_defaults():
    """Test create request default values."""
    req = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
    )
    assert req.show_on_create is True
    assert req.show_on_detail is False
    assert req.is_required is False
    assert req.sort_order == 0
    assert req.type_config == {}


def test_create_request_custom_defaults():
    """Test create request with custom values."""
    req = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
        show_on_create=False,
        show_on_detail=True,
        is_required=True,
        sort_order=5,
    )
    assert req.show_on_create is False
    assert req.show_on_detail is True
    assert req.is_required is True
    assert req.sort_order == 5
