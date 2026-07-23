"""High-ROI unit tests to reach 90% combined coverage."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.schemas.users import UpdateUserProfileRequest
from apps.user_service.app.services.user_service import (
    UserService,
    _member_role_change_context_or_raise,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, UnauthorizedException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ADMIN_ID = "660e8400-e29b-41d4-a716-446655440001"
MEMBER_ID = "770e8400-e29b-41d4-a716-446655440002"
ROLE_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ctx(*, org_id: str | None = ORG_ID) -> UserContext:
    return UserContext(user_id=ADMIN_ID, email="admin@example.com", organization_id=org_id)


def _profile(**overrides):
    row = {
        "user_id": MEMBER_ID,
        "email": "member@example.com",
        "first_name": "Member",
        "last_name": "One",
        "organization_id": ORG_ID,
        "role_id": ROLE_ID,
        "role": "member",
        "status": "active",
        "joined_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "last_active_at": None,
        "avatar_url": None,
        "timezone": "UTC",
        "salutation": None,
    }
    row.update(overrides)
    return row


def _service(**kwargs) -> UserService:
    from apps.user_service.tests.unit.test_user_service import (
        _FakeOrgMemberRepo,
        _FakeOrgRepo,
        _FakeRoleRepo,
    )

    svc = UserService(user_context=kwargs.get("ctx", _ctx()), db_connection=MagicMock())
    svc.organization_member_repository = kwargs.get("member_repo", _FakeOrgMemberRepo())
    svc.organization_repository = kwargs.get("org_repo", _FakeOrgRepo())
    svc.role_repository = kwargs.get("role_repo", _FakeRoleRepo())
    return svc


# --- user_service.py ---


@pytest.mark.asyncio
async def test_get_user_profile_by_id_blank_user_id():
    svc = _service()
    assert await svc.get_user_profile_by_id("  ", ORG_ID) is None


@pytest.mark.asyncio
async def test_get_user_profile_by_id_no_organization():
    svc = _service()
    assert await svc.get_user_profile_by_id(MEMBER_ID, None) is None


@pytest.mark.asyncio
async def test_get_user_profile_by_id_enriches_role():
    from apps.user_service.tests.unit.test_user_service import (
        _FakeOrgMemberRepo,
        _FakeRoleRepo,
    )

    member_repo = _FakeOrgMemberRepo(profile=_profile())
    role_repo = _FakeRoleRepo(role={"id": ROLE_ID, "name": "admin", "description": "Admin"})
    svc = _service(member_repo=member_repo, role_repo=role_repo)
    result = await svc.get_user_profile_by_id(MEMBER_ID, ORG_ID)
    assert result["roles"]["name"] == "admin"


@pytest.mark.asyncio
async def test_update_user_email_profile_missing():
    from apps.user_service.tests.unit.test_user_service import _FakeOrgMemberRepo

    svc = _service(member_repo=_FakeOrgMemberRepo(profile=None))
    with pytest.raises(NotFoundException):
        await svc.update_user_email(MEMBER_ID, ORG_ID, "new@example.com")


@pytest.mark.asyncio
async def test_update_user_email_repo_returns_false():
    from apps.user_service.tests.unit.test_user_service import _FakeOrgMemberRepo

    repo = _FakeOrgMemberRepo(profile=_profile())
    repo.update_user_email = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc = _service(member_repo=repo)
    with pytest.raises(NotFoundException):
        await svc.update_user_email(MEMBER_ID, ORG_ID, "new@example.com")


@pytest.mark.asyncio
async def test_get_user_profile_with_metadata_no_org(monkeypatch):
    from apps.user_service.tests.unit.test_user_service import _FakeOrgMemberRepo

    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_user_by_id",
        AsyncMock(return_value={"email": "x@y.com", "user_metadata": {}, "identities": []}),
    )
    svc = _service(member_repo=_FakeOrgMemberRepo(profile=None))
    result = await svc.get_user_profile_with_metadata(MEMBER_ID, organization_id=None)
    assert result["profile_data"]["email"] == "x@y.com"


@pytest.mark.asyncio
async def test_get_user_profile_with_metadata_isometrik_copy(monkeypatch):
    from apps.user_service.app.schemas.auth import IsometrikDetails
    from apps.user_service.tests.unit.test_user_service import _FakeOrgMemberRepo

    iso = IsometrikDetails(user_id="orig", token="t")
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_user_by_id",
        AsyncMock(return_value={"email": "m@example.com", "user_metadata": {}, "identities": []}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_isometrik_details",
        AsyncMock(return_value=iso),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.is_system_super_admin",
        AsyncMock(return_value=True),
    )
    svc = _service(member_repo=_FakeOrgMemberRepo(profile=_profile(isometrik_user_id="iso-1")))
    result = await svc.get_user_profile_with_metadata(
        MEMBER_ID, ORG_ID, current_user={"sub": ADMIN_ID}
    )
    assert result["profile_data"]["is_superadmin"] is True
    assert result["profile_data"]["isometrik_details"]["user_id"] == "iso-1"


def test_extract_auth_user_contact_invalid_payload():
    email, meta, phone, isd = UserService._extract_auth_user_contact(None, "fb@example.com")
    assert email == "fb@example.com"
    assert meta == {}
    assert phone is None


def test_extract_auth_user_contact_prefers_new_email():
    email, _, _, _ = UserService._extract_auth_user_contact(
        {"new_email": "pending@example.com", "email": "old@example.com", "user_metadata": {}},
        "fb@example.com",
    )
    assert email == "pending@example.com"


def test_build_or_update_profile_updates_existing_fields():
    svc = _service()
    base = _profile(email="OLD@example.com", salutation=None)
    updated = svc._build_or_update_profile(
        base_profile=base,
        user_metadata={"salutation": "Mr", "alternate_emails": ["alt@example.com"]},
        user_id=MEMBER_ID,
        current_email="new@example.com",
        phone_number="999",
        phone_isd_code="+1",
    )
    assert updated["email"] == "new@example.com"
    assert updated["phone_number"] == "999"
    assert updated["salutation"] == "Mr"


def test_build_identities_oauth_provider_and_fallback():
    identities = UserService._build_identities(
        {
            "identities": [
                {
                    "provider": "google",
                    "identity_data": {"provider_id": "google-123"},
                    "id": "fallback-id",
                    "created_at": "2026-01-01",
                    "updated_at": "2026-01-02",
                    "email": "oauth@example.com",
                },
                {
                    "provider": "email",
                    "identity_data": {},
                    "id": "identity-id",
                    "email": "id@example.com",
                },
            ]
        }
    )
    assert identities[0]["provider_id"] == "google-123"
    assert identities[0]["email"] == "oauth@example.com"
    assert identities[1]["provider_id"] == "identity-id"


def test_build_role_info_without_role_id():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserService._build_role_info({"role_id": None})


@pytest.mark.asyncio
async def test_transform_users_without_role_id():
    from apps.user_service.tests.unit.test_user_service import _FakeRoleRepo

    svc = _service(role_repo=_FakeRoleRepo())
    users = await svc.transform_users(
        [
            {
                "user_id": MEMBER_ID,
                "email": "m@example.com",
                "role_id": None,
                "role": "member",
                "member_role": "member",
            }
        ],
        ORG_ID,
    )
    assert users[0].role_id == ""
    assert users[0].permissions_count == 0


@pytest.mark.asyncio
async def test_fetch_profile_for_update_supabase_fallback(monkeypatch):
    user_obj = SimpleNamespace(user=SimpleNamespace(user_metadata={"first_name": "Auth"}))
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_user_by_id",
        AsyncMock(return_value=user_obj),
    )
    svc = _service()
    profile = await svc._fetch_profile_for_update(MEMBER_ID, organization_id=None)
    assert profile["first_name"] == "Auth"


def test_build_update_payload_all_optional_fields():
    svc = _service()
    body = UpdateUserProfileRequest(
        first_name="A",
        last_name="B",
        timezone="UTC",
        avatar_url="avatars/x.png",
        salutation="Dr",
        alternate_emails=["a@b.com", "a@b.com"],
    )
    update_data, metadata = svc._build_update_payload(body)
    assert update_data["salutation"] == "Dr"
    assert metadata["alternate_emails"] == ["a@b.com"]


def test_build_verification_metadata_method_without_two_fa():
    from libs.shared_utils.http_exceptions import BadRequestException

    svc = _service()
    body = UpdateUserProfileRequest(verification_method="PHONE")
    with pytest.raises(BadRequestException):
        svc._build_verification_metadata(body)


def test_create_user_profile_data_with_verification_pref():
    svc = _service()
    data = svc._create_user_profile_data(
        user_profile={
            **_profile(),
            "verification_preference": {"enabled": True, "type": "PHONE"},
            "phone_number": "1",
            "phone_isd_code": "+1",
            "has_password": True,
            "joined_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "last_active_at": "2026-01-02T00:00:00Z",
        }
    )
    assert data.verification_preference.verification_method == "PHONE"


@pytest.mark.asyncio
async def test_update_isometrik_user_if_needed_success(monkeypatch):
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.parse_json_field",
        lambda x: {},
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_isometrik_data_from_settings",
        lambda x: {"licenseKey": "k", "appSecret": "s"},
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.login_to_isometrik",
        AsyncMock(return_value={"userToken": "tok"}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.update_isometrik_user",
        AsyncMock(),
    )
    body = UpdateUserProfileRequest(first_name="New", avatar_url="pic.png")
    await svc._update_isometrik_user_if_needed(
        MEMBER_ID,
        ORG_ID,
        body,
        updated_profile={"first_name": "New", "last_name": "Name"},
    )


@pytest.mark.asyncio
async def test_update_isometrik_user_if_needed_swallows_errors(monkeypatch):
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.login_to_isometrik",
        AsyncMock(side_effect=RuntimeError("iso down")),
    )
    body = UpdateUserProfileRequest(first_name="New")
    await svc._update_isometrik_user_if_needed(
        MEMBER_ID, ORG_ID, body, updated_profile={"first_name": "New", "last_name": "X"}
    )


@pytest.mark.asyncio
async def test_update_isometrik_user_if_needed_no_op():
    svc = _service()
    body = UpdateUserProfileRequest(timezone="UTC")
    await svc._update_isometrik_user_if_needed(MEMBER_ID, ORG_ID, body, updated_profile={})


@pytest.mark.asyncio
async def test_update_organization_member_role_update_fails():
    from apps.user_service.tests.unit.test_user_service_member_role import (
        ROLE_NEW,
        _base_ctx_row,
        _FakeOrgMemberRepo,
    )

    svc = _service(ctx=_ctx())
    fake = _FakeOrgMemberRepo()
    fake.fetch_result = _base_ctx_row()
    fake.fetch_result["requester_user_id"] = ADMIN_ID
    fake.update_result = None
    svc.organization_member_repository = fake
    svc.user_context = _ctx()
    with pytest.raises(NotFoundException):
        await svc.update_organization_member_role(MEMBER_ID, ROLE_NEW)


def test_member_role_change_context_missing_requester():
    with pytest.raises(NotFoundException) as exc:
        _member_role_change_context_or_raise(
            {"target_user_id": "t", "new_role_id": "r", "new_role_name": "n"}
        )
    assert exc.value.message_key == "auth.errors.user_not_member_of_organization"


def test_member_role_change_context_missing_target():
    with pytest.raises(NotFoundException) as exc:
        _member_role_change_context_or_raise(
            {"requester_user_id": "r", "new_role_id": "rid", "new_role_name": "n"}
        )
    assert exc.value.message_key == "users.errors.organization_user_not_found"


@pytest.mark.asyncio
async def test_unban_email_failure_still_succeeds(monkeypatch):
    from apps.user_service.tests.unit.test_user_service import _FakeOrgMemberRepo

    repo = _FakeOrgMemberRepo(profile=_profile(), revoke_ok=True)
    svc = _service(member_repo=repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.send_org_member_unbanned_email",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("smtp")),
    )
    result = await svc.unban_user(MEMBER_ID, ORG_ID)
    assert result["audit_data"]["ban_removed"] is True


# --- session_context_cache.py ---


@pytest.mark.asyncio
async def test_resolve_session_context_from_redis_empty_session():
    from libs.shared_utils.session_context_cache import (
        resolve_session_context_from_redis,
    )

    blocked, ctx = await resolve_session_context_from_redis(
        user_id="u1", session_id="  ", redis_client=MagicMock()
    )
    assert blocked is False
    assert ctx is None


@pytest.mark.asyncio
async def test_fetch_redis_session_state_pipeline_error(monkeypatch):
    from libs.shared_utils.session_context_cache import _fetch_redis_session_state

    redis_client = MagicMock()
    redis_client.pipeline.side_effect = RuntimeError("pipe fail")
    blocked, ctx = await _fetch_redis_session_state(redis_client, "u1", "s1")
    assert blocked is False
    assert ctx is None


@pytest.mark.asyncio
async def test_get_redis_session_context_no_client(monkeypatch):
    from libs.shared_utils.session_context_cache import _get_redis_session_context

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=None),
    )
    assert await _get_redis_session_context("s1") is None


@pytest.mark.asyncio
async def test_warm_session_context_cache_redis_error(monkeypatch):
    from libs.shared_utils.session_context_cache import warm_session_context_cache

    redis_client = AsyncMock()
    redis_client.setex = AsyncMock(side_effect=RuntimeError("redis"))
    await warm_session_context_cache("s1", "org-1", redis_client=redis_client)


@pytest.mark.asyncio
async def test_invalidate_session_context_cache_redis_error(monkeypatch):
    from libs.shared_utils.session_context_cache import invalidate_session_context_cache

    redis_client = AsyncMock()
    redis_client.delete = AsyncMock(side_effect=RuntimeError("redis"))
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=redis_client),
    )
    await invalidate_session_context_cache("s1")


@pytest.mark.asyncio
async def test_coalesced_resolve_session_context_from_db(monkeypatch):
    from libs.shared_utils.session_context_cache import (
        coalesced_resolve_session_context_from_db,
    )

    conn = MagicMock()
    pool = MagicMock()

    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_pool",
        AsyncMock(return_value=pool),
    )
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.AcquireConnection",
        lambda _pool: _Acquire(),
    )
    with patch(
        "libs.shared_utils.session_context_cache._resolve_session_context_from_db_impl",
        AsyncMock(return_value={"organization_id": "org-1"}),
    ):
        ctx = await coalesced_resolve_session_context_from_db("session-1")
    assert ctx == {"organization_id": "org-1"}


@pytest.mark.asyncio
async def test_resolve_session_context_db_fallback(monkeypatch):
    from libs.shared_utils.session_context_cache import resolve_session_context

    with (
        patch(
            "libs.shared_utils.session_context_cache.resolve_session_context_from_redis",
            AsyncMock(return_value=(False, None)),
        ),
        patch(
            "libs.shared_utils.session_context_cache.coalesced_resolve_session_context_from_db",
            AsyncMock(return_value={"organization_id": "org-db"}),
        ),
    ):
        ctx = await resolve_session_context(user_id="u1", session_id="s1")
    assert ctx == {"organization_id": "org-db"}


@pytest.mark.asyncio
async def test_is_session_revoked_no_redis(monkeypatch):
    from libs.shared_utils.session_context_cache import _is_session_revoked

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=None),
    )
    assert await _is_session_revoked("s1") is False


@pytest.mark.asyncio
async def test_invalidate_user_sessions_cache_redis_mark_failure(monkeypatch):
    from libs.shared_utils.session_context_cache import invalidate_user_sessions_cache

    redis_client = AsyncMock()
    redis_client.setex = AsyncMock(side_effect=RuntimeError("redis"))
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=redis_client),
    )
    with patch(
        "libs.shared_utils.session_context_cache.invalidate_session_context_cache",
        AsyncMock(),
    ):
        await invalidate_user_sessions_cache("u1", ["s1"])


# --- jwt_auth.py ---


@pytest.mark.asyncio
async def test_require_request_user_raises():
    from libs.shared_middleware.jwt_auth import get_user_from_auth_db

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(UnauthorizedException):
        await get_user_from_auth_db(request)


@pytest.mark.asyncio
async def test_get_user_from_auth_db_success():
    from libs.shared_middleware.jwt_auth import get_user_from_auth_db

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "email": "u@example.com", "session_id": "s1"}
    session_ctx = {"organization_id": "org-1"}
    with patch(
        "libs.shared_middleware.jwt_auth.coalesced_resolve_session_context_from_db",
        AsyncMock(return_value=session_ctx),
    ):
        user = await get_user_from_auth_db(request)
    assert user["_session_context"] == session_ctx


# --- translations.py ---


def test_translator_load_path_file_not_found(tmp_path):
    from pathlib import Path

    from libs.shared_utils.translations import Translator

    path = tmp_path / "locales"
    path.mkdir()
    custom = Translator(default_language="en", extra_paths=[])
    with patch.object(Path, "glob", side_effect=FileNotFoundError("missing locale dir")):
        custom._load_from_path(path)
    assert custom.get("any.key") == "any.key"


@pytest.mark.asyncio
async def test_get_user_profile_by_id_missing_role_record():
    from apps.user_service.tests.unit.test_user_service import (
        _FakeOrgMemberRepo,
        _FakeRoleRepo,
    )

    member_repo = _FakeOrgMemberRepo(profile=_profile())
    role_repo = _FakeRoleRepo()
    role_repo.get_role_by_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc = _service(member_repo=member_repo, role_repo=role_repo)
    result = await svc.get_user_profile_by_id(MEMBER_ID, ORG_ID)
    assert "roles" not in result


@pytest.mark.asyncio
async def test_ban_user_default_org_name(monkeypatch):
    from apps.user_service.tests.unit.test_user_service import (
        _FakeOrgMemberRepo,
        _FakeOrgRepo,
    )

    repo = _FakeOrgMemberRepo(profile=_profile(), suspend_ok=True)
    org_repo = _FakeOrgRepo(org=None)
    org_repo.get_organization_by_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc = _service(member_repo=repo, org_repo=org_repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.revoke_org_member_sessions_everywhere",
        AsyncMock(),
    )
    sent = {}

    def _capture_email(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.send_org_member_banned_email",
        _capture_email,
    )
    await svc.ban_user(MEMBER_ID, ORG_ID)
    assert sent["organization_name"] == "your organization"


@pytest.mark.asyncio
async def test_warm_session_context_after_auth_no_session():
    from libs.shared_utils.session_context_cache import warm_session_context_after_auth

    with patch(
        "libs.shared_utils.session_context_cache.warm_session_context_cache",
        AsyncMock(),
    ) as warm_cache:
        await warm_session_context_after_auth(session_id=None, organization_id=ORG_ID)
    warm_cache.assert_not_called()


@pytest.mark.asyncio
async def test_is_user_deleted_cache_disabled(monkeypatch):
    from libs.shared_utils.session_context_cache import _is_user_deleted

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache._settings",
        lambda: SimpleNamespace(session_ctx_cache_enabled=False),
    )
    assert await _is_user_deleted("u1") is False


@pytest.mark.asyncio
async def test_is_user_deleted_no_user_id():
    from libs.shared_utils.session_context_cache import _is_user_deleted

    assert await _is_user_deleted(None) is False


@pytest.mark.asyncio
async def test_is_session_revoked_no_session_id():
    from libs.shared_utils.session_context_cache import _is_session_revoked

    assert await _is_session_revoked(None) is False


@pytest.mark.asyncio
async def test_is_session_revoked_redis_error(monkeypatch):
    from libs.shared_utils.session_context_cache import _is_session_revoked

    redis_client = AsyncMock()
    redis_client.exists = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=redis_client),
    )
    assert await _is_session_revoked("s1") is False


@pytest.mark.asyncio
async def test_fetch_redis_session_state_cache_disabled(monkeypatch):
    from libs.shared_utils.session_context_cache import _fetch_redis_session_state

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache._settings",
        lambda: SimpleNamespace(session_ctx_cache_enabled=False),
    )
    blocked, ctx = await _fetch_redis_session_state(MagicMock(), "u1", "s1")
    assert blocked is False
    assert ctx is None


@pytest.mark.asyncio
async def test_fetch_redis_session_state_cache_hit_without_user_id():
    from libs.shared_utils.session_context_cache import _fetch_redis_session_state

    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[0, '{"organization_id": "org-1"}'])
    redis_client = MagicMock()
    redis_client.pipeline.return_value = pipeline
    blocked, ctx = await _fetch_redis_session_state(redis_client, None, "s1")
    assert blocked is False
    assert ctx == {"organization_id": "org-1"}


@pytest.mark.asyncio
async def test_get_redis_session_context_read_error(monkeypatch):
    from libs.shared_utils.session_context_cache import _get_redis_session_context

    redis_client = AsyncMock()
    redis_client.get = AsyncMock(side_effect=RuntimeError("redis"))
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=redis_client),
    )
    assert await _get_redis_session_context("s1") is None


@pytest.mark.asyncio
async def test_resolve_session_context_from_db_empty_session():
    from libs.shared_utils.session_context_cache import resolve_session_context_from_db

    assert await resolve_session_context_from_db(session_id="  ", db_connection=MagicMock()) is None


@pytest.mark.asyncio
async def test_coalesced_resolve_empty_session():
    from libs.shared_utils.session_context_cache import (
        coalesced_resolve_session_context_from_db,
    )

    assert await coalesced_resolve_session_context_from_db("  ") is None


@pytest.mark.asyncio
async def test_warm_session_context_cache_disabled(monkeypatch):
    from libs.shared_utils.session_context_cache import warm_session_context_cache

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache._settings",
        lambda: SimpleNamespace(session_ctx_cache_enabled=False),
    )
    await warm_session_context_cache("s1", "org-1")


@pytest.mark.asyncio
async def test_warm_session_context_cache_no_redis_client(monkeypatch):
    from libs.shared_utils.session_context_cache import warm_session_context_cache

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=None),
    )
    await warm_session_context_cache("s1", "org-1")


@pytest.mark.asyncio
async def test_invalidate_session_context_cache_no_redis(monkeypatch):
    from libs.shared_utils.session_context_cache import invalidate_session_context_cache

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=None),
    )
    await invalidate_session_context_cache("s1")


@pytest.mark.asyncio
async def test_revoke_org_member_sessions_empty_list():
    from libs.shared_utils.session_context_cache import (
        revoke_org_member_sessions_everywhere,
    )

    class FakeRepo:
        def __init__(self, db_connection):
            self.db_connection = db_connection

        async def revoke_org_sessions_for_user(self, user_id, organization_id):
            del user_id, organization_id
            return []

    with patch("apps.user_service.app.db.repositories.SessionRepository", FakeRepo):
        await revoke_org_member_sessions_everywhere(
            db_connection=MagicMock(),
            user_id="u1",
            organization_id="org-1",
        )


@pytest.mark.asyncio
async def test_revoke_organization_sessions_empty_list():
    from libs.shared_utils.session_context_cache import (
        revoke_organization_sessions_everywhere,
    )

    class FakeRepo:
        def __init__(self, db_connection):
            self.db_connection = db_connection

        async def revoke_all_sessions_for_organization(self, organization_id):
            del organization_id
            return []

    with patch("apps.user_service.app.db.repositories.SessionRepository", FakeRepo):
        await revoke_organization_sessions_everywhere(
            db_connection=MagicMock(),
            organization_id="org-1",
        )


@pytest.mark.asyncio
async def test_invalidate_user_sessions_cache_no_user_id(monkeypatch):
    from libs.shared_utils.session_context_cache import invalidate_user_sessions_cache

    await invalidate_user_sessions_cache("", ["s1"])


@pytest.mark.asyncio
async def test_is_user_deleted_no_redis_client(monkeypatch):
    from libs.shared_utils.session_context_cache import _is_user_deleted

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=None),
    )
    assert await _is_user_deleted("u1") is False


def test_extract_session_id_from_jti_claim():
    import jwt

    from libs.shared_utils.session_context_cache import (
        extract_session_id_from_access_token,
    )

    token = jwt.encode({"jti": "sess-jti"}, "secret", algorithm="HS256")
    assert extract_session_id_from_access_token(token) == "sess-jti"


@pytest.mark.asyncio
async def test_invalidate_user_sessions_cache_no_redis_revokes_sessions(monkeypatch):
    from libs.shared_utils.session_context_cache import invalidate_user_sessions_cache

    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=None),
    )
    with patch(
        "libs.shared_utils.session_context_cache.invalidate_session_context_cache",
        AsyncMock(),
    ) as invalidate_session:
        await invalidate_user_sessions_cache("u1", ["s1", "s2"])
    assert invalidate_session.await_count == 2


@pytest.mark.asyncio
async def test_get_user_profile_by_id_not_found():
    from apps.user_service.tests.unit.test_user_service import _FakeOrgMemberRepo

    svc = _service(member_repo=_FakeOrgMemberRepo(profile=None))
    assert await svc.get_user_profile_by_id(MEMBER_ID, ORG_ID) is None


@pytest.mark.asyncio
async def test_build_organization_details_missing_org():
    from apps.user_service.tests.unit.test_user_service import _FakeOrgRepo

    org_repo = _FakeOrgRepo()
    org_repo.get_organization_details = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc = _service(org_repo=org_repo)
    assert await svc._build_organization_details(ORG_ID) is None
