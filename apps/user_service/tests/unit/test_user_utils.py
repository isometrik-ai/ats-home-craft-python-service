"""Unit tests for user utility helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from apps.user_service.app.schemas.enums import OrganizationStatus
from apps.user_service.app.utils import user_utils


def test_build_full_name_joins_parts() -> None:
    """Full name should join non-empty parts with spaces."""
    assert user_utils.build_full_name("Jane", "", "Doe") == "Jane Doe"


def test_create_admin_update_email_content() -> None:
    """Admin update email should include subject and magic link."""
    subject, html = user_utils.create_admin_update_email_content(
        {"first_name": "Jane", "last_name": "Doe"},
        "https://example.com/magic",
    )
    assert "Email Has Been Updated" in subject
    assert "https://example.com/magic" in html


@pytest.mark.asyncio
async def test_send_admin_update_email_success(monkeypatch) -> None:
    """Successful magic link generation should send HTML email."""
    monkeypatch.setattr(user_utils, "generate_magic_link", AsyncMock(return_value="https://link"))
    monkeypatch.setattr(user_utils, "send_email", MagicMock(return_value=True))
    ok = await user_utils.send_admin_update_email(
        MagicMock(),
        {"id": "u1", "first_name": "A", "last_name": "B", "email": "a@example.com"},
    )
    assert ok is True


@pytest.mark.asyncio
async def test_send_admin_update_email_no_magic_link() -> None:
    """Missing magic link should return False without raising."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(user_utils, "generate_magic_link", AsyncMock(return_value=None))
        ok = await user_utils.send_admin_update_email(
            MagicMock(),
            {"email": "a@example.com"},
        )
    assert ok is False


@pytest.mark.asyncio
async def test_update_supabase_user_email_not_found() -> None:
    """Missing org member should raise 404."""
    repo = MagicMock()
    repo.get_user_profile_by_id = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await user_utils.update_supabase_user_email(
            "u1", "org-1", "new@example.com", repo, MagicMock()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_isometrik_details_inactive_org() -> None:
    """Inactive organizations should raise forbidden."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(
        return_value={"status": OrganizationStatus.SUSPENDED.value, "settings": {}}
    )
    with pytest.raises(user_utils.ForbiddenException):
        await user_utils.get_isometrik_details(
            user_id="u1",
            organization_id="org-1",
            organization_repository=org_repo,
        )


@pytest.mark.asyncio
async def test_update_supabase_user_email_update_failed() -> None:
    """Failed Supabase email update raises 500."""
    repo = MagicMock()
    repo.get_user_profile_by_id = AsyncMock(return_value={"first_name": "Jane"})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(user_utils, "update_email", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as exc:
            await user_utils.update_supabase_user_email(
                "u1", "org-1", "new@example.com", repo, MagicMock()
            )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_supabase_user_email_success(monkeypatch) -> None:
    """Successful email update sends admin notification."""
    repo = MagicMock()
    repo.get_user_profile_by_id = AsyncMock(return_value={"first_name": "Jane", "last_name": "Doe"})
    repo.update_user_email = AsyncMock(return_value=True)
    monkeypatch.setattr(user_utils, "update_email", AsyncMock(return_value={"id": "u1"}))
    monkeypatch.setattr(user_utils, "send_admin_update_email", AsyncMock(return_value=True))

    await user_utils.update_supabase_user_email("u1", "org-1", "new@example.com", repo, MagicMock())

    repo.update_user_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_supabase_user_email_internal_error() -> None:
    """Unexpected failures are wrapped as HTTP 500."""
    repo = MagicMock()
    repo.get_user_profile_by_id = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(HTTPException) as exc:
        await user_utils.update_supabase_user_email(
            "u1", "org-1", "new@example.com", repo, MagicMock()
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_get_isometrik_details_org_not_found() -> None:
    """Missing organization returns None."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value=None)
    assert (
        await user_utils.get_isometrik_details(
            organization_id="org-1",
            organization_repository=org_repo,
        )
        is None
    )


@pytest.mark.asyncio
async def test_get_isometrik_details_no_credentials() -> None:
    """Active org without Isometrik settings returns None."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(
        return_value={"status": OrganizationStatus.ACTIVE.value, "settings": {}}
    )
    assert (
        await user_utils.get_isometrik_details(
            organization_id="org-1",
            organization_repository=org_repo,
        )
        is None
    )


@pytest.mark.asyncio
async def test_send_admin_update_email_send_failed(monkeypatch) -> None:
    """Failed SMTP send should return False."""
    monkeypatch.setattr(user_utils, "generate_magic_link", AsyncMock(return_value="https://link"))
    monkeypatch.setattr(user_utils, "send_email", MagicMock(return_value=False))
    ok = await user_utils.send_admin_update_email(
        MagicMock(),
        {"email": "a@example.com"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_get_isometrik_details_fallback_member_login(monkeypatch) -> None:
    """Failed user login should retry with organization member id."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(
        return_value={
            "status": OrganizationStatus.ACTIVE.value,
            "settings": {
                "isometrik_application_details": {
                    "licenseKey": "lk",
                    "userSecret": "us",
                    "appSecret": "as",
                    "projectId": "pid",
                    "keysetId": "kid",
                }
            },
        }
    )
    member_repo = MagicMock()
    member_repo.get_member_id_by_user_id = AsyncMock(return_value="member-1")

    async def _login(*, user_id, isometrik_credentials):
        del isometrik_credentials
        if user_id == "member-1":
            return {"userToken": "fallback-token"}
        raise RuntimeError("primary login failed")

    monkeypatch.setattr(user_utils, "login_to_isometrik", _login)
    details = await user_utils.get_isometrik_details(
        user_id="u1",
        organization_id="org-1",
        organization_repository=org_repo,
        organization_member_repository=member_repo,
    )
    assert details is not None
    assert details.token == "fallback-token"
    assert details.user_id == "member-1"


@pytest.mark.asyncio
async def test_update_supabase_user_email_member_update_not_found() -> None:
    """Missing org member on email update raises 404."""
    repo = MagicMock()
    repo.get_user_profile_by_id = AsyncMock(return_value={"first_name": "Jane"})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(user_utils, "update_email", AsyncMock(return_value={"id": "u1"}))
        repo.update_user_email = AsyncMock(return_value=False)
        with pytest.raises(HTTPException) as exc:
            await user_utils.update_supabase_user_email(
                "u1", "org-1", "new@example.com", repo, MagicMock()
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_isometrik_details_login_failure_without_member(monkeypatch) -> None:
    """Failed login without member fallback leaves token unset."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(
        return_value={
            "status": OrganizationStatus.ACTIVE.value,
            "settings": {
                "isometrik_application_details": {
                    "licenseKey": "lk",
                    "userSecret": "us",
                    "appSecret": "as",
                    "projectId": "pid",
                    "keysetId": "kid",
                }
            },
        }
    )
    monkeypatch.setattr(
        user_utils,
        "login_to_isometrik",
        AsyncMock(side_effect=RuntimeError("login failed")),
    )
    details = await user_utils.get_isometrik_details(
        user_id="u1",
        organization_id="org-1",
        organization_repository=org_repo,
    )
    assert details is not None
    assert details.token is None


@pytest.mark.asyncio
async def test_get_isometrik_details_returns_credentials(monkeypatch) -> None:
    """Active org with credentials should return IsometrikDetails."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(
        return_value={
            "status": OrganizationStatus.ACTIVE.value,
            "settings": {
                "isometrik_application_details": {
                    "licenseKey": "lk",
                    "userSecret": "us",
                    "appSecret": "as",
                    "projectId": "pid",
                    "keysetId": "kid",
                }
            },
        }
    )
    monkeypatch.setattr(
        user_utils,
        "login_to_isometrik",
        AsyncMock(return_value={"userToken": "token-1"}),
    )
    details = await user_utils.get_isometrik_details(
        user_id="u1",
        organization_id="org-1",
        organization_repository=org_repo,
    )
    assert details is not None
    assert details.token == "token-1"
    assert details.license_key == "lk"
