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

from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.app.utils.common_utils import (
    PerformanceTimer,
    UserContext,
    check_permissions,
    coerce_json_list,
    enum_member_title_label,
    extract_audit_data_value,
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
    require_organization_creator,
    require_permission,
    require_super_admin,
    safe_json_loads,
    safe_str,
    serialize_jsonb_param,
    serialize_pydantic_models,
    set_audit_old_data_from_user,
    title_case_field,
    validate_uuid_format,
)
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
    ValidationException,
)


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
