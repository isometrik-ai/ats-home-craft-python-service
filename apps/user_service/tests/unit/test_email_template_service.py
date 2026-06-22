"""Unit tests for EmailTemplateService validation helpers."""

import pytest

from apps.user_service.app.schemas.email_templates import (
    EmailTemplateVariableDefinition,
)
from apps.user_service.app.schemas.enums import EmailTemplateType, FieldType
from apps.user_service.app.services.email_template_service import EmailTemplateService
from apps.user_service.app.services.email_template_variable_validation import (
    EmailTemplateVariableValidator,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_layout_requires_body_content_and_body_inject():
    """Layout HTML must include BODY_CONTENT and body-inject id."""
    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._validate_template_html_rules(
            EmailTemplateType.LAYOUT,
            "<div>no markers</div>",
        )
    assert exc_info.value.message_key == "email_templates.errors.body_content_required"

    with pytest.raises(ValidationException):
        EmailTemplateService._validate_template_html_rules(
            EmailTemplateType.LAYOUT,
            '<div id="body-inject">only inject</div>',
        )


def test_trigger_forbids_body_content():
    """Trigger HTML must not include BODY_CONTENT."""
    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._validate_template_html_rules(
            EmailTemplateType.TRIGGER,
            "<p>{{BODY_CONTENT}}</p>",
        )
    assert exc_info.value.message_key == "email_templates.errors.body_content_not_allowed"


def test_html_placeholder_sync():
    """Variables and HTML placeholders must match."""
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="brand",
            field_name="Brand",
            field_type=FieldType.TEXT,
            default_value="Acme",
        )
    ]
    EmailTemplateService._validate_html_placeholders(
        "<p>{{.brand}}</p>",
        variables,
    )

    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._validate_html_placeholders(
            "<p>{{.brand}} {{.missing}}</p>",
            variables,
        )
    assert exc_info.value.message_key == "email_templates.errors.placeholder_undefined_variable"


def test_duplicate_variable_key_rejected():
    """Duplicate variable_key in tree raises."""
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="brand",
            field_name="Brand",
            field_type=FieldType.TEXT,
            default_value="A",
        ),
        EmailTemplateVariableDefinition(
            variable_key="brand",
            field_name="Brand 2",
            field_type=FieldType.TEXT,
            default_value="B",
        ),
    ]
    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._validate_unique_variable_keys(variables)
    assert exc_info.value.message_key == "email_templates.errors.duplicate_variable_key"


def test_validate_default_for_text_field():
    """Scalar default validation uses validator initialized with db connection."""
    validator = EmailTemplateVariableValidator(db_connection=None, user_context=None)
    result = validator.validate_default_for_field_type(
        "brand",
        FieldType.TEXT,
        {},
        is_required=False,
        default_value="House of Apps",
    )
    assert result == "House of Apps"


def test_substitute_variable_placeholders():
    """Runtime values replace {{.key}} tokens in HTML."""
    html = "<p>{{.brand}} — {{.code}}</p>"
    rendered = EmailTemplateService.substitute_variable_placeholders(
        html,
        {"brand": "House of Apps", "code": 42},
    )
    assert rendered == "<p>House of Apps — 42</p>"


def test_substitute_clears_unresolved_placeholders():
    """Placeholders without resolved values are removed from output."""
    html = "<p>Contact ({{.contact}}): {{.first_name}}</p>"
    rendered = EmailTemplateService.substitute_variable_placeholders(
        html,
        {"first_name": "Jordan"},
    )
    assert rendered == "<p>Contact (): Jordan</p>"


def test_format_address_for_html():
    """Address values render as a readable single line."""
    formatted = EmailTemplateService._format_value_for_html(
        {
            "address_line1": "500 Market St",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94104",
            "country": "US",
        }
    )
    assert formatted == "500 Market St, San Francisco, CA 94104, US"


def test_parse_template_id_from_agent_text_json() -> None:
    """Parse template_id from JSON agent response."""
    template_id = EmailTemplateService._parse_template_id_from_agent_text(
        '{"template_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}'
    )
    assert template_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_parse_template_id_from_agent_text_bare_uuid() -> None:
    """Parse template_id when agent returns a bare UUID string."""
    template_id = EmailTemplateService._parse_template_id_from_agent_text(
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    )
    assert template_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_parse_template_id_from_agent_text_invalid() -> None:
    """Return None when agent response does not contain a template id."""
    assert EmailTemplateService._parse_template_id_from_agent_text("not a template id") is None
