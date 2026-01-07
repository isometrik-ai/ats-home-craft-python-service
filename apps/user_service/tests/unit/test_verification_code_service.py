"""Unit tests for VerificationCodeService helpers."""

import types

import pytest

from apps.user_service.app.services.verification_code_service import (
    VerificationCodeService,
)
from libs.shared_utils.http_exceptions import BadRequestException, ConflictException


class _FakeUserRepo:
    """Lightweight fake user repository."""

    def __init__(self):
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
    """Unused but required for service init."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection


class _FakeOrgMemberRepo:
    """Unused but required for service init."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection


@pytest.fixture
def service(monkeypatch):
    """Provide service with fake repos."""

    user_repo = _FakeUserRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.UserRepository",
        lambda db_connection=None: user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.VerificationCodeRepository",
        lambda db_connection=None: _FakeVerificationRepo(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.OrganizationMemberRepository",
        lambda db_connection=None: _FakeOrgMemberRepo(),
    )

    svc = VerificationCodeService(db_connection=None, sb_client=None)
    return svc, user_repo


def test_sanitize_ip_and_get_client_ip():
    """Sanitize IP helper should accept valid and drop invalid."""

    assert VerificationCodeService._sanitize_ip("1.1.1.1") == "1.1.1.1"  # pylint: disable=protected-access
    assert (
        VerificationCodeService._sanitize_ip("bad-ip") is None  # pylint: disable=protected-access
    )

    req = types.SimpleNamespace(
        headers={"X-Forwarded-For": "2.2.2.2"},
        client=types.SimpleNamespace(host="3.3.3.3"),
    )
    assert VerificationCodeService.get_client_ip(req) == "2.2.2.2"


def test_normalize_phone():
    """Normalize phone removes '+'."""

    assert VerificationCodeService._normalize_phone("+123") == "123"  # pylint: disable=protected-access
    assert VerificationCodeService._normalize_phone("123") == "123"  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_validate_email_for_update_same_email_raises(service):
    """Raises BadRequest when email same as current."""

    svc, user_repo = service
    user_repo.user_by_email = None

    with pytest.raises(BadRequestException):
        await svc._validate_email_for_update(  # pylint: disable=protected-access
            email="user@example.com",
            user_id="u1",
            current_user_email="user@example.com",
        )


@pytest.mark.asyncio
async def test_validate_email_for_update_conflict(service):
    """Raises Conflict when email belongs to another user."""

    svc, user_repo = service
    user_repo.user_by_email = {"id": "u2"}

    with pytest.raises(ConflictException):
        await svc._validate_email_for_update(  # pylint: disable=protected-access
            email="other@example.com",
            user_id="u1",
            current_user_email="user@example.com",
        )


@pytest.mark.asyncio
async def test_check_phone_exists_conflict(service):
    """Raises Conflict when phone belongs to another user."""

    svc, user_repo = service
    user_repo.phone_exists = True

    with pytest.raises(ConflictException):
        await svc._check_phone_exists_for_other_user(  # pylint: disable=protected-access
            phone="+123",
            user_id="u1",
        )


@pytest.mark.asyncio
async def test_check_phone_exists_ok(service):
    """No error when phone free."""

    svc, user_repo = service
    user_repo.phone_exists = False

    await svc._check_phone_exists_for_other_user(  # pylint: disable=protected-access
        phone="+123",
        user_id="u1",
    )

    assert user_repo.calls["phone_exists_for_other_user"][0] == "123"
