"""Unit tests for AuthService key flows (mocked Supabase)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from supabase import AuthApiError

from apps.user_service.app.schemas.auth import AuthLogin
from apps.user_service.app.services.auth_service import AuthService
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ServiceUnavailableException,
    TooManyRequestsException,
    UnauthorizedException,
    ValidationException,
)


class _FakeUserRepo:
    """Fake UserRepository."""

    def __init__(self, *, user: dict[str, Any] | None = None) -> None:
        self.user = user

    async def get_auth_user_by_email(self, email: str):
        """Return configured auth user row."""
        del email
        return self.user


class _FakeOrgRepo:
    """Fake OrganizationRepository."""

    def __init__(self, *, organizations: list[dict[str, Any]] | None = None) -> None:
        self.organizations = organizations or []

    async def get_user_active_organizations(self, user_id: str):
        """Return active organizations for user."""
        del user_id
        return self.organizations


def _service(
    *,
    user_repo: _FakeUserRepo | None = None,
    org_repo: _FakeOrgRepo | None = None,
) -> AuthService:
    """Build AuthService with fake repos."""
    svc = AuthService.__new__(AuthService)
    svc.db_connection = MagicMock()
    svc.user_repository = user_repo or _FakeUserRepo()
    svc.organization_repository = org_repo or _FakeOrgRepo()
    svc.supabase_client = MagicMock()
    return svc


def _login_result(*, with_token: bool = True):
    """Build a minimal Supabase login result."""
    session = SimpleNamespace(
        access_token="access-token" if with_token else None,
        refresh_token="refresh-token",
        expires_in=3600,
        expires_at=0,
    )
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        user_metadata={"first_name": "Test", "last_name": "User"},
    )
    return SimpleNamespace(session=session, user=user)


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("Strong1!", True),
        ("weak", False),
        ("NoDigit!", False),
        ("abc12", False),
    ],
)
def test_is_password_strong(password: str, expected: bool):
    """Password strength helper enforces complexity rules."""
    assert AuthService._is_password_strong(password) is expected


def test_parse_user_metadata_dict():
    """Metadata parser returns dict input unchanged."""
    payload = {"first_name": "Jane"}
    assert AuthService._parse_user_metadata(payload) == payload


def test_parse_user_metadata_json_string():
    """Metadata parser decodes JSON strings."""
    payload = {"timezone": "UTC"}
    assert AuthService._parse_user_metadata(json.dumps(payload)) == payload


def test_parse_user_metadata_invalid_json():
    """Invalid JSON metadata returns empty dict."""
    assert AuthService._parse_user_metadata("{bad") == {}


def test_validate_password_strength_weak():
    """Weak password raises ValidationException."""
    svc = _service()
    with pytest.raises(ValidationException):
        svc._validate_password_strength("weak")


@pytest.mark.asyncio
async def test_forgot_password_user_found(monkeypatch):
    """Forgot password sends reset email when user exists."""
    svc = _service(user_repo=_FakeUserRepo(user={"id": "user-1"}))
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.send_password_reset_email",
        AsyncMock(),
    )

    result = await svc.forgot_password("user@example.com")

    assert "Password reset email sent" in result.message


@pytest.mark.asyncio
async def test_forgot_password_user_missing():
    """Forgot password raises when email is unknown."""
    svc = _service(user_repo=_FakeUserRepo(user=None))
    with pytest.raises(NotFoundException):
        await svc.forgot_password("missing@example.com")


@pytest.mark.asyncio
async def test_refresh_session_missing_token():
    """Refresh session rejects empty refresh token."""
    svc = _service()
    with pytest.raises(BadRequestException):
        await svc.refresh_session(None)


@pytest.mark.asyncio
async def test_refresh_session_success(monkeypatch):
    """Refresh session returns rotated tokens."""
    svc = _service()
    refreshed = _login_result()
    monkeypatch.setattr(
        AuthService,
        "_refresh_user_session_with_error_handling",
        AsyncMock(return_value=refreshed),
    )

    result = await svc.refresh_session("refresh-token")

    assert result.access_token == "access-token"
    assert result.token_refreshed is True


@pytest.mark.asyncio
async def test_login_success(monkeypatch):
    """Login returns tokens and active organizations."""
    svc = _service(
        org_repo=_FakeOrgRepo(
            organizations=[
                {
                    "id": "org-1",
                    "name": "Acme",
                    "domain": "acme.example.com",
                    "logo_url": None,
                    "description": None,
                }
            ]
        )
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.login_user",
        AsyncMock(return_value=_login_result()),
    )
    monkeypatch.setattr(
        AuthService,
        "_check_and_verify_2fa",
        AsyncMock(),
    )
    monkeypatch.setattr(
        AuthService,
        "_warm_session_context_from_session",
        AsyncMock(),
    )

    result = await svc.login(AuthLogin(email="user@example.com", password="Strong1!"))

    assert result.access_token == "access-token"
    assert result.user.email == "user@example.com"
    assert len(result.organizations) == 1


@pytest.mark.asyncio
async def test_login_invalid_credentials(monkeypatch):
    """Login maps Supabase 400 auth errors to BadRequest."""
    svc = _service()
    auth_error = AuthApiError("invalid", 400, None)

    async def _raise(*args, **kwargs):
        del args, kwargs
        raise auth_error

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.login_user",
        _raise,
    )

    with pytest.raises(BadRequestException):
        await svc.login(AuthLogin(email="user@example.com", password="wrong"))


@pytest.mark.asyncio
async def test_login_missing_access_token(monkeypatch):
    """Login rejects sessions without access_token."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.login_user",
        AsyncMock(return_value=_login_result(with_token=False)),
    )

    with pytest.raises(InternalServerErrorException):
        await svc.login(AuthLogin(email="user@example.com", password="Strong1!"))


@pytest.mark.asyncio
async def test_reset_password_weak(monkeypatch):
    """Reset password validates strength before Supabase call."""
    svc = _service()
    update_mock = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_by_user_id",
        update_mock,
    )

    with pytest.raises(ValidationException):
        await svc.reset_password(user_id="user-1", new_password="weak")

    update_mock.assert_not_awaited()


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"verification_preference": {"enabled": True, "type": "email"}}, True),
        ({}, False),
        ('{"verification_preference": {"enabled": false}}', False),
    ],
)
def test_is_2fa_enabled(metadata, expected: bool):
    """_is_2fa_enabled reads verification_preference from metadata."""
    enabled, _ = AuthService._is_2fa_enabled(metadata)
    assert enabled is expected


def test_validate_2fa_credentials_required_missing():
    """_validate_2fa_credentials_required rejects missing ids."""
    with pytest.raises(BadRequestException):
        AuthService._validate_2fa_credentials_required(None, "123456")


@pytest.mark.asyncio
async def test_reset_password_success(monkeypatch):
    """reset_password updates password and returns success message."""
    svc = _service()
    user = SimpleNamespace(
        email="user@example.com",
        user_metadata={"first_name": "Test", "last_name": "User"},
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_by_user_id",
        AsyncMock(return_value=SimpleNamespace(user=user)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.send_password_reset_success_email",
        lambda **kwargs: True,
    )

    result = await svc.reset_password(user_id="user-1", new_password="Strong1!")

    assert "Password reset successfully" in result.message


@pytest.mark.asyncio
async def test_reset_password_bad_token(monkeypatch):
    """reset_password maps AuthApiError to BadRequest."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_by_user_id",
        AsyncMock(side_effect=AuthApiError("expired", 400, None)),
    )
    with pytest.raises(BadRequestException):
        await svc.reset_password(user_id="user-1", new_password="Strong1!")


@pytest.mark.asyncio
async def test_change_password_success(monkeypatch):
    """change_password verifies current password and updates."""
    svc = _service(user_repo=_FakeUserRepo())
    svc.user_repository.verify_current_password = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_with_link_identity",
        AsyncMock(return_value=SimpleNamespace(email="user@example.com")),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.send_password_change_success_email",
        lambda **kwargs: True,
    )

    result = await svc.change_password(
        user_id="user-1",
        user_email="user@example.com",
        current_password="OldPass1!",
        new_password="NewPass1!",
        user_metadata={"first_name": "Test"},
    )

    assert result.message == "Password changed successfully"


@pytest.mark.asyncio
async def test_change_password_wrong_current():
    """change_password rejects incorrect current password."""
    svc = _service(user_repo=_FakeUserRepo())
    svc.user_repository.verify_current_password = AsyncMock(return_value=False)
    with pytest.raises(BadRequestException):
        await svc.change_password(
            user_id="user-1",
            user_email="user@example.com",
            current_password="Wrong1!",
            new_password="NewPass1!",
            user_metadata={},
        )


@pytest.mark.asyncio
async def test_change_password_same_as_current():
    """change_password rejects new password equal to current."""
    svc = _service(user_repo=_FakeUserRepo())
    svc.user_repository.verify_current_password = AsyncMock(return_value=True)
    with pytest.raises(BadRequestException):
        await svc.change_password(
            user_id="user-1",
            user_email="user@example.com",
            current_password="SamePass1!",
            new_password="SamePass1!",
            user_metadata={},
        )


@pytest.mark.asyncio
async def test_validate_account_login_not_found():
    """validate_account LOGIN raises when email unknown."""
    svc = _service(user_repo=_FakeUserRepo(user=None))
    from apps.user_service.app.schemas.auth import ValidateAccountTrigger

    with pytest.raises(NotFoundException):
        await svc.validate_account(ValidateAccountTrigger.LOGIN, "missing@example.com", "Pass1!")


@pytest.mark.asyncio
async def test_validate_account_signup_conflict():
    """validate_account SIGNUP raises when email already registered."""
    svc = _service(user_repo=_FakeUserRepo(user={"id": "user-1"}))
    from apps.user_service.app.schemas.auth import ValidateAccountTrigger

    with pytest.raises(ConflictException):
        await svc.validate_account(ValidateAccountTrigger.SIGNUP, "user@example.com")


@pytest.mark.asyncio
async def test_validate_account_login_2fa_flag():
    """validate_account LOGIN returns two_fa_enabled flag."""
    svc = _service(
        user_repo=_FakeUserRepo(
            user={
                "id": "user-1",
                "raw_user_meta_data": {
                    "verification_preference": {"enabled": True, "type": "email"}
                },
            }
        )
    )
    svc.user_repository._verify_credentials_by_email = AsyncMock(return_value=True)
    from apps.user_service.app.schemas.auth import ValidateAccountTrigger

    result = await svc.validate_account(
        ValidateAccountTrigger.LOGIN,
        "user@example.com",
        "Strong1!",
    )

    assert result is not None
    assert result.two_fa_enabled is True


@pytest.mark.asyncio
async def test_signup_success(monkeypatch):
    """signup validates verification, creates user, and returns tokens."""
    svc = _service()
    svc.supabase_client = MagicMock()
    svc.supabase_admin_client = None
    session = SimpleNamespace(
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
        expires_at=0,
    )
    signup_user = SimpleNamespace(
        id="user-1",
        email="new@example.com",
        user_metadata={"first_name": "New", "last_name": "User"},
    )
    signup_result = SimpleNamespace(user=signup_user, session=session)

    monkeypatch.setattr(
        AuthService,
        "_validate_verification_code_for_signup",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.sign_up_supabase_user",
        AsyncMock(return_value=signup_result),
    )

    def _fake_session_after_signup(self, signup_result=None):
        del self, signup_result
        return session

    monkeypatch.setattr(AuthService, "_get_session_after_signup", _fake_session_after_signup)
    monkeypatch.setattr(
        AuthService,
        "_warm_session_context_from_session",
        AsyncMock(),
    )

    def _noop_welcome(_self, email: str, first_name: str) -> None:
        del _self, email, first_name

    monkeypatch.setattr(
        AuthService,
        "_send_welcome_email_safely",
        _noop_welcome,
    )

    from apps.user_service.app.schemas.auth import SignupRequest

    result = await svc.signup(
        SignupRequest(
            email="new@example.com",
            password="Strong1!",
            first_name="New",
            verification_id="ver-1",
            verification_code="123456",
        )
    )

    assert result.access_token == "access"
    assert result.user.email == "new@example.com"


@pytest.mark.asyncio
async def test_signup_email_exists(monkeypatch):
    """signup maps Supabase email_exists to ConflictException."""
    svc = _service()
    svc.supabase_client = MagicMock()
    svc.supabase_admin_client = None
    monkeypatch.setattr(
        AuthService,
        "_validate_verification_code_for_signup",
        AsyncMock(),
    )

    async def _raise(*args, **kwargs):
        del args, kwargs
        raise AuthApiError("exists", 400, None)

    err = AuthApiError("exists", 400, None)
    err.code = "email_exists"
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.sign_up_supabase_user",
        AsyncMock(side_effect=err),
    )

    from apps.user_service.app.schemas.auth import SignupRequest

    with pytest.raises(ConflictException):
        await svc.signup(
            SignupRequest(
                email="new@example.com",
                password="Strong1!",
                first_name="New",
                verification_id="ver-1",
                verification_code="123456",
            )
        )


@pytest.mark.asyncio
async def test_signup_weak_password_supabase(monkeypatch):
    """signup maps Supabase weak_password to ValidationException."""
    svc = _service()
    svc.supabase_client = MagicMock()
    svc.supabase_admin_client = None
    monkeypatch.setattr(
        AuthService,
        "_validate_verification_code_for_signup",
        AsyncMock(),
    )
    err = AuthApiError("weak", 400, None)
    err.code = "weak_password"
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.sign_up_supabase_user",
        AsyncMock(side_effect=err),
    )

    from apps.user_service.app.schemas.auth import SignupRequest

    with pytest.raises(ValidationException):
        await svc.signup(
            SignupRequest(
                email="new@example.com",
                password="Strong1!",
                first_name="New",
                verification_id="ver-1",
                verification_code="123456",
            )
        )


# ---------------------------------------------------------------------------
# Extended coverage: refresh errors, delete_user, org selection, 2FA, signup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_user_session_invalid_token(monkeypatch):
    """Refresh maps Supabase 400 to BadRequestException."""
    svc = _service()
    err = AuthApiError("invalid", 400, None)
    err.code = "invalid_refresh_token"
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.refresh_session",
        AsyncMock(side_effect=err),
    )
    with pytest.raises(BadRequestException):
        await svc._refresh_user_session_with_error_handling("bad-token")


@pytest.mark.asyncio
async def test_refresh_user_session_already_used(monkeypatch):
    """Refresh maps refresh_token_already_used code."""
    svc = _service()
    err = AuthApiError("used", 400, None)
    err.code = "refresh_token_already_used"
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.refresh_session",
        AsyncMock(side_effect=err),
    )
    with pytest.raises(BadRequestException):
        await svc._refresh_user_session_with_error_handling("used-token")


@pytest.mark.asyncio
async def test_refresh_user_session_rate_limit(monkeypatch):
    """Refresh maps 429 to TooManyRequestsException."""
    svc = _service()
    err = AuthApiError("rate", 429, None)
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.refresh_session",
        AsyncMock(side_effect=err),
    )
    with pytest.raises(TooManyRequestsException):
        await svc._refresh_user_session_with_error_handling("token")


@pytest.mark.asyncio
async def test_refresh_user_session_unauthorized(monkeypatch):
    """Other 4xx auth errors map to UnauthorizedException."""
    svc = _service()
    err = AuthApiError("forbidden", 403, None)
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.refresh_session",
        AsyncMock(side_effect=err),
    )
    with pytest.raises(UnauthorizedException):
        await svc._refresh_user_session_with_error_handling("token")


@pytest.mark.asyncio
async def test_refresh_user_session_service_unavailable(monkeypatch):
    """5xx and network errors map to ServiceUnavailableException."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.refresh_session",
        AsyncMock(side_effect=RuntimeError("network")),
    )
    with pytest.raises(ServiceUnavailableException):
        await svc._refresh_user_session_with_error_handling("token")


@pytest.mark.asyncio
async def test_delete_user_success(monkeypatch):
    """delete_user invalidates session cache after Supabase delete."""
    svc = _service(user_repo=_FakeUserRepo())
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: MagicMock(
            get_active_session_ids_for_user=AsyncMock(return_value=["s1"]),
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.delete_user",
        AsyncMock(return_value={"id": "user-1"}),
    )
    invalidate = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.invalidate_user_sessions_cache",
        invalidate,
    )

    await svc.delete_user("user-1")
    invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_user_not_found(monkeypatch):
    """delete_user raises NotFound when Supabase returns None."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: MagicMock(
            get_active_session_ids_for_user=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.delete_user",
        AsyncMock(return_value=None),
    )
    with pytest.raises(NotFoundException):
        await svc.delete_user("missing")


@pytest.mark.asyncio
async def test_build_auth_response(monkeypatch):
    """_build_auth_response shapes login-compatible payload."""
    svc = _service(org_repo=_FakeOrgRepo(organizations=[{"id": "org-1", "name": "Acme"}]))
    monkeypatch.setattr(AuthService, "_warm_session_context_from_session", AsyncMock())
    session = _login_result().session
    user = _login_result().user

    result = await svc._build_auth_response(session=session, user=user)

    assert result.access_token == "access-token"
    assert result.user.org_setup_status_completed is True


@pytest.mark.asyncio
async def test_validate_verification_code_for_signup(monkeypatch):
    """Signup verification cross-checks email and code."""
    svc = _service()

    class _FakeVerificationRepo:
        async def get_verification_code_by_id(self, _vid):
            return {
                "verified": True,
                "given_input": "user@example.com",
                "verification_code": "123456",
            }

    class _FakeVerificationService:
        verification_code_repository = _FakeVerificationRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.VerificationCodeService",
        lambda db_connection: _FakeVerificationService(),
    )

    await svc._validate_verification_code_for_signup("ver-1", "user@example.com", "123456")


@pytest.mark.asyncio
async def test_validate_verification_code_for_signup_not_verified(monkeypatch):
    """Unverified signup code raises BadRequestException."""
    svc = _service()

    class _FakeVerificationRepo:
        async def get_verification_code_by_id(self, _vid):
            return {"verified": False, "given_input": "user@example.com"}

    class _FakeVerificationService:
        verification_code_repository = _FakeVerificationRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.VerificationCodeService",
        lambda db_connection: _FakeVerificationService(),
    )

    with pytest.raises(BadRequestException):
        await svc._validate_verification_code_for_signup("ver-1", "user@example.com", "123456")


def test_validate_phone_match_mismatch():
    """Phone 2FA rejects mismatched stored phone."""
    with pytest.raises(BadRequestException):
        AuthService._validate_phone_match("+15551234567", "+19998887777")


def test_create_verification_request_email_mismatch():
    """Email verification request rejects mismatched stored email."""
    with pytest.raises(BadRequestException):
        AuthService._create_verification_request(
            {"type": "EMAIL"},
            "ver-1",
            "123456",
            "other@example.com",
            "user@example.com",
        )


def test_create_verification_request_phone_missing_metadata():
    """Phone verification requires phone metadata on user."""
    with pytest.raises(BadRequestException):
        AuthService._create_verification_request(
            {"type": "PHONE"},
            "ver-1",
            "123456",
            "+15551234567",
            "user@example.com",
            phone_number=None,
            phone_isd_code=None,
        )


@pytest.mark.asyncio
async def test_check_and_verify_2fa_skips_when_disabled():
    """2FA check is skipped when not enabled in metadata."""
    svc = _service()
    await svc._check_and_verify_2fa({}, None, None, "user@example.com")


@pytest.mark.asyncio
async def test_check_and_verify_2fa_requires_credentials():
    """2FA enabled accounts require verification id/code."""
    svc = _service()
    metadata = {"verification_preference": {"enabled": True, "type": "EMAIL"}}
    with pytest.raises(BadRequestException):
        await svc._check_and_verify_2fa(metadata, None, None, "user@example.com")


@pytest.mark.asyncio
async def test_select_organization_member_success(monkeypatch):
    """select_organization links session to org and returns isometrik details."""
    svc = _service(org_repo=_FakeOrgRepo())

    class _FakeSessionRepo:
        async def check_session_has_organization(self, session_id):
            del session_id
            return {"organization_id": None}

        async def update_session_organization_context(self, **kwargs):
            del kwargs

    class _FakeOrgMemberRepo:
        async def get_active_membership_isometrik_user_id(self, **kwargs):
            del kwargs
            return True, "iso-member-1"

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: _FakeSessionRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.OrganizationMemberRepository",
        lambda db_connection: _FakeOrgMemberRepo(),
    )
    from apps.user_service.app.schemas.auth import IsometrikDetails

    iso_details = IsometrikDetails(user_id="iso-member-1", token="tok")
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.get_isometrik_details",
        AsyncMock(return_value=iso_details),
    )

    from apps.user_service.app.schemas.enums import SelectOrganizationType

    result = await svc.select_organization(
        user_id="user-1",
        session_id="session-1",
        organization_id="org-1",
        user_type=SelectOrganizationType.ORGANIZATION_MEMBER,
    )

    assert result.isometrik_details is not None


@pytest.mark.asyncio
async def test_select_organization_not_member(monkeypatch):
    """select_organization rejects non-members."""
    svc = _service()

    class _FakeSessionRepo:
        async def check_session_has_organization(self, session_id):
            del session_id
            return {"organization_id": None}

    class _FakeOrgMemberRepo:
        async def get_active_membership_isometrik_user_id(self, **kwargs):
            del kwargs
            return False, None

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: _FakeSessionRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.OrganizationMemberRepository",
        lambda db_connection: _FakeOrgMemberRepo(),
    )

    with pytest.raises(NotFoundException):
        await svc.select_organization("user-1", "session-1", "org-1")


@pytest.mark.asyncio
async def test_select_organization_session_conflict(monkeypatch):
    """select_organization rejects when session already linked to another org."""
    svc = _service()

    class _FakeSessionRepo:
        async def check_session_has_organization(self, session_id):
            del session_id
            return {"organization_id": "other-org"}

    class _FakeOrgMemberRepo:
        async def get_active_membership_isometrik_user_id(self, **kwargs):
            del kwargs
            return True, "iso-1"

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: _FakeSessionRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.OrganizationMemberRepository",
        lambda db_connection: _FakeOrgMemberRepo(),
    )

    with pytest.raises(ConflictException):
        await svc.select_organization("user-1", "session-1", "org-1")


@pytest.mark.asyncio
async def test_switch_organization_updates_context(monkeypatch):
    """switch_organization updates session when org changes."""
    svc = _service(org_repo=_FakeOrgRepo())
    updates: list[dict[str, str]] = []

    class _FakeSessionRepo:
        async def check_session_has_organization(self, session_id):
            del session_id
            return {"organization_id": "old-org"}

        async def update_session_organization_context(self, **kwargs):
            updates.append(kwargs)

    class _FakeOrgMemberRepo:
        async def get_active_membership_isometrik_user_id(self, **kwargs):
            del kwargs
            return True, "iso-1"

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: _FakeSessionRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.OrganizationMemberRepository",
        lambda db_connection: _FakeOrgMemberRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.get_isometrik_details",
        AsyncMock(return_value=None),
    )

    await svc.switch_organization("user-1", "session-1", "new-org")
    assert updates and updates[0]["organization_id"] == "new-org"


@pytest.mark.asyncio
async def test_select_organization_client_user(monkeypatch):
    """Client user type validates via contacts repository."""
    svc = _service()

    class _FakeSessionRepo:
        async def check_session_has_organization(self, session_id):
            del session_id
            return {"organization_id": None}

        async def update_session_organization_context(self, **kwargs):
            del kwargs

    class _FakeContactsRepo:
        async def is_active_contact_user_for_organization(self, **kwargs):
            del kwargs
            return True

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: _FakeSessionRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.ContactsRepository",
        lambda db_connection: _FakeContactsRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.get_isometrik_details",
        AsyncMock(return_value=None),
    )

    from apps.user_service.app.schemas.enums import SelectOrganizationType

    result = await svc.select_organization(
        "user-1",
        "session-1",
        "org-1",
        user_type=SelectOrganizationType.CLIENT,
    )
    assert result.isometrik_details is None


@pytest.mark.asyncio
async def test_signup_no_session_raises(monkeypatch):
    """Signup without session raises InternalServerErrorException."""
    svc = _service()
    svc.supabase_client = MagicMock()
    svc.supabase_admin_client = None
    signup_user = SimpleNamespace(
        id="user-1",
        email="new@example.com",
        user_metadata={},
    )
    signup_result = SimpleNamespace(user=signup_user, session=None)
    monkeypatch.setattr(AuthService, "_validate_verification_code_for_signup", AsyncMock())
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.sign_up_supabase_user",
        AsyncMock(return_value=signup_result),
    )
    monkeypatch.setattr(
        AuthService, "_get_session_after_signup", lambda self, signup_result=None: None
    )

    from apps.user_service.app.schemas.auth import SignupRequest

    with pytest.raises(InternalServerErrorException):
        await svc.signup(
            SignupRequest(
                email="new@example.com",
                password="Strong1!",
                first_name="New",
                verification_id="ver-1",
                verification_code="123456",
            )
        )


@pytest.mark.asyncio
async def test_signup_rate_limit(monkeypatch):
    """Signup maps rate limit errors to TooManyRequestsException."""
    svc = _service()
    svc.supabase_client = MagicMock()
    svc.supabase_admin_client = None
    monkeypatch.setattr(AuthService, "_validate_verification_code_for_signup", AsyncMock())
    err = AuthApiError("rate", 429, None)
    err.code = "over_request_rate_limit"
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.sign_up_supabase_user",
        AsyncMock(side_effect=err),
    )

    from apps.user_service.app.schemas.auth import SignupRequest

    with pytest.raises(TooManyRequestsException):
        await svc.signup(
            SignupRequest(
                email="new@example.com",
                password="Strong1!",
                first_name="New",
                verification_id="ver-1",
                verification_code="123456",
            )
        )


@pytest.mark.asyncio
async def test_validate_account_login_invalid_password():
    """validate_account LOGIN rejects bad password."""
    svc = _service(user_repo=_FakeUserRepo(user={"id": "user-1", "raw_user_meta_data": {}}))
    svc.user_repository._verify_credentials_by_email = AsyncMock(return_value=False)
    from apps.user_service.app.schemas.auth import ValidateAccountTrigger

    with pytest.raises(BadRequestException):
        await svc.validate_account(
            ValidateAccountTrigger.LOGIN,
            "user@example.com",
            "WrongPass1!",
        )


@pytest.mark.asyncio
async def test_reset_password_no_user_in_result(monkeypatch):
    """reset_password rejects empty Supabase user payload."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_by_user_id",
        AsyncMock(return_value=SimpleNamespace(user=None)),
    )
    with pytest.raises(BadRequestException):
        await svc.reset_password("user-1", "Strong1!")


@pytest.mark.asyncio
async def test_change_password_update_failed(monkeypatch):
    """change_password rejects failed Supabase password update."""
    svc = _service(user_repo=_FakeUserRepo())
    svc.user_repository.verify_current_password = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_with_link_identity",
        AsyncMock(return_value=None),
    )
    with pytest.raises(BadRequestException):
        await svc.change_password(
            "user-1",
            "user@example.com",
            "OldPass1!",
            "NewPass1!",
            {},
        )


def test_extract_session_and_get_session_after_signup():
    """Session helper methods handle missing access tokens."""
    assert AuthService._extract_session(None) is None
    session = SimpleNamespace(access_token="tok")
    svc = _service()
    assert svc._get_session_after_signup(SimpleNamespace(session=session)) is session
    assert svc._get_session_after_signup(SimpleNamespace(session=None)) is None
