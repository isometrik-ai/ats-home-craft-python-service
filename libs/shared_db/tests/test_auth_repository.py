"""Unit tests for Supabase auth_repository helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.auth import SignupRequest
from libs.shared_db.supabase_db import auth_repository
from libs.shared_utils.http_exceptions import BadRequestException


def _mock_user(**fields):
    """Build a mock Supabase user with model_dump support."""
    user = MagicMock()
    user.model_dump.return_value = {"id": "user-1", **fields}
    return user


def _mock_client(**admin_methods) -> MagicMock:
    """Build a mock AsyncClient with nested auth.admin methods."""
    client = MagicMock()
    for name, impl in admin_methods.items():
        setattr(client.auth.admin, name, impl)
    for name in ("sign_up", "sign_in_with_password", "reset_password_email", "refresh_session"):
        if name not in admin_methods:
            setattr(client.auth, name, AsyncMock())
    return client


@pytest.mark.asyncio
async def test_create_user_requires_email_or_phone():
    """create_user rejects payloads with neither email nor phone."""
    with pytest.raises(BadRequestException):
        await auth_repository.create_user(_mock_client(), email=None, phone=None)


@pytest.mark.asyncio
async def test_create_user_returns_user_payload():
    """create_user returns serialized user data from Supabase admin API."""
    user = _mock_user(email="user@example.com")
    client = _mock_client(create_user=AsyncMock(return_value=MagicMock(user=user)))
    result = await auth_repository.create_user(
        client, email="user@example.com", password="Secret1!"
    )
    assert result["id"] == "user-1"


@pytest.mark.asyncio
async def test_get_user_by_id():
    """get_user_by_id fetches auth user by id."""
    user = _mock_user(email="user@example.com")
    client = _mock_client(get_user_by_id=AsyncMock(return_value=MagicMock(user=user)))
    result = await auth_repository.get_user_by_id(client, "user-1")
    assert result["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_delete_user_and_delete_auth_user():
    """delete helpers delegate to Supabase admin delete_user."""
    client = _mock_client(delete_user=AsyncMock())
    assert await auth_repository.delete_user(client, "user-1") is True
    assert await auth_repository.delete_auth_user(client, "user-1") is True


@pytest.mark.asyncio
async def test_list_users_returns_model_dump_rows():
    """list_users serializes each auth user."""
    users = [_mock_user(email="a@example.com"), _mock_user(email="b@example.com")]
    client = _mock_client(list_users=AsyncMock(return_value=MagicMock(users=users)))
    result = await auth_repository.list_users(client, page=1, per_page=10)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_ban_and_unban_user():
    """ban/unban helpers return True when Supabase returns a user."""
    client = _mock_client(update_user_by_id=AsyncMock(return_value=MagicMock(user=_mock_user())))
    assert await auth_repository.ban_user(client, "user-1") is True
    assert await auth_repository.unban_user(client, "user-1") is True


@pytest.mark.asyncio
async def test_update_email_and_metadata():
    """update_email and update_metadata return True on successful update."""
    client = _mock_client(update_user_by_id=AsyncMock(return_value=MagicMock(user=_mock_user())))
    assert await auth_repository.update_email(client, "user-1", "new@example.com") is True
    assert await auth_repository.update_metadata(client, "user-1", {"first_name": "Jane"}) is True


@pytest.mark.asyncio
async def test_update_phone_merges_metadata():
    """update_phone stores phone fields in user_metadata and auth phone."""
    client = _mock_client(update_user_by_id=AsyncMock(return_value=MagicMock(user=_mock_user())))
    ok = await auth_repository.update_phone(
        client,
        "user-1",
        {"first_name": "Jane"},
        "9876543210",
        "+91",
    )
    assert ok is True
    payload = client.auth.admin.update_user_by_id.await_args.args[1]
    assert payload["phone"] == "+919876543210"
    assert payload["user_metadata"]["phone_number"] == "9876543210"


@pytest.mark.asyncio
async def test_update_password_with_link_identity_adds_email_provider():
    """update_password_with_link_identity adds email provider for OAuth-only users."""
    existing_user = MagicMock()
    existing_user.app_metadata = {"providers": ["google"]}
    existing_user.user_metadata = {"first_name": "Jane"}
    client = _mock_client(
        get_user_by_id=AsyncMock(return_value=MagicMock(user=existing_user)),
        update_user_by_id=AsyncMock(return_value=MagicMock(user=existing_user)),
    )

    result = await auth_repository.update_password_with_link_identity(
        client,
        "user-1",
        "Secret1!",
    )

    assert result is existing_user
    attrs = client.auth.admin.update_user_by_id.await_args.args[1]
    assert "email" in attrs["app_metadata"]["providers"]


@pytest.mark.asyncio
async def test_generate_magic_link_success_and_failure():
    """generate_magic_link returns action link or None."""
    props = MagicMock(action_link="https://example.com/magic")
    client = _mock_client(generate_link=AsyncMock(return_value=MagicMock(properties=props)))
    assert (
        await auth_repository.generate_magic_link(client, "user@example.com") == props.action_link
    )

    client_fail = _mock_client(generate_link=AsyncMock(return_value=MagicMock(properties=None)))
    assert await auth_repository.generate_magic_link(client_fail, "user@example.com") is None


@pytest.mark.asyncio
async def test_generate_magiclink_and_exchange_for_session(monkeypatch):
    """Magic link exchange verifies OTP and returns session payload."""
    props = MagicMock(hashed_token="hash-123")
    admin_client = _mock_client(generate_link=AsyncMock(return_value=MagicMock(properties=props)))
    verify_response = MagicMock(session=MagicMock(access_token="access"))
    fresh_client = MagicMock()
    fresh_client.auth.verify_otp = AsyncMock(return_value=verify_response)

    monkeypatch.setattr(
        auth_repository,
        "create_async_client",
        AsyncMock(return_value=fresh_client),
    )

    result = await auth_repository.generate_magiclink_and_exchange_for_session(
        admin_client=admin_client,
        email="user@example.com",
    )
    assert result.session.access_token == "access"


@pytest.mark.asyncio
async def test_sign_up_supabase_user_success_and_failure():
    """sign_up_supabase_user returns auth response or raises when user missing."""
    user = _mock_user(email="user@example.com")
    client = _mock_client()
    client.auth.sign_up = AsyncMock(return_value=MagicMock(user=user))

    body = SignupRequest(
        email="user@example.com",
        password="Secret1!",
        first_name="Jane",
        verification_id="verify-1",
        verification_code="123456",
    )
    result = await auth_repository.sign_up_supabase_user(body, client)
    assert result.user is user

    client.auth.sign_up = AsyncMock(return_value=MagicMock(user=None))
    with pytest.raises(BadRequestException):
        await auth_repository.sign_up_supabase_user(body, client)


@pytest.mark.asyncio
async def test_login_and_refresh_helpers():
    """login and refresh helpers delegate to Supabase auth client."""
    auth_result = MagicMock(session=MagicMock(access_token="access"))
    client = _mock_client()
    client.auth.sign_in_with_password = AsyncMock(return_value=auth_result)
    client.auth.refresh_session = AsyncMock(return_value=auth_result)

    assert await auth_repository.login_user("user@example.com", "Secret1!", client) is auth_result
    assert (
        await auth_repository.login_user_with_phone("+911234567890", "Secret1!", client)
        is auth_result
    )
    assert await auth_repository.refresh_session("refresh-token", client) is auth_result


@pytest.mark.asyncio
async def test_send_password_reset_email():
    """send_password_reset_email forwards redirect URL to Supabase."""
    client = _mock_client()
    client.auth.reset_password_email = AsyncMock(return_value={"ok": True})
    result = await auth_repository.send_password_reset_email("user@example.com", client)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_update_password_by_user_id():
    """update_password_by_user_id uses admin update_user_by_id."""
    client = _mock_client(update_user_by_id=AsyncMock(return_value=MagicMock(user=_mock_user())))
    result = await auth_repository.update_password_by_user_id("user-1", "NewSecret1!", client)
    assert result.user.model_dump()["id"] == "user-1"


@pytest.mark.asyncio
async def test_create_user_empty_response():
    """create_user returns empty dict when Supabase user missing."""
    client = _mock_client(create_user=AsyncMock(return_value=MagicMock(user=None)))
    result = await auth_repository.create_user(client, email="user@example.com")
    assert result == {}


@pytest.mark.asyncio
async def test_get_user_by_id_empty_response():
    """get_user_by_id returns empty dict when user missing."""
    client = _mock_client(get_user_by_id=AsyncMock(return_value=MagicMock(user=None)))
    result = await auth_repository.get_user_by_id(client, "user-1")
    assert result == {}


@pytest.mark.asyncio
async def test_list_users_empty_response():
    """list_users returns empty list when no users."""
    client = _mock_client(list_users=AsyncMock(return_value=MagicMock(users=None)))
    assert await auth_repository.list_users(client) == []


@pytest.mark.asyncio
async def test_ban_unban_update_failures():
    """ban/unban/update helpers return False when user missing."""
    client = _mock_client(update_user_by_id=AsyncMock(return_value=MagicMock(user=None)))
    assert await auth_repository.ban_user(client, "user-1") is False
    assert await auth_repository.unban_user(client, "user-1") is False
    assert await auth_repository.update_email(client, "user-1", "a@example.com") is False
    assert await auth_repository.update_metadata(client, "user-1", {}) is False
    assert await auth_repository.update_phone(client, "user-1", {}, "9876543210", "+91") is False


@pytest.mark.asyncio
async def test_update_password_with_link_identity_existing_email_provider():
    """When email provider exists, only password is updated."""
    existing_user = MagicMock()
    existing_user.app_metadata = {"providers": ["google", "email"]}
    existing_user.user_metadata = {"first_name": "Jane"}
    updated = MagicMock()
    updated.user = existing_user
    client = _mock_client(
        get_user_by_id=AsyncMock(return_value=MagicMock(user=existing_user)),
        update_user_by_id=AsyncMock(return_value=updated),
    )

    result = await auth_repository.update_password_with_link_identity(client, "user-1", "Secret1!")

    assert result is existing_user
    attrs = client.auth.admin.update_user_by_id.await_args.args[1]
    assert attrs == {"password": "Secret1!"}


@pytest.mark.asyncio
async def test_update_password_with_link_identity_none_result():
    """Returns None when update response has no user."""
    existing_user = MagicMock()
    existing_user.app_metadata = {"providers": ["email"]}
    existing_user.user_metadata = {}
    client = _mock_client(
        get_user_by_id=AsyncMock(return_value=MagicMock(user=existing_user)),
        update_user_by_id=AsyncMock(return_value=MagicMock(user=None)),
    )
    assert (
        await auth_repository.update_password_with_link_identity(client, "user-1", "Secret1!")
        is None
    )


@pytest.mark.asyncio
async def test_generate_magiclink_exchange_failures(monkeypatch):
    """Magic link exchange raises when token or session missing."""
    client = _mock_client(generate_link=AsyncMock(return_value=MagicMock(properties=None)))
    with pytest.raises(BadRequestException):
        await auth_repository.generate_magiclink_and_exchange_for_session(
            admin_client=client,
            email="user@example.com",
        )

    props = MagicMock(hashed_token="hash-123")
    admin_client = _mock_client(generate_link=AsyncMock(return_value=MagicMock(properties=props)))
    fresh_client = MagicMock()
    fresh_client.auth.verify_otp = AsyncMock(return_value=MagicMock(session=None))
    monkeypatch.setattr(
        auth_repository,
        "create_async_client",
        AsyncMock(return_value=fresh_client),
    )
    with pytest.raises(BadRequestException):
        await auth_repository.generate_magiclink_and_exchange_for_session(
            admin_client=admin_client,
            email="user@example.com",
        )


@pytest.mark.asyncio
async def test_generate_magic_link_no_action_link():
    """generate_magic_link returns None when action link absent."""
    client = _mock_client(
        generate_link=AsyncMock(return_value=MagicMock(properties=MagicMock(action_link=None)))
    )
    assert await auth_repository.generate_magic_link(client, "user@example.com") is None
