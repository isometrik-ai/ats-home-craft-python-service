"""Unit tests for AuthService.set_password orchestration (auto-login variant)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from apps.user_service.app.services.auth_service import AuthService
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    UnauthorizedException,
)


class _FakeSessionRepo:
    """In-memory stand-in for SessionRepository."""

    def __init__(self, *, old_org_id: str | None) -> None:
        self._old_org_id = old_org_id
        self.update_calls: list[dict[str, str]] = []

    async def get_valid_session_context(self, session_id: str):
        """Return a minimal session context payload for the given session_id."""
        del session_id
        if self._old_org_id is None:
            return {"organization_id": None}
        return {"organization_id": self._old_org_id}

    async def update_session_organization_context(
        self, *, session_id: str, user_id: str, organization_id: str
    ) -> None:
        """Record carryover calls for later assertions."""
        self.update_calls.append(
            {
                "session_id": session_id,
                "user_id": user_id,
                "organization_id": organization_id,
            }
        )


class _FakeOrgRepo:
    """Fake OrganizationRepository for testing."""

    async def get_user_active_organizations(self, user_id: str):
        """Return no active organizations for the given user_id."""
        del user_id
        return []


def _build_service(monkeypatch, *, old_org_id: str | None) -> tuple[AuthService, _FakeSessionRepo]:
    """Construct an AuthService instance with repositories monkey-patched."""
    service = AuthService.__new__(AuthService)
    service.db_connection = object()  # unused; SessionRepository is monkey-patched
    service.organization_repository = _FakeOrgRepo()
    service.user_repository = None  # not used by this flow
    service.supabase_client = None  # not used; admin_client/anon_client are passed explicitly

    fake_repo = _FakeSessionRepo(old_org_id=old_org_id)
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.SessionRepository",
        lambda db_connection: fake_repo,
    )
    return service, fake_repo


def _patch_password_update(monkeypatch, *, return_user: Any | None) -> None:
    """Patch password update helper to return a controlled user object."""

    async def fake_update(client, user_id, password):
        del client, user_id, password
        return return_user

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.update_password_with_link_identity",
        fake_update,
    )


def _patch_login(monkeypatch, *, session: Any | None, user: Any | None, raises: bool = False):
    """Patch login helper to return a controlled session/user or raise."""

    async def fake_login(*, email, password, sb_client):
        del email, password, sb_client
        if raises:
            raise RuntimeError("supabase down")
        return SimpleNamespace(session=session, user=user)

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.login_user",
        fake_login,
    )


def _patch_claims(monkeypatch, *, session_id: str | None) -> None:
    """Patch token claims helper to return a controlled session_id claim."""

    async def fake_claims(token, supabase_client=None):
        del token, supabase_client
        return {"session_id": session_id} if session_id else {}

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.get_claims_from_token",
        fake_claims,
    )


def _make_session_user(*, with_session: bool = True):
    """Create a minimal (session, user) tuple for the AuthService response build."""
    user = SimpleNamespace(
        id="user-1",
        email="user@example.com",
        user_metadata={"first_name": "Test", "last_name": "User", "timezone": "UTC"},
        app_metadata={"providers": ["email"]},
    )
    if not with_session:
        return None, user
    session = SimpleNamespace(
        access_token="atk",
        refresh_token="rtk",
        expires_in=3600,
        expires_at=0,
    )
    return session, user


@pytest.mark.asyncio
async def test_set_password_carries_org_to_new_session(monkeypatch):
    """Test that when the password update returns a user with an email"""
    service, fake_repo = _build_service(monkeypatch, old_org_id="org-old")

    updated_user = SimpleNamespace(email="user@example.com")
    _patch_password_update(monkeypatch, return_user=updated_user)
    session, user = _make_session_user()
    _patch_login(monkeypatch, session=session, user=user)
    _patch_claims(monkeypatch, session_id="new-session-id")

    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    result = await service.set_password(
        user_id="user-1",
        current_session_id="old-session-id",
        password="NewPass123!",
        admin_client=object(),
        anon_client=object(),
    )

    assert result.auth.access_token == "atk"
    assert result.auth.user.email == "user@example.com"
    assert fake_repo.update_calls == [
        {
            "session_id": "new-session-id",
            "user_id": "user-1",
            "organization_id": "org-old",
        }
    ]


@pytest.mark.asyncio
async def test_set_password_no_old_org_skips_carryover(monkeypatch):
    """Test that when there is no old organization, the carryover is skipped."""
    service, fake_repo = _build_service(monkeypatch, old_org_id=None)

    _patch_password_update(monkeypatch, return_user=SimpleNamespace(email="user@example.com"))
    session, user = _make_session_user()
    _patch_login(monkeypatch, session=session, user=user)
    _patch_claims(monkeypatch, session_id="new-session-id")
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    result = await service.set_password(
        user_id="user-1",
        current_session_id="old-session-id",
        password="NewPass123!",
        admin_client=object(),
        anon_client=object(),
    )

    assert result.auth.access_token == "atk"
    assert not fake_repo.update_calls  # no org carryover


@pytest.mark.asyncio
async def test_set_password_no_session_id_still_works(monkeypatch):
    """Test that when there is no session id, the carryover is skipped."""
    service, fake_repo = _build_service(monkeypatch, old_org_id=None)
    _patch_password_update(monkeypatch, return_user=SimpleNamespace(email="user@example.com"))
    session, user = _make_session_user()
    _patch_login(monkeypatch, session=session, user=user)
    _patch_claims(monkeypatch, session_id=None)
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    result = await service.set_password(
        user_id="user-1",
        current_session_id=None,
        password="NewPass123!",
        admin_client=object(),
        anon_client=object(),
    )

    assert result.auth.access_token == "atk"
    assert not fake_repo.update_calls


@pytest.mark.asyncio
async def test_set_password_update_none_raises_bad_request(monkeypatch):
    """Test that when the password update returns None, a BadRequestException is raised."""
    service, _ = _build_service(monkeypatch, old_org_id=None)
    _patch_password_update(monkeypatch, return_user=None)
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    with pytest.raises(BadRequestException):
        await service.set_password(
            user_id="user-1",
            current_session_id=None,
            password="NewPass123!",
            admin_client=object(),
            anon_client=object(),
        )


@pytest.mark.asyncio
async def test_set_password_missing_email_raises_bad_request(monkeypatch):
    """Test that when the password update returns a user with no email."""
    service, _ = _build_service(monkeypatch, old_org_id=None)
    _patch_password_update(monkeypatch, return_user=SimpleNamespace(email=None))
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    with pytest.raises(BadRequestException):
        await service.set_password(
            user_id="user-1",
            current_session_id=None,
            password="NewPass123!",
            admin_client=object(),
            anon_client=object(),
        )


@pytest.mark.asyncio
async def test_set_password_login_failure_raises_unauthorized(monkeypatch):
    """Test that when the login fails, a UnauthorizedException is raised."""
    service, _ = _build_service(monkeypatch, old_org_id=None)
    _patch_password_update(monkeypatch, return_user=SimpleNamespace(email="user@example.com"))
    _patch_login(monkeypatch, session=None, user=None, raises=True)
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    with pytest.raises(UnauthorizedException):
        await service.set_password(
            user_id="user-1",
            current_session_id=None,
            password="NewPass123!",
            admin_client=object(),
            anon_client=object(),
        )


@pytest.mark.asyncio
async def test_set_password_incomplete_session_unauthorized(monkeypatch):
    """Test that when the session is incomplete, a UnauthorizedException is raised."""
    service, _ = _build_service(monkeypatch, old_org_id=None)
    _patch_password_update(monkeypatch, return_user=SimpleNamespace(email="user@example.com"))
    # session lacks access_token
    bad_session = SimpleNamespace(access_token=None)
    _patch_login(
        monkeypatch,
        session=bad_session,
        user=SimpleNamespace(id="user-1"),
    )
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    with pytest.raises(UnauthorizedException):
        await service.set_password(
            user_id="user-1",
            current_session_id=None,
            password="NewPass123!",
            admin_client=object(),
            anon_client=object(),
        )


@pytest.mark.asyncio
async def test_set_password_carryover_failure_is_non_fatal(monkeypatch):
    """If updating the new session row fails, we still return the AuthResponse."""
    service, fake_repo = _build_service(monkeypatch, old_org_id="org-old")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(fake_repo, "update_session_organization_context", boom)

    _patch_password_update(monkeypatch, return_user=SimpleNamespace(email="user@example.com"))
    session, user = _make_session_user()
    _patch_login(monkeypatch, session=session, user=user)
    _patch_claims(monkeypatch, session_id="new-session-id")
    monkeypatch.setattr(AuthService, "_validate_password_strength", lambda self, p: None)

    result = await service.set_password(
        user_id="user-1",
        current_session_id="old-session-id",
        password="NewPass123!",
        admin_client=object(),
        anon_client=object(),
    )

    assert result.auth.access_token == "atk"
