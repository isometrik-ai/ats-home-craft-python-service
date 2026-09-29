"""Extended unit tests for common_utils helpers."""

from __future__ import annotations

import datetime as dt
import json
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from starlette.requests import Request

import apps.user_service.app.utils.common_utils as common_utils_module
from apps.user_service.app.schemas.enums import ClientStatus, ProjectMemberRole
from apps.user_service.app.utils.common_utils import (
    PerformanceTimer,
    UserContext,
    check_any_permissions,
    check_permissions,
    coerce_json_list,
    ensure_crm_or_resident_project_access,
    ensure_daily_help_reviewer_access,
    ensure_project_staff_management_access_for_context,
    ensure_resident_access_for_unit,
    ensure_resident_or_crm_contact_access,
    ensure_security_project_member_access,
    ensure_staff_project_access_for_context,
    ensure_staff_project_access_optional,
    enum_member_title_label,
    extract_audit_data_value,
    extract_notice_viewer_context,
    extract_onboarding_contact_context,
    extract_user_context,
    format_iso_datetime,
    format_permissions_data,
    generate_random_password,
    get_nested,
    handle_api_exceptions,
    hash_token,
    json_dumps_or_none,
    name_to_email_domain_label,
    normalize_nested_addresses_for_audit,
    parse_flexible_date,
    parse_json_any,
    parse_json_field,
    require_any_permission,
    require_organization_creator,
    require_permission,
    require_super_admin,
    safe_json_loads,
    safe_str,
    serialize_jsonb_param,
    serialize_pydantic_models,
    set_audit_old_data_from_user,
    title_case_field,
    user_has_any_permission,
    validate_uuid_format,
)
from libs.shared_utils.common_query import (
    CONTACTS_MANAGEMENT_VIEW,
    DAILY_HELP_MANAGEMENT_CREATE,
    PROJECT_MEMBERS_MANAGE_ASSIGNED,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    RESIDENT_MANAGEMENT_VIEW,
    SETTINGS_MANAGEMENT_VIEW,
    VISITOR_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.project_role_defaults import COMMUNITY_ADMIN_SLUG


class _SampleEnum(Enum):
    ACTIVE = "active"


class _SampleModel(BaseModel):
    name: str


def test_enum_member_title_label():
    """Enum member names become title-case labels."""
    assert enum_member_title_label(ClientStatus.ACTIVE) == "Active"


def test_name_to_email_domain_label():
    """Organization names normalize to domain-safe labels."""
    assert name_to_email_domain_label("T's Org & Co") == "t-s-org-and-co"


def test_coerce_json_list_from_string():
    """JSON string arrays coerce to Python lists."""
    assert coerce_json_list('["a","b"]') == ["a", "b"]


def test_coerce_json_list_invalid_string():
    """Invalid JSON strings coerce to empty lists."""
    assert coerce_json_list("{not-json") == []


def test_parse_json_field_dict_and_list():
    """Parser accepts dicts and JSON-encoded lists."""
    assert parse_json_field({"a": 1}) == {"a": 1}
    assert parse_json_field('["x"]') == ["x"]


def test_parse_json_any_defaults():
    """parse_json_any returns defaults for missing values."""
    assert parse_json_any(None, default=[]) == []
    assert parse_json_any('{"a":1}', default={}) == {"a": 1}


def test_safe_str_and_title_case_field():
    """String helpers normalize ids and field labels."""
    assert safe_str(None) == ""
    assert safe_str(42) == "42"
    assert title_case_field("company_id") == "company"


def test_get_nested_path():
    """Dotted paths resolve nested dict values."""
    data = {"a": {"b": {"c": 1}}}
    assert get_nested(data, "a.b.c") == 1
    assert get_nested(data, "a.missing") is None


def test_hash_token_stable():
    """Token hashing is deterministic SHA256 hex."""
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64


def test_serialize_jsonb_param():
    """JSONB columns serialize dict/list payloads."""
    cols = frozenset({"meta"})
    assert serialize_jsonb_param("meta", {"a": 1}, cols) == json.dumps({"a": 1})
    assert serialize_jsonb_param("name", "Acme", cols) == "Acme"


def test_json_dumps_or_none():
    """json_dumps_or_none preserves None and encodes empty lists."""
    assert json_dumps_or_none(None) is None
    assert json_dumps_or_none([]) == "[]"


def test_serialize_pydantic_models():
    """Nested pydantic models and enums serialize recursively."""
    payload = {"status": _SampleEnum.ACTIVE, "model": _SampleModel(name="Acme")}
    out = serialize_pydantic_models(payload)
    assert out["status"] == "active"
    assert out["model"]["name"] == "Acme"


def test_generate_random_password_meets_rules():
    """Generated passwords satisfy complexity requirements."""
    password = generate_random_password(12)
    assert len(password) == 12
    assert any(ch.isupper() for ch in password)
    assert any(ch.islower() for ch in password)
    assert any(ch.isdigit() for ch in password)
    assert any(not ch.isalnum() for ch in password)


def test_normalize_nested_addresses_for_audit():
    """Address audit snapshots stringify ids and timestamps."""
    normalized = {
        "addresses": [
            {
                "id": 1,
                "company_id": 2,
                "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                "address_data": '{"line1":"Main"}',
            }
        ]
    }
    normalize_nested_addresses_for_audit(normalized, parent_fk_field="company_id")
    addr = normalized["addresses"][0]
    assert addr["id"] == "1"
    assert addr["company_id"] == "2"
    address_data = addr["address_data"]
    assert isinstance(address_data, dict)
    assert address_data["line1"] == "Main"


def test_extract_audit_data_value():
    """Audit extractor reads dotted changed-field paths."""
    audit_values = {"data": {"name": "Acme", "nested": {"city": "Mumbai"}}}
    assert extract_audit_data_value(audit_values, "name") == "Acme"
    assert extract_audit_data_value(audit_values, "data.nested.city") == "Mumbai"
    assert extract_audit_data_value(None, "name") is None


def test_parse_flexible_date_formats():
    """parse_flexible_date accepts ISO and slash-delimited dates."""
    assert parse_flexible_date("2026-01-15").isoformat() == "2026-01-15"
    assert parse_flexible_date("01/15/2026").isoformat() == "2026-01-15"
    assert parse_flexible_date(None) is None
    assert parse_flexible_date("") is None


def test_parse_flexible_date_invalid_raises():
    """Unparseable date strings raise ValueError."""
    with pytest.raises(ValueError):
        parse_flexible_date("not-a-date")


def test_format_iso_datetime_variants():
    """format_iso_datetime handles None, strings, and datetimes."""
    ts = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert format_iso_datetime(None) is None
    assert format_iso_datetime("2026-01-01T12:00:00Z") == "2026-01-01T12:00:00Z"
    assert format_iso_datetime(ts) == ts.isoformat()


def test_safe_json_loads():
    """safe_json_loads returns default on invalid JSON."""
    assert safe_json_loads('{"a": 1}') == {"a": 1}
    assert safe_json_loads("{bad", default=[]) == []
    assert safe_json_loads(None, default={}) == {}


def test_validate_uuid_format_valid_and_invalid():
    """validate_uuid_format accepts UUIDs and rejects bad values."""
    validate_uuid_format("550e8400-e29b-41d4-a716-446655440000", "org ID")
    with pytest.raises(ValidationException):
        validate_uuid_format("not-a-uuid", "org ID")


def test_format_permissions_data_empty_and_rows():
    """format_permissions_data maps DB rows to PermissionItem objects."""
    assert format_permissions_data([]) == []
    items = format_permissions_data(
        [
            {
                "id": 1,
                "name": "Manage Users",
                "code": "users.manage",
                "category": "users",
                "description": "Manage users",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    assert items[0].code == "users.manage"
    assert items[0].created_at == "2026-01-01T00:00:00Z"


def test_set_audit_old_data_from_user():
    """set_audit_old_data_from_user stores normalized audit snapshot."""
    request = Request({"type": "http", "method": "DELETE", "path": "/", "headers": []})
    set_audit_old_data_from_user(
        request,
        {
            "user_id": "u-1",
            "email": "u@example.com",
            "organization_id": "org-1",
            "first_name": "Jane",
            "joined_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            "last_active_at": dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc),
        },
    )
    audit = request.state.raw_audit_old_data
    assert audit["user_id"] == "u-1"
    assert audit["email"] == "u@example.com"
    assert audit["joined_at"].startswith("2026-01-01")
    assert audit["last_active_at"].startswith("2026-01-02")


def test_performance_timer_elapsed():
    """PerformanceTimer tracks elapsed milliseconds."""
    timer = PerformanceTimer(operation_name="op")
    elapsed = timer.checkpoint()
    assert elapsed >= 0
    assert timer.total_time() >= elapsed


def test_parse_flexible_date_datetime_and_type_error():
    """parse_flexible_date accepts datetime objects and rejects bad types."""
    ts = dt.datetime(2026, 3, 15, 12, 0)
    assert parse_flexible_date(ts) == dt.date(2026, 3, 15)
    assert parse_flexible_date(dt.date(2026, 3, 15)) == dt.date(2026, 3, 15)
    with pytest.raises(TypeError):
        parse_flexible_date(123)


def test_parse_flexible_date_year_first_numeric():
    """Numeric dates with year-first segments parse correctly."""
    assert parse_flexible_date("1992/11/02") == dt.date(1992, 11, 2)


def test_parse_json_field_empty_and_list():
    """parse_json_field handles empty strings and list passthrough."""
    assert parse_json_field("") == {}
    assert parse_json_field(["x"]) == ["x"]


def test_coerce_json_list_non_list_parsed():
    """coerce_json_list returns empty list when parsed JSON is not a list."""
    assert coerce_json_list('{"a": 1}') == []


def test_format_iso_datetime_fallback():
    """format_iso_datetime falls back to str() for unknown types."""
    assert format_iso_datetime(42) == "42"


def test_safe_json_loads_non_string_passthrough():
    """safe_json_loads returns non-string values unchanged."""
    assert safe_json_loads({"a": 1}) == {"a": 1}


def test_normalize_nested_addresses_skips_invalid_entries():
    """normalize_nested_addresses ignores non-list and non-dict entries."""
    normalized = {"addresses": "not-a-list"}
    normalize_nested_addresses_for_audit(normalized, parent_fk_field="contact_id")
    assert normalized["addresses"] == "not-a-list"

    normalized2 = {"addresses": ["bad", {"id": 1, "contact_id": 2}]}
    normalize_nested_addresses_for_audit(normalized2, parent_fk_field="contact_id")
    addresses = normalized2["addresses"]
    assert isinstance(addresses, list)
    assert len(addresses) == 1
    assert addresses[0]["id"] == "1"


def test_extract_audit_data_value_missing_data():
    """extract_audit_data_value returns None when data payload is absent."""
    assert extract_audit_data_value({"other": {}}, "name") is None


def test_serialize_pydantic_models_list_branch():
    """serialize_pydantic_models recurses into lists."""
    payload = [_SampleModel(name="A"), _SampleModel(name="B")]
    out = serialize_pydantic_models(payload)
    assert out[0]["name"] == "A"


@pytest.mark.asyncio
async def test_extract_user_context_from_session_cache():
    """extract_user_context reads organization_id from cached session context."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    ctx = await extract_user_context(current_user, MagicMock())
    assert ctx.organization_id == "org-1"
    assert ctx.user_type == "organization_member"


@pytest.mark.asyncio
async def test_extract_user_context_missing_user_id():
    """extract_user_context rejects tokens without user id."""
    with pytest.raises(ValidationException):
        await extract_user_context({"email": "u@example.com"}, MagicMock())


@pytest.mark.asyncio
async def test_extract_user_context_resolves_session():
    """extract_user_context resolves organization via session lookup."""
    current_user = {"sub": "user-1", "email": "u@example.com", "session_id": "sess-1"}
    with patch(
        "apps.user_service.app.utils.common_utils.resolve_session_context",
        AsyncMock(return_value={"organization_id": "org-2"}),
    ):
        ctx = await extract_user_context(current_user, MagicMock())
    assert ctx.organization_id == "org-2"


@pytest.mark.asyncio
async def test_extract_user_context_audit_context_fallback():
    """extract_user_context uses audit context when request state matches user."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.audit_user_context = {
        "user_id": "user-1",
        "organization_id": "org-audit",
    }
    current_user = {"sub": "user-1", "email": "u@example.com"}
    ctx = await extract_user_context(current_user, MagicMock(), request=request)
    assert ctx.organization_id == "org-audit"


@pytest.mark.asyncio
async def test_require_permission_forbidden():
    """require_permission raises when access check fails."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(return_value=False),
    ):
        with pytest.raises(ForbiddenException):
            await require_permission("users.manage", ctx, MagicMock(), "org-1")


@pytest.mark.asyncio
async def test_require_permission_list_codes():
    """require_permission accepts a list of permission codes."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    mock_check = AsyncMock(return_value=True)
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        mock_check,
    ):
        await require_permission(["a.read", "b.write"], ctx, MagicMock(), "org-1")
    assert mock_check.await_args.kwargs["permission_code"] == ["a.read", "b.write"]


@pytest.mark.asyncio
async def test_check_permissions_org_mismatch():
    """check_permissions rejects cross-organization access attempts."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with pytest.raises(ForbiddenException):
        await check_permissions(current_user, MagicMock(), "users.read", organization_id="org-2")


@pytest.mark.asyncio
async def test_extract_onboarding_contact_context_no_org():
    """extract_onboarding_contact_context requires organization in session."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {},
    }
    with pytest.raises(ValidationException):
        await extract_onboarding_contact_context(current_user, MagicMock())


@pytest.mark.asyncio
async def test_extract_onboarding_contact_context_missing_contact():
    """extract_onboarding_contact_context raises when active contact is missing."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_active_contact_by_user_id = AsyncMock(return_value=None)
        with pytest.raises(NotFoundException):
            await extract_onboarding_contact_context(current_user, MagicMock())


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_uses_contact_when_present():
    """Residents with an active contact profile use contact-based viewer context."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
    ) as mock_contacts_cls:
        mock_contacts_cls.return_value.get_active_contact_by_user_id = AsyncMock(
            return_value={"id": "contact-1", "user_id": "user-1"}
        )
        viewer = await extract_notice_viewer_context(
            current_user,
            MagicMock(),
            project_id="project-1",
        )
    assert viewer.contact_id == "contact-1"
    assert viewer.contact_user_id == "user-1"


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_uses_project_member_for_staff():
    """Staff/security without a contact profile resolve via project_members."""
    current_user = {
        "sub": "staff-1",
        "email": "staff@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    db = MagicMock()
    with (
        patch(
            "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
        ) as mock_contacts_cls,
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_contacts_cls.return_value.get_active_contact_by_user_id = AsyncMock(return_value=None)
        mock_projects_cls.return_value.get_active_member_with_role = AsyncMock(
            return_value={"role_slug": "security", "user_id": "staff-1"}
        )
        viewer = await extract_notice_viewer_context(
            current_user,
            db,
            project_id="project-1",
        )
    assert viewer.contact_id is None
    assert viewer.contact_user_id == "staff-1"
    assert viewer.project_member_role == "security"


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_denies_unassigned_staff():
    """Staff without project assignment cannot open the notice feed."""
    current_user = {
        "sub": "staff-1",
        "email": "staff@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with (
        patch(
            "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
        ) as mock_contacts_cls,
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_contacts_cls.return_value.get_active_contact_by_user_id = AsyncMock(return_value=None)
        mock_projects_cls.return_value.get_active_member_with_role = AsyncMock(return_value=None)
        with pytest.raises(ForbiddenException):
            await extract_notice_viewer_context(
                current_user,
                MagicMock(),
                project_id="project-1",
            )


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_missing_notice_returns_not_found():
    """Detail routes return not found when the notice id does not exist."""
    current_user = {
        "sub": "staff-1",
        "email": "staff@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    db = MagicMock()
    db.fetchval = AsyncMock(return_value=None)
    with patch(
        "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
    ) as mock_contacts_cls:
        mock_contacts_cls.return_value.get_active_contact_by_user_id = AsyncMock(return_value=None)
        with pytest.raises(NotFoundException):
            await extract_notice_viewer_context(
                current_user,
                db,
                notice_id="missing-notice",
            )


@pytest.mark.asyncio
async def test_require_organization_creator_forbidden():
    """require_organization_creator rejects non-owners."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with patch("apps.user_service.app.db.repositories.OrganizationRepository") as mock_repo_cls:
        mock_repo_cls.return_value.is_user_organization_owner = AsyncMock(return_value=False)
        with pytest.raises(ForbiddenException):
            await require_organization_creator(ctx, "org-1", MagicMock())


@pytest.mark.asyncio
async def test_require_super_admin_forbidden():
    """require_super_admin rejects non-admin users."""
    with patch(
        "apps.user_service.app.utils.common_utils.is_system_super_admin",
        AsyncMock(return_value=False),
    ):
        with pytest.raises(ForbiddenException):
            await require_super_admin({"sub": "user-1"})


@pytest.mark.asyncio
async def test_handle_api_exceptions_maps_errors():
    """handle_api_exceptions converts ValueError and generic exceptions."""

    @handle_api_exceptions("test op")
    async def _raises_value_error():
        raise ValueError("bad input")

    @handle_api_exceptions("test op")
    async def _raises_generic():
        raise RuntimeError("boom")

    @handle_api_exceptions("test op")
    async def _raises_http():
        raise HTTPException(status_code=404, detail="missing")

    with pytest.raises(ValidationException):
        await _raises_value_error()
    with pytest.raises(InternalServerErrorException):
        await _raises_generic()
    with pytest.raises(HTTPException):
        await _raises_http()


def test_name_to_email_domain_label_empty_fallback():
    """Empty labels fall back to org."""
    assert name_to_email_domain_label("   ") == "org"


def test_get_nested_invalid_path_and_non_dict():
    """get_nested rejects empty paths and non-dict intermediates."""
    assert get_nested({"a": 1}, "") is None
    assert get_nested({"a": "not-a-dict"}, "a.b") is None


def test_parse_json_field_non_dict_non_list():
    """parse_json_field returns empty dict for unsupported types."""
    assert parse_json_field(42) == {}


def test_parse_json_any_non_json_scalar():
    """parse_json_any returns default for unsupported scalar types."""
    assert parse_json_any(99, default="x") == "x"


def test_coerce_json_list_invalid_json_string():
    """coerce_json_list swallows parse errors."""
    assert coerce_json_list("{bad-json") == []


def test_serialize_pydantic_models_none():
    """serialize_pydantic_models preserves None."""
    assert serialize_pydantic_models(None) is None


def test_normalize_nested_addresses_formats_updated_at():
    """Address audit snapshots format updated_at timestamps."""
    normalized = {
        "addresses": [
            {
                "id": 1,
                "contact_id": 2,
                "updated_at": dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
            }
        ]
    }
    normalize_nested_addresses_for_audit(normalized, parent_fk_field="contact_id")
    assert normalized["addresses"][0]["updated_at"].startswith("2026-02-01")


def test_extract_audit_data_value_strips_data_prefix():
    """extract_audit_data_value supports data.* changed-field paths."""
    audit_values = {"data": {"city": "Pune"}}
    assert extract_audit_data_value(audit_values, "data.city") == "Pune"


def test_parse_flexible_date_numeric_mdy():
    """Variable-width numeric dates parse with year-last segments."""
    assert parse_flexible_date("11/2/1992") == dt.date(1992, 11, 2)


def test_parse_flexible_date_year_first_segment():
    """Year-first numeric segments parse when first part exceeds 31."""
    assert parse_flexible_date("1992/11/02") == dt.date(1992, 11, 2)


@pytest.mark.asyncio
async def test_extract_user_context_missing_email():
    """extract_user_context rejects tokens without email."""
    with pytest.raises(ValidationException):
        await extract_user_context({"sub": "user-1"}, MagicMock())


@pytest.mark.asyncio
async def test_extract_user_context_session_not_found():
    """extract_user_context rejects missing session context."""
    with patch(
        "apps.user_service.app.utils.common_utils.resolve_session_context",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(ValidationException):
            await extract_user_context(
                {"sub": "user-1", "email": "u@example.com", "session_id": "sess-1"},
                MagicMock(),
            )


@pytest.mark.asyncio
async def test_extract_user_context_internal_error():
    """Unexpected errors become InternalServerErrorException."""
    with patch(
        "apps.user_service.app.utils.common_utils.resolve_session_context",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        with pytest.raises(InternalServerErrorException):
            await extract_user_context(
                {"sub": "user-1", "email": "u@example.com", "session_id": "sess-1"},
                MagicMock(),
            )


@pytest.mark.asyncio
async def test_require_permission_success():
    """require_permission passes when access check succeeds."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(return_value=True),
    ):
        await require_permission("users.manage", ctx, MagicMock(), "org-1")


@pytest.mark.asyncio
async def test_require_permission_ceiling_codes():
    """Org ceiling permission codes route through require_any_permission."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    mock_any = AsyncMock()
    with patch(
        "apps.user_service.app.utils.common_utils.require_any_permission",
        mock_any,
    ):
        await require_permission(SETTINGS_MANAGEMENT_VIEW, ctx, MagicMock(), "org-1")
    mock_any.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_permissions_success():
    """check_permissions returns user context when permitted."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(return_value=True),
    ):
        ctx = await check_permissions(current_user, MagicMock(), "users.read")
    assert ctx.organization_id == "org-1"


@pytest.mark.asyncio
async def test_check_any_permissions_org_mismatch():
    """check_any_permissions rejects cross-organization access attempts."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with pytest.raises(ForbiddenException):
        await check_any_permissions(
            current_user,
            MagicMock(),
            ["users.read"],
            organization_id="org-2",
        )


@pytest.mark.asyncio
async def test_user_has_any_permission():
    """user_has_any_permission stops at the first matching code."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(side_effect=[False, True]),
    ):
        assert await user_has_any_permission(
            permission_codes=["a.read", "b.read"],
            user_context=ctx,
            db_connection=MagicMock(),
            organization_id="org-1",
        )


@pytest.mark.asyncio
async def test_require_any_permission_empty_codes():
    """require_any_permission rejects empty permission lists."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with pytest.raises(ForbiddenException):
        await require_any_permission([], ctx, MagicMock(), "org-1")


@pytest.mark.asyncio
async def test_extract_onboarding_contact_context_success():
    """extract_onboarding_contact_context returns user and contact rows."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_active_contact_by_user_id = AsyncMock(
            return_value={"id": "contact-1", "user_id": "user-1"}
        )
        ctx, contact = await extract_onboarding_contact_context(current_user, MagicMock())
    assert ctx.user_id == "user-1"
    assert contact["id"] == "contact-1"


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_resolves_project_from_notice():
    """Notice detail routes resolve project_id via notices table."""
    current_user = {
        "sub": "staff-1",
        "email": "staff@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    db = MagicMock()
    db.fetchval = AsyncMock(return_value="project-99")
    with (
        patch(
            "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
        ) as mock_contacts_cls,
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_contacts_cls.return_value.get_active_contact_by_user_id = AsyncMock(return_value=None)
        mock_projects_cls.return_value.get_active_member_with_role = AsyncMock(
            return_value={"role_slug": "staff", "user_id": "staff-1"}
        )
        viewer = await extract_notice_viewer_context(
            current_user,
            db,
            notice_id="notice-1",
        )
    assert viewer.project_member_role == "staff"
    db.fetchval.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_missing_org():
    """Notice viewer resolution requires organization in session."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {},
    }
    with pytest.raises(ValidationException):
        await extract_notice_viewer_context(current_user, MagicMock())


@pytest.mark.asyncio
async def test_require_organization_creator_success():
    """require_organization_creator passes for organization owners."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with patch("apps.user_service.app.db.repositories.OrganizationRepository") as mock_repo_cls:
        mock_repo_cls.return_value.is_user_organization_owner = AsyncMock(return_value=True)
        await require_organization_creator(ctx, "org-1", MagicMock())


@pytest.mark.asyncio
async def test_require_super_admin_success():
    """require_super_admin passes for system super admins."""
    with patch(
        "apps.user_service.app.utils.common_utils.is_system_super_admin",
        AsyncMock(return_value=True),
    ):
        await require_super_admin({"sub": "user-1"})


@pytest.mark.asyncio
async def test_ensure_staff_project_access_optional_without_project():
    """Optional project access falls back to org permission checks."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(return_value=True),
    ):
        ctx = await ensure_staff_project_access_optional(
            current_user,
            MagicMock(),
            project_id=None,
            permission_codes=CONTACTS_MANAGEMENT_VIEW,
        )
    assert ctx.organization_id == "org-1"


@pytest.mark.asyncio
async def test_ensure_crm_or_resident_project_access_org_scope():
    """CRM access without project_id uses org permissions."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(return_value=True),
    ):
        ctx = await ensure_crm_or_resident_project_access(
            current_user=current_user,
            db_connection=MagicMock(),
            project_id=None,
        )
    assert ctx.organization_id == "org-1"


@pytest.mark.asyncio
async def test_ensure_resident_or_crm_contact_access_unit_branch():
    """Unit-scoped resident access resolves project from unit_id."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with (
        patch(
            "apps.user_service.app.db.repositories.contact_units_repository.ContactUnitsRepository"
        ) as mock_units_cls,
        patch(
            "apps.user_service.app.utils.common_utils.ensure_staff_project_access_for_context",
            AsyncMock(
                return_value=UserContext(
                    user_id="user-1", email="u@example.com", organization_id="org-1"
                )
            ),
        ),
    ):
        mock_units_cls.return_value.get_unit_project = AsyncMock(
            return_value={"project_id": "project-1"}
        )
        ctx = await ensure_resident_or_crm_contact_access(
            current_user=current_user,
            db_connection=MagicMock(),
            unit_id="unit-1",
        )
    assert ctx.organization_id == "org-1"


@pytest.mark.asyncio
async def test_ensure_daily_help_reviewer_access_delegates():
    """Daily help reviewer access delegates to staff project access."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    expected = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.ensure_staff_project_access",
        AsyncMock(return_value=expected),
    ) as mock_staff:
        ctx = await ensure_daily_help_reviewer_access(
            current_user,
            MagicMock(),
            project_id="project-1",
        )
    assert ctx is expected
    mock_staff.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_security_project_member_access_requires_security_role():
    """Security routes reject members without the security role."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    staff_ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    member_lookup = AsyncMock(return_value={"role_slug": ProjectMemberRole.COMMUNITY_ADMIN.value})
    for_context_mock = AsyncMock(
        side_effect=[
            ForbiddenException(message_key="errors.forbidden"),
            staff_ctx,
        ]
    )
    with (
        patch.object(
            common_utils_module,
            "ensure_staff_project_access_for_context",
            for_context_mock,
        ),
        patch.object(
            common_utils_module,
            "extract_user_context",
            AsyncMock(return_value=staff_ctx),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_projects_cls.return_value.get_active_member_with_role = member_lookup
        with pytest.raises(ForbiddenException):
            await ensure_security_project_member_access(
                current_user,
                MagicMock(),
                project_id="project-1",
                permission_codes=RESIDENT_MANAGEMENT_VIEW,
            )

    assert for_context_mock.await_count == 2
    bypass_call = for_context_mock.await_args_list[0].kwargs
    assert bypass_call["permission_codes"] == DAILY_HELP_MANAGEMENT_CREATE
    assert bypass_call["require_action_permission"] is True
    fallback_call = for_context_mock.await_args_list[1].kwargs
    assert fallback_call["permission_codes"] == RESIDENT_MANAGEMENT_VIEW
    member_lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_project_staff_management_access_hq_manage():
    """HQ project member managers bypass community-admin assignment checks."""
    ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with (
        patch(
            "apps.user_service.app.utils.common_utils.ensure_staff_project_access_for_context",
            AsyncMock(return_value=ctx),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            AsyncMock(return_value=True),
        ),
    ):
        result = await ensure_project_staff_management_access_for_context(
            user_context=ctx,
            db_connection=MagicMock(),
            project_id="project-1",
        )
    assert result.user_id == "user-1"


@pytest.mark.asyncio
async def test_require_permission_internal_error():
    """require_permission wraps unexpected errors as internal failures."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(side_effect=RuntimeError("db")),
    ):
        with pytest.raises(InternalServerErrorException):
            await require_permission("users.manage", ctx, MagicMock(), "org-1")


@pytest.mark.asyncio
async def test_ensure_staff_project_access_for_context_missing_org():
    """Staff project access requires organization context."""
    ctx = UserContext(user_id="u1", email="u@example.com", organization_id=None)
    with pytest.raises(ValidationException):
        await ensure_staff_project_access_for_context(
            user_context=ctx,
            db_connection=MagicMock(),
            project_id="project-1",
            permission_codes=CONTACTS_MANAGEMENT_VIEW,
        )


@pytest.mark.asyncio
async def test_ensure_resident_access_for_unit_missing_org():
    """Unit resident access requires organization in session."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {},
    }
    with pytest.raises(ValidationException):
        await ensure_resident_access_for_unit(
            current_user=current_user,
            db_connection=MagicMock(),
            unit_id="unit-1",
        )


@pytest.mark.asyncio
async def test_ensure_resident_access_for_unit_not_found():
    """Missing units raise NotFoundException."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.db.repositories.contact_units_repository.ContactUnitsRepository"
    ) as mock_units_cls:
        mock_units_cls.return_value.get_unit_project = AsyncMock(return_value=None)
        with pytest.raises(NotFoundException):
            await ensure_resident_access_for_unit(
                current_user=current_user,
                db_connection=MagicMock(),
                unit_id="unit-1",
            )


@pytest.mark.asyncio
async def test_ensure_resident_or_crm_contact_access_project_branch():
    """Project-scoped resident access delegates to staff project access."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    expected = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.ensure_staff_project_access",
        AsyncMock(return_value=expected),
    ) as mock_staff:
        ctx = await ensure_resident_or_crm_contact_access(
            current_user=current_user,
            db_connection=MagicMock(),
            project_id="project-1",
        )
    assert ctx is expected
    mock_staff.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_crm_or_resident_project_access_with_project():
    """Project-scoped CRM access delegates to staff project access."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    expected = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.ensure_staff_project_access",
        AsyncMock(return_value=expected),
    ):
        ctx = await ensure_crm_or_resident_project_access(
            current_user=current_user,
            db_connection=MagicMock(),
            project_id="project-1",
            edit=True,
        )
    assert ctx is expected


@pytest.mark.asyncio
async def test_ensure_staff_project_access_optional_with_project():
    """Optional project access enforces assignment when project_id is set."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    expected = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with patch(
        "apps.user_service.app.utils.common_utils.ensure_staff_project_access",
        AsyncMock(return_value=expected),
    ) as mock_staff:
        ctx = await ensure_staff_project_access_optional(
            current_user=current_user,
            db_connection=MagicMock(),
            project_id="project-1",
            permission_codes=CONTACTS_MANAGEMENT_VIEW,
        )
    assert ctx is expected
    mock_staff.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_project_staff_management_community_admin():
    """Community admins with assigned manage permission may manage project staff."""
    ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with (
        patch(
            "apps.user_service.app.utils.common_utils.ensure_staff_project_access_for_context",
            AsyncMock(return_value=ctx),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            AsyncMock(
                side_effect=lambda **kwargs: kwargs["permission_code"]
                == [PROJECT_MEMBERS_MANAGE_ASSIGNED]
            ),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_projects_cls.return_value.get_active_member_with_role = AsyncMock(
            return_value={"role_slug": COMMUNITY_ADMIN_SLUG, "user_id": "user-1"}
        )
        result = await ensure_project_staff_management_access_for_context(
            user_context=ctx,
            db_connection=MagicMock(),
            project_id="project-1",
        )
    assert result.user_id == "user-1"


@pytest.mark.asyncio
async def test_ensure_security_project_member_access_success():
    """Security project access passes for active security assignments."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    staff_ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    member_lookup = AsyncMock(return_value={"role_slug": ProjectMemberRole.SECURITY.value})
    for_context_mock = AsyncMock(
        side_effect=[
            ForbiddenException(message_key="errors.forbidden"),
            staff_ctx,
        ]
    )
    with (
        patch.object(
            common_utils_module,
            "ensure_staff_project_access_for_context",
            for_context_mock,
        ),
        patch.object(
            common_utils_module,
            "extract_user_context",
            AsyncMock(return_value=staff_ctx),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_projects_cls.return_value.get_active_member_with_role = member_lookup
        ctx = await ensure_security_project_member_access(
            current_user,
            MagicMock(),
            project_id="project-1",
            permission_codes=RESIDENT_MANAGEMENT_VIEW,
        )
    assert ctx.user_id == "user-1"
    assert for_context_mock.await_count == 2
    member_lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_security_project_member_access_daily_help_create_bypass():
    """Staff with daily-help create permission skip the security role assignment check."""
    current_user = {"sub": "user-1", "email": "u@example.com"}
    staff_ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    for_context_mock = AsyncMock(return_value=staff_ctx)
    with (
        patch.object(
            common_utils_module,
            "ensure_staff_project_access_for_context",
            for_context_mock,
        ),
        patch.object(
            common_utils_module,
            "extract_user_context",
            AsyncMock(return_value=staff_ctx),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        ctx = await ensure_security_project_member_access(
            current_user,
            MagicMock(),
            project_id="project-1",
            permission_codes=RESIDENT_MANAGEMENT_VIEW,
        )
    assert ctx.user_id == "user-1"
    for_context_mock.assert_awaited_once()
    assert for_context_mock.await_args.kwargs["permission_codes"] == DAILY_HELP_MANAGEMENT_CREATE
    mock_projects_cls.assert_not_called()


def test_parse_flexible_date_strptime_fallback():
    """Fixed-width slash dates parse via strptime fallbacks."""
    assert parse_flexible_date("01-15-2026") == dt.date(2026, 1, 15)


def test_parse_json_field_list_passthrough():
    """parse_json_field returns list payloads unchanged."""
    assert parse_json_field(["a"]) == ["a"]


@pytest.mark.asyncio
async def test_ensure_staff_project_access_for_context_assigned_member():
    """Assigned project view uses project role permissions when membership matches."""
    db = MagicMock()
    ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    setup_mock = MagicMock()
    setup_mock.ensure_project = AsyncMock(return_value={"id": "project-1"})
    repo_mock = MagicMock()
    repo_mock.get_active_member_with_role = AsyncMock(
        return_value={
            "project_role_id": "role-1",
            "role_slug": "security",
            "user_id": "user-1",
        }
    )
    roles_repo_mock = MagicMock()
    roles_repo_mock.get_permission_codes_for_role = AsyncMock(
        return_value={VISITOR_MANAGEMENT_VIEW, PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    )

    with (
        patch(
            "apps.user_service.app.utils.common_utils.require_any_permission",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.project_setup_service.ProjectSetupService",
            return_value=setup_mock,
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            AsyncMock(
                side_effect=lambda **kwargs: kwargs["permission_code"]
                in ([PROJECTS_MANAGEMENT_VIEW_ASSIGNED], [VISITOR_MANAGEMENT_VIEW])
            ),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository",
            return_value=repo_mock,
        ),
        patch(
            "apps.user_service.app.db.repositories.project_roles_repository.ProjectRolesRepository",
            return_value=roles_repo_mock,
        ),
    ):
        result = await ensure_staff_project_access_for_context(
            user_context=ctx,
            db_connection=db,
            project_id="project-1",
            permission_codes=VISITOR_MANAGEMENT_VIEW,
        )
    assert result.project_member_role == "security"


@pytest.mark.asyncio
async def test_ensure_resident_or_crm_contact_access_org_crm():
    """Org-scoped CRM access uses check_permissions when no unit/project is provided."""
    current_user = {
        "sub": "user-1",
        "email": "u@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        AsyncMock(return_value=True),
    ):
        ctx = await ensure_resident_or_crm_contact_access(
            current_user=current_user,
            db_connection=MagicMock(),
        )
    assert ctx.organization_id == "org-1"


@pytest.mark.asyncio
async def test_extract_notice_viewer_context_no_project_or_notice():
    """Staff without contact, project, or notice id cannot open the feed."""
    current_user = {
        "sub": "staff-1",
        "email": "staff@example.com",
        "_session_context": {"organization_id": "org-1"},
    }
    with patch(
        "apps.user_service.app.db.repositories.contacts_repository.ContactsRepository"
    ) as mock_contacts_cls:
        mock_contacts_cls.return_value.get_active_contact_by_user_id = AsyncMock(return_value=None)
        with pytest.raises(NotFoundException):
            await extract_notice_viewer_context(current_user, MagicMock())


@pytest.mark.asyncio
async def test_ensure_project_staff_management_forbidden_role():
    """Assigned manage permission requires community-admin project role."""
    ctx = UserContext(user_id="user-1", email="u@example.com", organization_id="org-1")
    with (
        patch(
            "apps.user_service.app.utils.common_utils.ensure_staff_project_access_for_context",
            AsyncMock(return_value=ctx),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            AsyncMock(
                side_effect=lambda **kwargs: kwargs["permission_code"]
                == [PROJECT_MEMBERS_MANAGE_ASSIGNED]
            ),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository"
        ) as mock_projects_cls,
    ):
        mock_projects_cls.return_value.get_active_member_with_role = AsyncMock(
            return_value={"role_slug": "security", "user_id": "user-1"}
        )
        with pytest.raises(ForbiddenException):
            await ensure_project_staff_management_access_for_context(
                user_context=ctx,
                db_connection=MagicMock(),
                project_id="project-1",
            )
