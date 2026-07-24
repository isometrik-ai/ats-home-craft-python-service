"""Unit tests for EmailTemplateService validation helpers."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.email_templates import (
    EmailTemplateVariableDefinition,
)
from apps.user_service.app.schemas.enums import EmailTemplateType, FieldType
from apps.user_service.app.services.email_template_service import EmailTemplateService
from apps.user_service.app.services.email_template_variable_validation import (
    EmailTemplateVariableValidator,
)
from libs.shared_utils.http_exceptions import ConflictException, ValidationException


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


def test_flatten_variable_keys_nested() -> None:
    """Nested sub_fields contribute keys to flatten set."""
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="root",
            field_name="Root",
            field_type=FieldType.OBJECT,
            default_value=None,
            sub_fields=[
                EmailTemplateVariableDefinition(
                    variable_key="child",
                    field_name="Child",
                    field_type=FieldType.TEXT,
                    default_value="x",
                )
            ],
        )
    ]
    keys = EmailTemplateService._flatten_variable_keys(variables)
    assert keys == {"root", "child"}


def test_format_value_for_html_bool_and_money() -> None:
    """Booleans and money dicts render predictably."""
    assert EmailTemplateService._format_value_for_html(True) == "Yes"
    assert EmailTemplateService._format_value_for_html(False) == "No"
    assert (
        EmailTemplateService._format_value_for_html({"amount": 99, "currency_code": "USD"})
        == "99 USD"
    )


def test_format_value_for_html_list() -> None:
    """Lists join formatted child values."""
    rendered = EmailTemplateService._format_value_for_html(["a", "b"])
    assert rendered == "a, b"


def test_variable_not_in_html_raises() -> None:
    """Defined variable missing from HTML is rejected."""
    variables = [
        EmailTemplateVariableDefinition(
            variable_key="brand",
            field_name="Brand",
            field_type=FieldType.TEXT,
            default_value="Acme",
        )
    ]
    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._validate_html_placeholders("<p>Hi</p>", variables)
    assert exc_info.value.message_key == "email_templates.errors.variable_not_in_html"


def test_assign_backend_variable_ids() -> None:
    """Storage nodes receive backend ids when missing."""
    nodes = [{"variable_key": "brand", "sub_fields": [{"variable_key": "code"}]}]
    out = EmailTemplateService._assign_backend_variable_ids(nodes)
    assert out[0]["id"]
    assert out[0]["sub_fields"][0]["id"]


def _layout_html() -> str:
    """Valid layout HTML for template tests."""
    return '<div id="body-inject">{{BODY_CONTENT}}</div><p>{{.brand}}</p>'


def _brand_variable() -> EmailTemplateVariableDefinition:
    """Single text variable used in template tests."""
    return EmailTemplateVariableDefinition(
        variable_key="brand",
        field_name="Brand",
        field_type=FieldType.TEXT,
        default_value="Acme",
    )


@pytest.fixture
def email_template_service(monkeypatch):
    """EmailTemplateService with mocked repository and validator."""
    from apps.user_service.app.utils.common_utils import UserContext

    mock_repo = MagicMock()
    mock_repo.create_template = AsyncMock()
    mock_repo.list_templates = AsyncMock(return_value=[])
    mock_repo.get_template_by_id = AsyncMock(return_value=None)
    mock_repo.update_template = AsyncMock()
    mock_repo.delete_template = AsyncMock()
    mock_repo.get_default_layout = AsyncMock(return_value=None)

    mock_validator = MagicMock()
    mock_validator.validate_variable_tree_defaults = MagicMock()
    mock_validator.resolve_runtime_variable_values = MagicMock(return_value={"brand": "Acme"})

    svc = EmailTemplateService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="u1", email="u@x.com", organization_id="org-1"),
        repository=mock_repo,
    )
    svc.variable_validator = mock_validator
    return svc


@pytest.mark.asyncio
async def test_create_email_template_persists(email_template_service):
    """create_email_template validates payload and returns audit snapshot."""
    from apps.user_service.app.schemas.email_templates import CreateEmailTemplateRequest
    from apps.user_service.app.schemas.enums import EmailTemplateStatus

    email_template_service.repository.create_template = AsyncMock(
        return_value={
            "id": "tpl-1",
            "organization_id": "org-1",
            "name": "Welcome",
            "template_type": "trigger",
            "status": "draft",
            "is_default": False,
            "subject": "Hi",
            "html_content": "<p>{{.brand}}</p>",
            "variables": [],
            "created_at": None,
            "updated_at": None,
        }
    )
    body = CreateEmailTemplateRequest(
        name="Welcome",
        template_type=EmailTemplateType.TRIGGER,
        html_content="<p>{{.brand}}</p>",
        variables=[_brand_variable()],
        status=EmailTemplateStatus.DRAFT,
    )
    snapshot = await email_template_service.create_email_template(body)
    assert snapshot["id"] == "tpl-1"
    email_template_service.repository.create_template.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_email_template_not_found(email_template_service):
    """get_email_template raises when template is missing."""
    from libs.shared_utils.http_exceptions import NotFoundException

    with pytest.raises(NotFoundException):
        await email_template_service.get_email_template("missing")


@pytest.mark.asyncio
async def test_list_email_templates(email_template_service):
    """list_email_templates maps repository rows to list items."""
    email_template_service.repository.list_templates = AsyncMock(
        return_value=[
            {
                "id": "tpl-1",
                "name": "Welcome",
                "template_type": "trigger",
                "status": "draft",
                "is_default": False,
                "created_at": None,
                "updated_at": None,
            }
        ]
    )
    items, total = await email_template_service.list_email_templates()
    assert total == 1
    assert items[0]["name"] == "Welcome"


@pytest.mark.asyncio
async def test_delete_email_template_blocks_default_layout(email_template_service):
    """Default layout templates cannot be deleted."""
    from libs.shared_utils.http_exceptions import ConflictException

    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "tpl-1",
            "name": "Default",
            "template_type": "layout",
            "status": "active",
            "is_default": True,
            "html_content": _layout_html(),
            "variables": [],
        }
    )
    with pytest.raises(ConflictException):
        await email_template_service.delete_email_template("tpl-1")


@pytest.mark.asyncio
async def test_render_trigger_template_with_layout(email_template_service):
    """render_email_template merges trigger body into layout shell."""
    from apps.user_service.app.schemas.email_templates import RenderEmailTemplateRequest

    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "trigger-1",
            "template_type": "trigger",
            "subject": "Hello",
            "html_content": "<p>{{.brand}}</p>",
            "variables": [
                {
                    "id": "v1",
                    "variable_key": "brand",
                    "field_name": "Brand",
                    "field_type": "text",
                    "default_value": "Acme",
                    "sub_fields": [],
                }
            ],
        }
    )
    email_template_service.repository.get_default_layout = AsyncMock(
        return_value={
            "id": "layout-1",
            "template_type": "layout",
            "html_content": _layout_html(),
            "variables": [],
        }
    )
    email_template_service.variable_validator.resolve_runtime_variable_values = MagicMock(
        side_effect=lambda _defs, values: values
    )
    body = RenderEmailTemplateRequest(variable_values={"brand": "House of Apps"})
    rendered = await email_template_service.render_email_template("trigger-1", body)
    assert rendered["layout_id"] == "layout-1"
    assert "House of Apps" in rendered["html_content"]
    assert "{{BODY_CONTENT}}" not in rendered["html_content"]


@pytest.mark.asyncio
async def test_render_layout_template(email_template_service):
    """render_email_template substitutes layout variables and body slot."""
    from apps.user_service.app.schemas.email_templates import RenderEmailTemplateRequest

    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "layout-1",
            "template_type": "layout",
            "html_content": _layout_html(),
            "variables": [
                {
                    "id": "v1",
                    "variable_key": "brand",
                    "field_name": "Brand",
                    "field_type": "text",
                    "default_value": "Acme",
                    "sub_fields": [],
                }
            ],
        }
    )
    body = RenderEmailTemplateRequest(body_content="<strong>Body</strong>")
    rendered = await email_template_service.render_email_template("layout-1", body)
    assert "Body" in rendered["html_content"]
    assert rendered["layout_id"] == "layout-1"


@pytest.mark.asyncio
async def test_generate_email_template_with_ai_success(monkeypatch, email_template_service):
    """generate_email_template_with_ai returns parsed template id."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        EmailTemplateService,
        "email_template_ai_generation_enabled",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(email_template_agent_id="agent-1")),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.call_strands_agent",
        AsyncMock(return_value={"text": '{"template_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}'}),
    )
    template_id = await email_template_service.generate_email_template_with_ai(query="welcome")
    assert template_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.mark.asyncio
async def test_update_email_template_success(email_template_service):
    """update_email_template returns before/after audit snapshots."""
    from apps.user_service.app.schemas.email_templates import UpdateEmailTemplateRequest

    existing = {
        "id": "tpl-1",
        "organization_id": "org-1",
        "name": "Old",
        "template_type": "trigger",
        "status": "draft",
        "is_default": False,
        "subject": "Hi",
        "html_content": "<p>{{.brand}}</p>",
        "variables": [
            {
                "id": "v1",
                "variable_key": "brand",
                "field_name": "Brand",
                "field_type": "text",
                "default_value": "Acme",
                "sub_fields": [],
            }
        ],
        "created_at": None,
        "updated_at": None,
    }
    updated = {**existing, "name": "New"}
    email_template_service.repository.get_template_by_id = AsyncMock(return_value=existing)
    email_template_service.repository.update_template = AsyncMock(return_value=updated)

    before, after = await email_template_service.update_email_template(
        "tpl-1",
        UpdateEmailTemplateRequest(name="New"),
    )
    assert before["name"] == "Old"
    assert after["name"] == "New"


@pytest.mark.asyncio
async def test_delete_email_template_success(email_template_service):
    """delete_email_template removes non-default templates."""
    existing = {
        "id": "tpl-1",
        "organization_id": "org-1",
        "name": "Welcome",
        "template_type": "trigger",
        "status": "draft",
        "is_default": False,
        "html_content": "<p>{{.brand}}</p>",
        "variables": [],
        "created_at": None,
        "updated_at": None,
    }
    email_template_service.repository.get_template_by_id = AsyncMock(return_value=existing)
    email_template_service.repository.delete_template = AsyncMock(return_value=existing)
    snapshot = await email_template_service.delete_email_template("tpl-1")
    assert snapshot["id"] == "tpl-1"


@pytest.mark.asyncio
async def test_get_email_template_success(email_template_service):
    """get_email_template returns detail payload."""
    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "tpl-1",
            "name": "Welcome",
            "template_type": "trigger",
            "status": "draft",
            "is_default": False,
            "subject": "Hi",
            "html_content": "<p>{{.brand}}</p>",
            "variables": [],
            "created_at": None,
            "updated_at": None,
        }
    )
    detail = await email_template_service.get_email_template("tpl-1")
    assert detail["name"] == "Welcome"


@pytest.mark.asyncio
async def test_generate_email_template_with_ai_not_configured(email_template_service):
    """generate_email_template_with_ai raises when AI is disabled."""
    from libs.shared_utils.http_exceptions import ServiceUnavailableException

    with pytest.raises(ServiceUnavailableException):
        await email_template_service.generate_email_template_with_ai(query="welcome")


def test_layout_requires_body_inject_single_quotes():
    """Layout HTML accepts single-quoted body-inject id."""
    EmailTemplateService._validate_template_html_rules(
        EmailTemplateType.LAYOUT,
        "<div id='body-inject'>{{BODY_CONTENT}}</div><p>{{.brand}}</p>",
    )


def test_layout_missing_body_inject_raises():
    """Layout HTML without body-inject id is rejected."""
    with pytest.raises(ValidationException) as exc_info:
        EmailTemplateService._validate_template_html_rules(
            EmailTemplateType.LAYOUT,
            "<div>{{BODY_CONTENT}}</div>",
        )
    assert exc_info.value.message_key == "email_templates.errors.body_inject_required"


def test_format_value_for_html_address_with_line2():
    """Address dicts include line2 in formatted output."""
    formatted = EmailTemplateService._format_value_for_html(
        {
            "address_line1": "1 Main",
            "address_line2": "Suite 5",
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "US",
        }
    )
    assert "Suite 5" in formatted


def test_parse_variables_from_row_non_list():
    """_parse_variables_from_row returns empty list for invalid JSON."""
    assert EmailTemplateService._parse_variables_from_row({"variables": {}}) == []


def test_raise_unique_violation_name_and_layout():
    """Unique violations map to specific conflict errors."""
    exc = UniqueViolationError("duplicate")
    exc.constraint_name = "uq_et_org_name"
    with pytest.raises(ConflictException) as info:
        EmailTemplateService._raise_unique_violation(exc)
    assert info.value.message_key == "email_templates.errors.name_exists"

    exc_layout = UniqueViolationError("duplicate")
    exc_layout.constraint_name = "uq_et_org_default_layout"
    with pytest.raises(ConflictException) as info2:
        EmailTemplateService._raise_unique_violation(exc_layout)
    assert info2.value.message_key == "email_templates.errors.default_layout_exists"


def test_raise_unique_violation_unknown_re_raises():
    """Unknown unique constraints propagate the original error."""
    exc = UniqueViolationError("duplicate")
    exc.constraint_name = "other"
    with pytest.raises(UniqueViolationError):
        EmailTemplateService._raise_unique_violation(exc)


def test_apply_variable_mutations_remove_update_add():
    """Variable mutations remove, patch, and append nodes."""
    from apps.user_service.app.schemas.email_templates import (
        EmailTemplateVariableAddRequest,
        EmailTemplateVariablesMutation,
        EmailTemplateVariableUpdateRequest,
    )

    stored = [
        {
            "id": "root-1",
            "variable_key": "brand",
            "field_name": "Brand",
            "field_type": "object",
            "default_value": None,
            "sub_fields": [
                {
                    "id": "child-1",
                    "variable_key": "code",
                    "field_name": "Code",
                    "field_type": "text",
                    "default_value": "OLD",
                    "sub_fields": [],
                }
            ],
        }
    ]
    mutation = EmailTemplateVariablesMutation(
        remove=["child-1"],
        update=[
            EmailTemplateVariableUpdateRequest(
                id="root-1",
                field_name="Brand Name",
            )
        ],
        add=[
            EmailTemplateVariableAddRequest(
                parent_id="root-1",
                variable_key="tagline",
                field_name="Tagline",
                field_type=FieldType.TEXT,
                default_value="Hello",
            )
        ],
    )
    result = EmailTemplateService._apply_variable_mutations(stored, mutation)
    assert result[0]["field_name"] == "Brand Name"
    assert {node["variable_key"] for node in result[0]["sub_fields"]} == {"tagline"}


def test_apply_variable_mutations_invalid_parent_raises():
    """Add mutation with missing parent id raises validation error."""
    from apps.user_service.app.schemas.email_templates import (
        EmailTemplateVariableAddRequest,
        EmailTemplateVariablesMutation,
    )

    stored = [
        {
            "id": "root-1",
            "variable_key": "brand",
            "field_name": "Brand",
            "field_type": "text",
            "default_value": "Acme",
            "sub_fields": [],
        }
    ]
    mutation = EmailTemplateVariablesMutation(
        add=[
            EmailTemplateVariableAddRequest(
                parent_id="missing",
                variable_key="code",
                field_name="Code",
                field_type=FieldType.TEXT,
                default_value="X",
            )
        ]
    )
    with pytest.raises(ValidationException):
        EmailTemplateService._apply_variable_mutations(stored, mutation)


def test_attach_variable_child_rejects_scalar_parent():
    """Scalar parents cannot receive nested variables."""
    roots = [
        {
            "id": "root-1",
            "variable_key": "brand",
            "field_name": "Brand",
            "field_type": "text",
            "sub_fields": [],
        }
    ]
    child = {
        "id": "child-1",
        "variable_key": "code",
        "field_name": "Code",
        "field_type": "text",
        "sub_fields": [],
    }
    with pytest.raises(ValidationException):
        EmailTemplateService._attach_variable_child(roots, "root-1", child)


def test_parse_template_id_from_agent_text_code_fence():
    """Parse template_id from fenced JSON agent response."""
    raw = '```json\n{"template_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}\n```'
    template_id = EmailTemplateService._parse_template_id_from_agent_text(raw)
    assert template_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.mark.asyncio
async def test_create_email_template_unique_violation(email_template_service):
    """create_email_template maps unique violations to conflict errors."""
    from apps.user_service.app.schemas.email_templates import CreateEmailTemplateRequest
    from apps.user_service.app.schemas.enums import EmailTemplateStatus

    exc = UniqueViolationError("duplicate")
    exc.constraint_name = "uq_et_org_name"
    email_template_service.repository.create_template = AsyncMock(side_effect=exc)

    body = CreateEmailTemplateRequest(
        name="Welcome",
        template_type=EmailTemplateType.TRIGGER,
        html_content="<p>{{.brand}}</p>",
        variables=[_brand_variable()],
        status=EmailTemplateStatus.DRAFT,
    )
    with pytest.raises(ConflictException):
        await email_template_service.create_email_template(body)


@pytest.mark.asyncio
async def test_update_email_template_not_found_after_update(email_template_service):
    """update_email_template raises when repository returns no row."""
    from apps.user_service.app.schemas.email_templates import UpdateEmailTemplateRequest
    from libs.shared_utils.http_exceptions import NotFoundException

    existing = {
        "id": "tpl-1",
        "organization_id": "org-1",
        "name": "Old",
        "template_type": "trigger",
        "status": "draft",
        "is_default": False,
        "subject": "Hi",
        "html_content": "<p>{{.brand}}</p>",
        "variables": [],
        "created_at": None,
        "updated_at": None,
    }
    email_template_service.repository.get_template_by_id = AsyncMock(return_value=existing)
    email_template_service.repository.update_template = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await email_template_service.update_email_template(
            "tpl-1",
            UpdateEmailTemplateRequest(name="New"),
        )


@pytest.mark.asyncio
async def test_delete_email_template_not_found_after_delete(email_template_service):
    """delete_email_template raises when repository delete returns None."""
    from libs.shared_utils.http_exceptions import NotFoundException

    existing = {
        "id": "tpl-1",
        "organization_id": "org-1",
        "name": "Welcome",
        "template_type": "trigger",
        "status": "draft",
        "is_default": False,
        "html_content": "<p>{{.brand}}</p>",
        "variables": [],
        "created_at": None,
        "updated_at": None,
    }
    email_template_service.repository.get_template_by_id = AsyncMock(return_value=existing)
    email_template_service.repository.delete_template = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await email_template_service.delete_email_template("tpl-1")


@pytest.mark.asyncio
async def test_render_trigger_missing_layout_body_token(email_template_service):
    """render_email_template rejects layouts missing BODY_CONTENT slot."""
    from apps.user_service.app.schemas.email_templates import RenderEmailTemplateRequest

    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "trigger-1",
            "template_type": "trigger",
            "subject": "Hello",
            "html_content": "<p>{{.brand}}</p>",
            "variables": [],
        }
    )
    email_template_service.repository.get_default_layout = AsyncMock(
        return_value={
            "id": "layout-1",
            "template_type": "layout",
            "html_content": "<div>No slot</div>",
            "variables": [],
        }
    )
    with pytest.raises(ValidationException):
        await email_template_service.render_email_template(
            "trigger-1",
            RenderEmailTemplateRequest(variable_values={"brand": "Acme"}),
        )


@pytest.mark.asyncio
async def test_render_layout_missing_body_token(email_template_service):
    """render_email_template rejects layout templates missing BODY_CONTENT."""
    from apps.user_service.app.schemas.email_templates import RenderEmailTemplateRequest

    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "layout-1",
            "template_type": "layout",
            "html_content": "<div>No slot</div>",
            "variables": [],
        }
    )
    with pytest.raises(ValidationException):
        await email_template_service.render_email_template(
            "layout-1",
            RenderEmailTemplateRequest(body_content="<p>Body</p>"),
        )


@pytest.mark.asyncio
async def test_get_layout_for_render_custom_layout_not_found(email_template_service):
    """_get_layout_for_render raises when explicit layout id is missing."""
    from libs.shared_utils.http_exceptions import NotFoundException

    email_template_service.repository.get_template_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await email_template_service._get_layout_for_render("org-1", "missing-layout")


@pytest.mark.asyncio
async def test_get_layout_for_render_wrong_type(email_template_service):
    """_get_layout_for_render rejects non-layout template ids."""
    email_template_service.repository.get_template_by_id = AsyncMock(
        return_value={
            "id": "trigger-1",
            "template_type": "trigger",
            "html_content": "<p>{{.brand}}</p>",
        }
    )
    with pytest.raises(ValidationException):
        await email_template_service._get_layout_for_render("org-1", "trigger-1")


@pytest.mark.asyncio
async def test_get_layout_for_render_default_missing(email_template_service):
    """_get_layout_for_render raises when default layout is missing."""
    from libs.shared_utils.http_exceptions import NotFoundException

    email_template_service.repository.get_default_layout = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await email_template_service._get_layout_for_render("org-1", None)


@pytest.mark.asyncio
async def test_generate_email_template_with_ai_http_error(monkeypatch, email_template_service):
    """generate_email_template_with_ai maps HTTP failures to service unavailable."""
    from types import SimpleNamespace

    from libs.shared_utils.http_exceptions import ServiceUnavailableException

    monkeypatch.setattr(
        EmailTemplateService,
        "email_template_ai_generation_enabled",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(email_template_agent_id="agent-1")),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.call_strands_agent",
        AsyncMock(side_effect=httpx.HTTPError("down")),
    )
    with pytest.raises(ServiceUnavailableException):
        await email_template_service.generate_email_template_with_ai(query="welcome")


@pytest.mark.asyncio
async def test_generate_email_template_with_ai_empty_response(monkeypatch, email_template_service):
    """generate_email_template_with_ai rejects empty agent text."""
    from types import SimpleNamespace

    from libs.shared_utils.http_exceptions import ServiceUnavailableException

    monkeypatch.setattr(
        EmailTemplateService,
        "email_template_ai_generation_enabled",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(email_template_agent_id="agent-1")),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.call_strands_agent",
        AsyncMock(return_value={"text": "   "}),
    )
    with pytest.raises(ServiceUnavailableException):
        await email_template_service.generate_email_template_with_ai(query="welcome")


@pytest.mark.asyncio
async def test_generate_email_template_with_ai_invalid_template_id(
    monkeypatch, email_template_service
):
    """generate_email_template_with_ai rejects responses without template id."""
    from types import SimpleNamespace

    from libs.shared_utils.http_exceptions import ServiceUnavailableException

    monkeypatch.setattr(
        EmailTemplateService,
        "email_template_ai_generation_enabled",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(email_template_agent_id="agent-1")),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.call_strands_agent",
        AsyncMock(return_value={"text": "no id here"}),
    )
    with pytest.raises(ServiceUnavailableException):
        await email_template_service.generate_email_template_with_ai(query="welcome")


def test_attach_variable_child_rejects_second_list_child():
    """List parents accept only one child variable."""
    roots = [
        {
            "id": "list-1",
            "variable_key": "items",
            "field_name": "Items",
            "field_type": "list",
            "sub_fields": [
                {
                    "id": "child-1",
                    "variable_key": "item",
                    "field_name": "Item",
                    "field_type": "text",
                    "sub_fields": [],
                }
            ],
        }
    ]
    child = {
        "id": "child-2",
        "variable_key": "extra",
        "field_name": "Extra",
        "field_type": "text",
        "sub_fields": [],
    }
    with pytest.raises(ValidationException):
        EmailTemplateService._attach_variable_child(roots, "list-1", child)


def test_apply_variable_field_type_change_clears_sub_fields():
    """Changing object/list to scalar clears nested sub_fields."""
    node = {
        "id": "obj-1",
        "field_type": "object",
        "sub_fields": [{"id": "child-1", "variable_key": "x"}],
    }
    EmailTemplateService._apply_variable_field_type_change(node, FieldType.TEXT)
    assert node["field_type"] == "text"
    assert node["sub_fields"] == []


@pytest.mark.asyncio
async def test_update_email_template_with_variable_mutations(email_template_service):
    """update_email_template re-validates merged variable tree."""
    from apps.user_service.app.schemas.email_templates import (
        EmailTemplateVariablesMutation,
        EmailTemplateVariableUpdateRequest,
        UpdateEmailTemplateRequest,
    )

    existing = {
        "id": "tpl-1",
        "organization_id": "org-1",
        "name": "Welcome",
        "template_type": "trigger",
        "status": "draft",
        "is_default": False,
        "subject": "Hi",
        "html_content": "<p>{{.brand}}</p>",
        "variables": [
            {
                "id": "v1",
                "variable_key": "brand",
                "field_name": "Brand",
                "field_type": "text",
                "default_value": "Acme",
                "sub_fields": [],
            }
        ],
        "created_at": None,
        "updated_at": None,
    }
    updated = {**existing, "name": "Updated"}
    email_template_service.repository.get_template_by_id = AsyncMock(return_value=existing)
    email_template_service.repository.update_template = AsyncMock(return_value=updated)

    before, after = await email_template_service.update_email_template(
        "tpl-1",
        UpdateEmailTemplateRequest(
            variables=EmailTemplateVariablesMutation(
                update=[EmailTemplateVariableUpdateRequest(id="v1", field_name="Brand Name")]
            )
        ),
    )
    assert before["name"] == "Welcome"
    assert after["name"] == "Updated"


@pytest.mark.asyncio
async def test_generate_email_template_with_ai_missing_org(monkeypatch, email_template_service):
    """generate_email_template_with_ai requires organization context."""
    from types import SimpleNamespace

    email_template_service.user_context.organization_id = None
    monkeypatch.setattr(
        EmailTemplateService,
        "email_template_ai_generation_enabled",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service.shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(email_template_agent_id="agent-1")),
    )
    with pytest.raises(ValidationException):
        await email_template_service.generate_email_template_with_ai(query="welcome")
