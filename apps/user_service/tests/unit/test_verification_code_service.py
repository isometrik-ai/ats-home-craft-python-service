"""Unit tests for VerificationCodeService helpers and flows."""

from __future__ import annotations

import types
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerificationType,
    VerifyVerificationCodeRequest,
)
from apps.user_service.app.services.verification_code_service import (
    VerificationCodeService,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GoneException,
    TooManyRequestsException,
    UnauthorizedException,
)


class _FakeUserRepo:
    """Lightweight fake user repository."""

    def __init__(self):
        """Initialize fake repository."""
        self.calls = {}
        self.user_by_email = None
        self.phone_exists = False

    async def get_auth_user_by_email(self, email):
        """Return fake user object for email."""
        self.calls["get_auth_user_by_email"] = email
        return self.user_by_email

    async def phone_exists_for_other_user(self, phone, user_id):
        """Return whether phone exists for another user."""
        self.calls["phone_exists_for_other_user"] = (phone, user_id)
        return self.phone_exists


class _FakeVerificationRepo:
    """Fake verification code repository."""

    def __init__(self):
        """Initialize fake repo state."""
        self.record: dict[str, Any] | None = None
        self.inserted: dict[str, Any] | None = None
        self.updated: dict[str, Any] | None = None
        self.recent_codes: list[dict[str, Any]] = []

    async def get_verification_code_by_id(self, verification_id: str):
        """Return configured verification record."""
        del verification_id
        return self.record

    async def insert_verification_code(self, verification_data: dict[str, Any]):
        """Store inserted verification row."""
        row = {"id": "ver-1", **verification_data}
        self.inserted = row
        self.record = row
        return row

    async def update_verification_code(self, verification_id: str, **kwargs):
        """Update verification record."""
        del verification_id
        self.updated = kwargs
        if self.record:
            self.record.update(kwargs)

    async def get_recent_verification_codes(self, **kwargs):
        """Return recent codes for rate limiting."""
        del kwargs
        return self.recent_codes


class _FakeOrgMemberRepo:
    """Unused but required for service init."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection

    async def update_user_email_by_user_id(self, user_id: str, new_email: str) -> int:
        """Record cross-org email update."""
        del user_id, new_email
        return 1


@pytest.fixture
def service(monkeypatch):
    """Provide service with fake repos."""
    user_repo = _FakeUserRepo()
    verification_repo = _FakeVerificationRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.UserRepository",
        lambda db_connection=None: user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.VerificationCodeRepository",
        lambda db_connection=None: verification_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.OrganizationMemberRepository",
        lambda db_connection=None: _FakeOrgMemberRepo(),
    )

    svc = VerificationCodeService(db_connection=None, sb_client=None)
    svc.verification_code_repository = verification_repo
    svc.user_repository = user_repo
    return svc, user_repo, verification_repo


def test_sanitize_ip_and_get_client_ip():
    """Sanitize IP helper should accept valid and drop invalid."""
    assert VerificationCodeService._sanitize_ip("1.1.1.1") == "1.1.1.1"
    assert VerificationCodeService._sanitize_ip("bad-ip") is None

    req = types.SimpleNamespace(
        headers={"X-Forwarded-For": "2.2.2.2"},
        client=types.SimpleNamespace(host="3.3.3.3"),
    )
    assert VerificationCodeService.get_client_ip(req) == "2.2.2.2"


def test_normalize_phone():
    """Normalize phone removes '+'."""
    assert VerificationCodeService._normalize_phone("+123") == "123"
    assert VerificationCodeService._normalize_phone("123") == "123"


def test_combine_phone():
    """_combine_phone joins ISD code and number."""
    assert VerificationCodeService._combine_phone("9876543210", "+91") == "+919876543210"
    assert VerificationCodeService._combine_phone(None, "+91") is None


def test_determine_triggered_text_signup(service):
    """Unauthenticated email send uses signup trigger."""
    svc, _, _ = service
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="a@b.com")
    text = svc._determine_triggered_text(data, current_user=None)
    assert text == "SIGNUP_EMAIL_VERIFICATION"


def test_determine_triggered_text_update(service):
    """Authenticated email send uses email update trigger."""
    svc, _, _ = service
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="a@b.com")
    text = svc._determine_triggered_text(data, current_user={"sub": "u1"})
    assert text == "EMAIL_UPDATE"


@pytest.mark.asyncio
async def test_validate_email_for_update_same_email_raises(service):
    """Raises BadRequest when email same as current."""
    svc, user_repo, _ = service
    user_repo.user_by_email = None

    with pytest.raises(BadRequestException):
        await svc._validate_email_for_update(
            email="user@example.com",
            user_id="u1",
            current_user_email="user@example.com",
        )


@pytest.mark.asyncio
async def test_validate_email_for_update_conflict(service):
    """Raises Conflict when email belongs to another user."""
    svc, user_repo, _ = service
    user_repo.user_by_email = {"id": "u2"}

    with pytest.raises(ConflictException):
        await svc._validate_email_for_update(
            email="other@example.com",
            user_id="u1",
            current_user_email="user@example.com",
        )


@pytest.mark.asyncio
async def test_check_phone_exists_conflict(service):
    """Raises Conflict when phone belongs to another user."""
    svc, user_repo, _ = service
    user_repo.phone_exists = True

    with pytest.raises(ConflictException):
        await svc._check_phone_exists_for_other_user(phone="+123", user_id="u1")


@pytest.mark.asyncio
async def test_check_phone_exists_ok(service):
    """No error when phone free."""
    svc, user_repo, _ = service
    user_repo.phone_exists = False

    await svc._check_phone_exists_for_other_user(phone="+123", user_id="u1")

    assert user_repo.calls["phone_exists_for_other_user"][0] == "123"


def test_validate_verification_record_expired(service):
    """_validate_verification_record raises GoneException when expired."""
    svc, _, _ = service
    past_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    record = {
        "verified": False,
        "expiry_at": past_ms,
        "given_input": "user@example.com",
        "verification_code": "123456",
    }
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="ver-1",
        verification_code="123456",
        email="user@example.com",
    )
    with pytest.raises(GoneException):
        svc._validate_verification_record(record, data)


def test_validate_verification_record_already_verified(service):
    """_validate_verification_record rejects already verified codes."""
    svc, _, _ = service
    future_ms = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    record = {
        "verified": True,
        "expiry_at": future_ms,
        "given_input": "user@example.com",
    }
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="ver-1",
        verification_code="123456",
        email="user@example.com",
    )
    with pytest.raises(BadRequestException):
        svc._validate_verification_record(record, data)


def test_check_verification_code_ownership_mismatch(service):
    """_check_verification_code_ownership rejects wrong user."""
    svc, _, _ = service
    record = {"user_id": "other-user"}
    current_user = {"sub": "u1"}
    with pytest.raises(ForbiddenException):
        svc._check_verification_code_ownership(record, current_user)


@pytest.mark.asyncio
async def test_verify_code_and_update_record_success(service):
    """_verify_code_and_update_record marks record verified on match."""
    svc, _, verification_repo = service
    future_ms = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    record = {
        "verification_code": "654321",
        "attempts": [],
        "expiry_at": future_ms,
    }

    matched = await svc._verify_code_and_update_record(record, "654321", "ver-1")

    assert matched is True
    assert verification_repo.updated["verified"] is True


@pytest.mark.asyncio
async def test_verify_code_and_update_record_invalid(service):
    """_verify_code_and_update_record raises on wrong code."""
    svc, _, _ = service
    record = {"verification_code": "654321", "attempts": []}
    with pytest.raises(BadRequestException):
        await svc._verify_code_and_update_record(record, "000000", "ver-1")


@pytest.mark.asyncio
async def test_send_verification_code_signup(monkeypatch, service):
    """send_verification_code creates record for signup email."""
    svc, user_repo, verification_repo = service
    user_repo.user_by_email = None
    monkeypatch.setattr(svc, "_check_rate_limit", AsyncMock(return_value=2))
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.send_verification_code_email",
        lambda **kwargs: True,
    )
    req = types.SimpleNamespace(
        headers={"X-Forwarded-For": "1.2.3.4"},
        client=types.SimpleNamespace(host="5.6.7.8"),
    )
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    result = await svc.send_verification_code(req, data, current_user=None)

    assert result["verification_id"] == "ver-1"
    assert verification_repo.inserted is not None
    assert result["attemptsLeft"] == 2


@pytest.mark.asyncio
async def test_send_verification_code_registered_email(service):
    """Signup send rejects already registered email."""
    svc, user_repo, _ = service
    user_repo.user_by_email = {"id": "existing"}
    req = types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="1.1.1.1"))
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="taken@example.com")

    with pytest.raises(BadRequestException):
        await svc.send_verification_code(req, data, current_user=None)


@pytest.mark.asyncio
async def test_verify_verification_code_success(service):
    """verify_verification_code returns verified true on valid OTP."""
    svc, _, verification_repo = service
    future_ms = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    verification_repo.record = {
        "id": "ver-1",
        "verified": False,
        "expiry_at": future_ms,
        "given_input": "user@example.com",
        "verification_code": "123456",
        "triggered_text": "signup_email_verification",
        "attempts": [],
    }
    req = types.SimpleNamespace(state=types.SimpleNamespace(access_token=None))
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="ver-1",
        verification_code="123456",
        email="user@example.com",
    )

    result = await svc.verify_verification_code(req, data, current_user=None)

    assert result["verified"] is True


def test_determine_triggered_text_phone_signup(service):
    """Unauthenticated phone send uses signup phone trigger."""
    svc, _, _ = service
    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phone_number="9876543210",
        phone_isd_code="+91",
    )
    text = svc._determine_triggered_text(data, current_user=None)
    assert text == "SIGNUP_PHONE_VERIFICATION"


def test_get_client_ip_prefers_forwarded_header(service):
    """get_client_ip uses first X-Forwarded-For entry when present."""
    req = types.SimpleNamespace(
        headers={"X-Forwarded-For": "9.9.9.9, 8.8.8.8"},
        client=types.SimpleNamespace(host="1.1.1.1"),
    )
    assert VerificationCodeService.get_client_ip(req) == "9.9.9.9"


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone(service):
    """_check_auth_user_exists_by_phone delegates to user repository."""
    svc, user_repo, _ = service
    user_repo.phone_exists = True
    assert await svc._check_auth_user_exists_by_phone("+919876543210") is True


@pytest.mark.asyncio
async def test_validate_phone_for_update_same_phone(service, monkeypatch):
    """_validate_phone_for_update rejects unchanged phone numbers."""
    svc, _, _ = service
    user_obj = types.SimpleNamespace(
        user=types.SimpleNamespace(
            user_metadata={"phone_number": "9876543210", "phone_isd_code": "+91"}
        )
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.get_user_by_id",
        AsyncMock(return_value=user_obj),
    )
    with pytest.raises(BadRequestException):
        await svc._validate_phone_for_update("+919876543210", "u1")


@pytest.mark.asyncio
async def test_check_rate_limit_exceeded(service, monkeypatch):
    """_check_rate_limit raises when max unverified attempts reached."""
    svc, _, verification_repo = service
    verification_repo.recent_codes = [{"verified": False}] * 5
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.app_settings.two_fa_settings",
        types.SimpleNamespace(
            max_attempt_verification=5,
            verification_attempt_window_hours=1,
            verification_code_expiry_minutes=10,
            email_otp_enabled=False,
            phone_otp_enabled=False,
            email_default_otp="1234",
            phone_default_otp="1234",
        ),
    )
    with pytest.raises(TooManyRequestsException):
        await svc._check_rate_limit("EMAIL", "user@example.com")


@pytest.mark.asyncio
async def test_send_verification_code_phone(monkeypatch, service):
    """send_verification_code creates phone verification record."""
    svc, user_repo, verification_repo = service
    user_repo.phone_exists = False
    monkeypatch.setattr(svc, "_check_rate_limit", AsyncMock(return_value=1))
    req = types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="1.1.1.1"))
    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phone_number="9876543210",
        phone_isd_code="+91",
    )

    result = await svc.send_verification_code(req, data, current_user=None)

    assert result["verification_id"] == "ver-1"
    assert verification_repo.inserted["given_input"] == "+919876543210"


@pytest.mark.asyncio
async def test_send_verification_code_registered_phone(service):
    """Signup phone send rejects already registered numbers."""
    svc, user_repo, _ = service
    user_repo.phone_exists = True
    req = types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="1.1.1.1"))
    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phone_number="9876543210",
        phone_isd_code="+91",
    )

    with pytest.raises(BadRequestException):
        await svc.send_verification_code(req, data, current_user=None)


def test_validate_verification_record_input_mismatch(service):
    """_validate_verification_record rejects mismatched email input."""
    svc, _, _ = service
    future_ms = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    record = {
        "verified": False,
        "expiry_at": future_ms,
        "given_input": "other@example.com",
        "verification_code": "123456",
    }
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="ver-1",
        verification_code="123456",
        email="user@example.com",
    )
    with pytest.raises(BadRequestException):
        svc._validate_verification_record(record, data)


@pytest.mark.asyncio
async def test_determine_user_context_two_factor_auth(monkeypatch, service):
    """TWO_FACTOR_AUTH verification_method skips signup existence checks."""
    svc, user_repo, _ = service
    user_repo.user_by_email = {"id": "existing"}
    data = SendVerificationCodeRequest(
        type=VerificationType.EMAIL,
        email="user@example.com",
        verification_method="TWO_FACTOR_AUTH",
    )

    user_id, triggered_text = await svc._determine_user_context(data, current_user=None)

    assert user_id is None
    assert triggered_text == "TWO_FACTOR_AUTH"


@pytest.mark.asyncio
async def test_verify_verification_code_missing_token(service):
    """Authenticated email update verification requires access token."""
    svc, _, verification_repo = service
    future_ms = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    verification_repo.record = {
        "id": "ver-1",
        "verified": False,
        "expiry_at": future_ms,
        "given_input": "new@example.com",
        "verification_code": "123456",
        "triggered_text": "EMAIL_UPDATE",
        "attempts": [],
        "user_id": "u1",
    }
    req = types.SimpleNamespace(state=types.SimpleNamespace(access_token=None))
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="ver-1",
        verification_code="123456",
        email="new@example.com",
    )

    with pytest.raises(UnauthorizedException):
        await svc.verify_verification_code(
            req,
            data,
            current_user={"sub": "u1"},
        )


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_email(service, monkeypatch):
    """Authenticated email send validates update and returns trigger."""
    svc, user_repo, _ = service
    user_repo.user_by_email = None
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.get_user_by_id",
        AsyncMock(
            return_value=types.SimpleNamespace(
                user=types.SimpleNamespace(email="old@example.com"),
            )
        ),
    )
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    user_id, triggered = await svc._validate_authenticated_user_input(
        data,
        current_user={"sub": "u1"},
    )

    assert user_id == "u1"
    assert triggered == "EMAIL_UPDATE"


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_phone(service, monkeypatch):
    """Authenticated phone send validates update and returns trigger."""
    svc, _, _ = service
    user_obj = types.SimpleNamespace(
        user=types.SimpleNamespace(
            user_metadata={"phone_number": "1111111111", "phone_isd_code": "+1"},
        )
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.get_user_by_id",
        AsyncMock(return_value=user_obj),
    )
    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phone_number="2222222222",
        phone_isd_code="+1",
    )

    user_id, triggered = await svc._validate_authenticated_user_input(
        data,
        current_user={"sub": "u1"},
    )

    assert user_id == "u1"
    assert triggered == "PHONE_NUMBER_UPDATE"


@pytest.mark.asyncio
async def test_verify_verification_code_updates_email(service, monkeypatch):
    """Successful email update verification updates Supabase user email."""
    svc, _, verification_repo = service
    future_ms = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    verification_repo.record = {
        "id": "ver-1",
        "verified": False,
        "expiry_at": future_ms,
        "given_input": "new@example.com",
        "verification_code": "123456",
        "triggered_text": "EMAIL_UPDATE",
        "attempts": [],
        "user_id": "u1",
    }
    req = types.SimpleNamespace(state=types.SimpleNamespace(access_token="token-abc"))
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="ver-1",
        verification_code="123456",
        email="new@example.com",
    )
    monkeypatch.setattr(svc, "_update_email_or_phone", AsyncMock(return_value=(True, False)))

    result = await svc.verify_verification_code(req, data, current_user={"sub": "u1"})

    assert result["verified"] is True
    svc._update_email_or_phone.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_email_or_phone_phone_path(service, monkeypatch):
    """_update_email_or_phone updates phone when trigger is phone update."""
    svc, _, _ = service
    monkeypatch.setattr(svc, "_validate_and_set_session", AsyncMock())
    monkeypatch.setattr(svc, "_update_user_phone", AsyncMock(return_value=True))

    email_updated, phone_updated = await svc._update_email_or_phone(
        "u1",
        "+12222222222",
        "PHONE_NUMBER_UPDATE",
        "token-abc",
        phone_number="2222222222",
        phone_isd_code="+1",
    )

    assert email_updated is False
    assert phone_updated is True


@pytest.mark.asyncio
async def test_update_user_email_success(service):
    """_update_user_email updates auth and organization member rows."""
    svc, _, _ = service
    admin = MagicMock()
    admin.get_user_by_id = AsyncMock(
        side_effect=[
            types.SimpleNamespace(
                user=types.SimpleNamespace(user_metadata={}, email="old@example.com")
            ),
            types.SimpleNamespace(user=types.SimpleNamespace(email="new@example.com")),
        ]
    )
    admin.update_user_by_id = AsyncMock(
        return_value=types.SimpleNamespace(user=types.SimpleNamespace())
    )
    svc.supabase_client = types.SimpleNamespace(auth=types.SimpleNamespace(admin=admin))
    svc.organization_member_repository.update_user_email_by_user_id = AsyncMock(return_value=1)

    updated = await svc._update_user_email("u1", "new@example.com")

    assert updated is True


def test_get_client_ip_from_client_host():
    """get_client_ip falls back to request.client.host."""
    req = types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="7.7.7.7"))
    assert VerificationCodeService.get_client_ip(req) == "7.7.7.7"
