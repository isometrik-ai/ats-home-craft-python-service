"""Email template builder schemas."""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.custom_fields import (
    MAX_NESTING_DEPTH,
    validate_and_normalize_type_config,
)
from apps.user_service.app.schemas.enums import (
    EmailTemplateStatus,
    EmailTemplateType,
    FieldType,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

VARIABLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class EmailTemplateVariableDefinition(BaseModel):
    """Client-facing variable definition (no id; ids are assigned by the backend on persist)."""

    model_config = ConfigDict(extra="forbid")

    variable_key: str = Field(..., min_length=1, max_length=100, description="Placeholder key")
    field_name: str = Field(..., min_length=1, max_length=200, description="Display label")
    description: str | None = Field(None, max_length=1000)
    field_type: FieldType = Field(..., description="Field type")
    type_config: dict[str, Any] = Field(default_factory=dict)
    is_required: bool = Field(default=False)
    default_value: Any | None = Field(None, description="Fallback value for scalar types")
    sort_order: int = Field(default=0, ge=0)
    sub_fields: list[EmailTemplateVariableDefinition] = Field(default_factory=list)

    @field_validator("variable_key")
    @classmethod
    def validate_variable_key_format(cls, value: str) -> str:
        """Enforce snake_case variable keys used in {{.key}} placeholders."""
        normalized = value.strip().lower()
        if not VARIABLE_KEY_PATTERN.match(normalized):
            raise ValidationException(
                message_key="email_templates.errors.invalid_variable_key",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return normalized

    @model_validator(mode="after")
    def validate_type_config(self) -> EmailTemplateVariableDefinition:
        """Validate type_config matches field_type."""
        self.type_config = validate_and_normalize_type_config(self.field_type, self.type_config)
        return self

    @model_validator(mode="after")
    def validate_sub_fields_only_for_object_or_list(self) -> EmailTemplateVariableDefinition:
        """Validate sub_fields can only be provided for object or list type."""
        if self.sub_fields and self.field_type not in (FieldType.OBJECT, FieldType.LIST):
            raise ValidationException(
                message_key="custom_fields.errors.sub_fields_only_for_object_or_list",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self

    @model_validator(mode="after")
    def validate_list_has_exactly_one_child(self) -> EmailTemplateVariableDefinition:
        """Validate list type has exactly one child field."""
        if self.field_type == FieldType.LIST and len(self.sub_fields) != 1:
            raise ValidationException(
                message_key="custom_fields.errors.list_must_have_exactly_one_child",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self

    @staticmethod
    def validate_nesting_depth_iterative(request: EmailTemplateVariableDefinition) -> None:
        """Validate nesting depth does not exceed MAX_NESTING_DEPTH."""
        queue: deque[tuple[EmailTemplateVariableDefinition, int]] = deque([(request, 0)])
        while queue:
            field, depth = queue.popleft()
            if depth >= MAX_NESTING_DEPTH:
                raise ValidationException(
                    message_key="custom_fields.errors.max_nesting_depth_exceeded",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            for sub_field in field.sub_fields:
                queue.append((sub_field, depth + 1))


class EmailTemplateVariableRequest(EmailTemplateVariableDefinition):
    """Persisted variable node returned from the API (includes server-assigned id)."""

    id: str = Field(..., description="Server-assigned variable id")
    sub_fields: list[EmailTemplateVariableRequest] = Field(default_factory=list)


class EmailTemplateVariableAddRequest(EmailTemplateVariableDefinition):
    """New variable to add on template update (flat parent_id, optional nested sub_fields)."""

    parent_id: str | None = Field(
        None,
        description="Parent variable id; omit for a root-level variable",
    )


class EmailTemplateVariableUpdateRequest(BaseModel):
    """Partial update for an existing variable by id."""

    id: str = Field(..., description="Variable id to update")
    variable_key: str | None = Field(None, min_length=1, max_length=100)
    field_name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    field_type: FieldType | None = None
    type_config: dict[str, Any] | None = None
    is_required: bool | None = None
    default_value: Any | None = None
    sort_order: int | None = Field(None, ge=0)

    @field_validator("variable_key")
    @classmethod
    def validate_variable_key_format(cls, value: str | None) -> str | None:
        """Enforce snake_case variable keys used in {{.key}} placeholders."""
        if value is None:
            return value
        normalized = value.strip().lower()
        if not VARIABLE_KEY_PATTERN.match(normalized):
            raise ValidationException(
                message_key="email_templates.errors.invalid_variable_key",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return normalized

    @model_validator(mode="after")
    def validate_type_config_when_field_type_set(self) -> EmailTemplateVariableUpdateRequest:
        """Validate type_config when field_type is provided on update."""
        if self.type_config is not None and self.field_type is None:
            raise ValidationException(
                message_key="custom_fields.errors.field_type_required_for_type_config",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if self.field_type is None:
            return self
        self.type_config = validate_and_normalize_type_config(
            self.field_type,
            self.type_config or {},
        )
        return self


class EmailTemplateVariablesMutation(BaseModel):
    """Delta operations for template variables (remove → update → add)."""

    model_config = ConfigDict(extra="forbid")

    add: list[EmailTemplateVariableAddRequest] | None = Field(
        None,
        description="Variables to add",
        max_length=100,
    )
    update: list[EmailTemplateVariableUpdateRequest] | None = Field(
        None,
        description="Variables to update by id",
        max_length=100,
    )
    remove: list[str] | None = Field(
        None,
        description="Variable ids to remove (includes descendants)",
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_at_least_one_operation(self) -> EmailTemplateVariablesMutation:
        """Require at least one add, update, or remove entry."""
        has_add = bool(self.add)
        has_update = bool(self.update)
        has_remove = bool(self.remove)
        if not (has_add or has_update or has_remove):
            raise ValidationException(
                message_key="email_templates.errors.empty_variables_mutation",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class CreateEmailTemplateRequest(BaseModel):
    """Request schema for creating an email template."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    template_type: EmailTemplateType
    subject: str | None = Field(
        None,
        max_length=500,
        description="Plain email subject line (no variable placeholders)",
    )
    html_content: str = Field(..., min_length=1)
    variables: list[EmailTemplateVariableDefinition] = Field(default_factory=list)
    status: EmailTemplateStatus = Field(default=EmailTemplateStatus.DRAFT)
    is_default: bool = Field(
        default=False,
        description="When true on a LAYOUT, marks the org default shell used by TRIGGER render",
    )

    @model_validator(mode="after")
    def validate_is_default_only_for_layout(self) -> CreateEmailTemplateRequest:
        """Only LAYOUT templates may be marked as the organization default."""
        if self.is_default and self.template_type != EmailTemplateType.LAYOUT:
            raise ValidationException(
                message_key="email_templates.errors.is_default_layout_only",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, value: str) -> str:
        """Disallow whitespace-only names."""
        normalized = value.strip()
        if not normalized:
            raise ValidationException(
                message_key="email_templates.errors.name_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return normalized


class UpdateEmailTemplateRequest(BaseModel):
    """Request schema for partially updating an email template."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=200)
    subject: str | None = Field(
        None,
        max_length=500,
        description="Plain email subject line (no variable placeholders)",
    )
    html_content: str | None = Field(None, min_length=1)
    variables: EmailTemplateVariablesMutation | None = None
    status: EmailTemplateStatus | None = None

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> UpdateEmailTemplateRequest:
        """Require at least one updatable field."""
        if (
            self.name is None
            and self.subject is None
            and self.html_content is None
            and self.variables is None
            and self.status is None
        ):
            raise ValidationException(
                message_key="email_templates.errors.empty_update_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, value: str | None) -> str | None:
        """Disallow whitespace-only names."""
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValidationException(
                message_key="email_templates.errors.name_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return normalized


class EmailTemplateListItem(BaseModel):
    """Summary row for list endpoints."""

    id: str
    name: str
    template_type: str
    status: str
    is_default: bool
    created_at: str | None = None
    updated_at: str | None = None


class EmailTemplateDetailResponse(BaseModel):
    """Full template detail including variables."""

    id: str
    name: str
    template_type: str
    status: str
    is_default: bool
    subject: str | None = None
    html_content: str
    variables: list[dict[str, Any]]
    created_at: str | None = None
    updated_at: str | None = None


class RenderEmailTemplateRequest(BaseModel):
    """Runtime values used to produce final HTML (preview / hand-off to mail providers)."""

    model_config = ConfigDict(extra="forbid")

    variable_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Map of variable_key to runtime value (e.g. brand, otp_code)",
    )
    layout_id: str | None = Field(
        None,
        description="Layout template id when rendering a TRIGGER; defaults to org default layout",
    )
    body_content: str | None = Field(
        None,
        description="Body HTML when rendering a LAYOUT only (replaces {{BODY_CONTENT}})",
    )


class RenderEmailTemplateResponse(BaseModel):
    """Rendered email HTML ready to send via any provider."""

    template_id: str
    template_type: str
    layout_id: str | None = None
    subject: str | None = None
    html_content: str
    resolved_variables: dict[str, Any]


class GenerateEmailTemplateWithAiRequest(BaseModel):
    """Request body for AI-assisted email template generation."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural-language prompt forwarded to the email template agent",
    )


class GenerateEmailTemplateWithAiResult(BaseModel):
    """Response for AI-assisted email template generation."""

    model_config = ConfigDict(extra="forbid")

    template_id: str


EmailTemplateVariableDefinition.model_rebuild()
EmailTemplateVariableRequest.model_rebuild()
