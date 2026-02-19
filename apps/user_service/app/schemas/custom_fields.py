"""Custom Fields Management Schemas Module.

This module contains Pydantic models for custom field management operations.
"""

from collections import deque
from typing import Any

from pydantic import BaseModel, Field, model_validator

from apps.user_service.app.schemas.enums import (
    AcceptedFileTypes,
    EntityType,
    FieldType,
    SupportedCurrency,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

# Maximum nesting depth for custom fields
MAX_NESTING_DEPTH = 5


class DropdownTypeConfig(BaseModel):
    """Type config for dropdown field type."""

    options: list[str] = Field(..., min_length=1, description="Dropdown options")


class RangeSliderTypeConfig(BaseModel):
    """Type config for range_slider field type."""

    min: float = Field(default=0, description="Minimum value")
    max: float = Field(default=100, description="Maximum value")
    step: float = Field(default=1, gt=0, description="Step increment")
    units: list[str] = Field(default_factory=list, description="Unit labels")

    @model_validator(mode="after")
    def validate_range(self) -> "RangeSliderTypeConfig":
        """Validate min < max."""
        if self.max <= self.min:
            raise ValidationException(
                message_key="custom_fields.errors.range_max_greater_than_min",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class CurrencyTypeConfig(BaseModel):
    """Type config for currency field type."""

    allowed_currencies: list[SupportedCurrency] = Field(
        ..., min_length=1, description="Allowed currency codes (from SupportedCurrency enum)"
    )


class FileUploadTypeConfig(BaseModel):
    """Type config for file_upload field type."""

    allow_multiple: bool = Field(default=False, description="Allow multiple files")
    max_files: int = Field(default=1, ge=1, description="Maximum file count")
    accepted_file_types: AcceptedFileTypes = Field(
        default=AcceptedFileTypes.ANY, description="Accepted file types"
    )


class ImageTypeConfig(BaseModel):
    """Type config for image field type."""

    allow_multiple: bool = Field(default=False, description="Allow multiple images")
    max_files: int = Field(default=1, ge=1, description="Maximum image count")


class AddressTypeConfig(BaseModel):
    """Type config for address field type."""

    show_line_2: bool = Field(default=True, description="Show address line 2")
    include_lat_long: bool = Field(default=False, description="Include latitude/longitude fields")
    default_country: str = Field(default="", max_length=10, description="Default country code")


# Mapping from FieldType to corresponding config class (None means empty dict)
FIELD_TYPE_TO_CONFIG_CLASS: dict[FieldType, type[BaseModel] | None] = {
    FieldType.DROPDOWN: DropdownTypeConfig,
    FieldType.RANGE_SLIDER: RangeSliderTypeConfig,
    FieldType.CURRENCY: CurrencyTypeConfig,
    FieldType.FILE_UPLOAD: FileUploadTypeConfig,
    FieldType.IMAGE: ImageTypeConfig,
    FieldType.ADDRESS: AddressTypeConfig,
    FieldType.OBJECT: None,  # Empty dict
    # Simple types (empty dict): text, number, date, yes_no, url, long_text, rich_text
    FieldType.TEXT: None,
    FieldType.NUMBER: None,
    FieldType.DATE: None,
    FieldType.YES_NO: None,
    FieldType.URL: None,
    FieldType.LONG_TEXT: None,
    FieldType.RICH_TEXT: None,
}


def field_type_requires_type_config(field_type: FieldType) -> bool:
    """Return True if this field type has a type_config schema."""
    return FIELD_TYPE_TO_CONFIG_CLASS.get(field_type) is not None


def validate_and_normalize_type_config(
    field_type: FieldType, type_config: dict[str, Any]
) -> dict[str, Any]:
    """Validate and normalize type_config based on field_type.

    Args:
        field_type: The field type enum value
        type_config: The raw type_config dictionary

    Returns:
        Normalized type_config dictionary
    """
    config_class = FIELD_TYPE_TO_CONFIG_CLASS.get(field_type)

    if config_class is None:
        # Simple types or OBJECT type - return empty dict
        return {}

    # Validate and convert using the appropriate config class
    config = config_class(**type_config)
    return config.model_dump()


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================


class CreateCustomFieldRequest(BaseModel):
    """Request schema for creating a custom field.

    Supports creating:
    - Top-level fields (with entity_type)
    - Object parent fields with nested sub-fields recursively
    (with entity_type, field_type='object', sub_fields array)

    This model is recursive - sub_fields can themselves contain sub_fields
    for nested object structures.
    """

    field_name: str = Field(..., min_length=1, max_length=200, description="Field name")
    description: str | None = Field(None, max_length=1000, description="Field description")
    field_type: FieldType = Field(..., description="Field type")
    show_on_create: bool = Field(default=True, description="Show on create form")
    show_on_detail: bool = Field(default=False, description="Show on detail page")
    is_required: bool = Field(default=False, description="Field is required")
    type_config: dict[str, Any] = Field(
        default_factory=dict, description="Type-specific configuration"
    )
    sort_order: int = Field(default=0, description="Sort order for field")
    entity_type: EntityType | None = Field(
        None, description="Entity type (required for top-level fields, inherited for nested fields)"
    )
    parent_id: str | None = Field(
        None, description="Parent field ID (required when adding via update endpoint)"
    )
    sub_fields: list["CreateCustomFieldRequest"] = Field(
        default_factory=list,
        description="Sub-fields for object type (only valid when field_type='object')",
    )

    @model_validator(mode="after")
    def validate_type_config(self) -> "CreateCustomFieldRequest":
        """Validate type_config matches field_type."""
        self.type_config = validate_and_normalize_type_config(self.field_type, self.type_config)
        return self

    @model_validator(mode="after")
    def validate_sub_fields_only_for_object(self) -> "CreateCustomFieldRequest":
        """Validate sub_fields can only be provided for object type."""
        if self.sub_fields and self.field_type != FieldType.OBJECT:
            raise ValidationException(
                message_key="custom_fields.errors.sub_fields_only_for_object",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self

    @staticmethod
    def validate_nesting_depth_iterative(request: "CreateCustomFieldRequest") -> None:
        """Validate that nesting depth does not exceed maximum allowed depth.

        Uses iterative approach with a queue to avoid recursion.

        Args:
            request: Root field request to validate

        Raises:
            ValidationException: If nesting depth exceeds maximum
        """
        # Queue: (field, depth)
        queue: deque[tuple["CreateCustomFieldRequest", int]] = deque([(request, 0)])

        while queue:
            field, depth = queue.popleft()

            if depth >= MAX_NESTING_DEPTH:
                raise ValidationException(
                    message_key="custom_fields.errors.max_nesting_depth_exceeded",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

            # Add sub-fields to queue for processing
            if field.sub_fields:
                for sub_field in field.sub_fields:
                    queue.append((sub_field, depth + 1))


class FlatFieldUpdateRequest(BaseModel):
    """Request schema for updating a field (flat ID-based design).

    All fields are optional except id. Only provided fields will be updated.
    Parent reassignment is not allowed.
    """

    id: str = Field(..., description="Field ID to update")
    field_name: str | None = Field(None, min_length=1, max_length=200, description="Field name")
    description: str | None = Field(None, max_length=1000, description="Field description")
    field_type: FieldType | None = Field(None, description="Field type")
    type_config: dict[str, Any] | None = Field(None, description="Type-specific configuration")
    show_on_create: bool | None = Field(None, description="Show on create form")
    show_on_detail: bool | None = Field(None, description="Show on detail page")
    is_required: bool | None = Field(None, description="Field is required")
    sort_order: int | None = Field(None, description="Sort order for field")

    @model_validator(mode="after")
    def validate_type_config(self) -> "FlatFieldUpdateRequest":
        """Validate type_config is provided and valid when field_type requires it."""
        if self.type_config is not None and self.field_type is None:
            raise ValidationException(
                message_key="custom_fields.errors.field_type_required_for_type_config",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if self.field_type is None:
            return self
        if field_type_requires_type_config(self.field_type) and self.type_config is None:
            raise ValidationException(
                message_key="custom_fields.errors.type_config_required_for_field_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        self.type_config = validate_and_normalize_type_config(
            self.field_type, self.type_config or {}
        )
        return self


class UpdateCustomFieldRequest(BaseModel):
    """Request schema for updating a custom field (PATCH semantics, flat ID-based design).

    Supports updating root field and flat delta operations on subtree:
    - update: Array of field updates (id + updatable fields)
    - remove: Array of field IDs to delete (cascades to descendants)
    - add: Array of new field definitions
    """

    field_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Root field name",
    )
    description: str | None = Field(None, max_length=1000, description="Root field description")
    field_type: FieldType | None = Field(None, description="Root field type")
    type_config: dict[str, Any] | None = Field(
        None, description="Root field type-specific configuration"
    )
    show_on_create: bool | None = Field(None, description="Root field show on create form")
    show_on_detail: bool | None = Field(None, description="Root field show on detail page")
    is_required: bool | None = Field(None, description="Root field is required")
    sort_order: int | None = Field(None, description="Root field sort order")
    update: list[FlatFieldUpdateRequest] | None = Field(
        None, description="Fields to update (flat array with id)", max_length=100
    )
    remove: list[str] | None = Field(
        None, description="Field IDs to remove (deletes all descendants)", max_length=100
    )
    add: list[CreateCustomFieldRequest] | None = Field(
        None,
        description="New fields to add",
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_type_config(self) -> "UpdateCustomFieldRequest":
        """Validate type_config is provided and valid when field_type requires it."""
        if self.type_config is not None and self.field_type is None:
            raise ValidationException(
                message_key="custom_fields.errors.field_type_required_for_type_config",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if self.field_type is None:
            return self
        if field_type_requires_type_config(self.field_type) and self.type_config is None:
            raise ValidationException(
                message_key="custom_fields.errors.type_config_required_for_field_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        self.type_config = validate_and_normalize_type_config(
            self.field_type, self.type_config or {}
        )
        return self

    @model_validator(mode="after")
    def validate_add_has_parent_id(self) -> "UpdateCustomFieldRequest":
        """Validate all add items have parent_id."""
        if self.add:
            for item in self.add:
                if item.parent_id is None:
                    raise ValidationException(
                        message_key="custom_fields.errors.parent_id_required_for_add",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
        return self


class BaseFieldResponse(BaseModel):
    """Base response schema for field (shared fields)."""

    id: str = Field(..., description="Unique field identifier")
    field_name: str = Field(..., description="Display name of the field")
    field_key: str = Field(..., description="Slug/key used in API and storage")
    description: str | None = Field(None, description="Brief description of the field")
    field_type: str = Field(..., description="Field type (text, number, date, etc.)")
    show_on_create: bool = Field(..., description="Whether to show on create/edit forms")
    show_on_detail: bool = Field(..., description="Whether to show on detail/listing pages")
    is_required: bool = Field(..., description="Whether the field is required")
    type_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific configuration (options, range, etc.)",
    )
    sort_order: int = Field(..., description="Display order among fields")
    is_active: bool = Field(..., description="Whether the field is active")
    entity_type: str | None = Field(None, description="Entity type if applicable")


class SubFieldResponse(BaseFieldResponse):
    """Response schema for sub-field (nested field under an object type).

    Supports recursive nesting - sub-fields can themselves contain sub-fields.
    """

    parent_id: str = Field(..., description="Parent object field ID")
    sub_fields: list["SubFieldResponse"] = Field(
        default_factory=list,
        description="Nested sub-fields for object type (supports recursive nesting)",
    )


class CustomFieldResponse(BaseFieldResponse):
    """Response schema for custom field (top-level or nested).

    Supports recursive nesting - sub-fields can themselves contain sub-fields.
    """

    parent_id: str | None = Field(None, description="Parent field ID for sub-fields")
    sub_fields: list[SubFieldResponse] = Field(
        default_factory=list,
        description="Nested sub-fields for object type (supports recursive nesting)",
    )


# Update forward references for recursive models
FlatFieldUpdateRequest.model_rebuild()
