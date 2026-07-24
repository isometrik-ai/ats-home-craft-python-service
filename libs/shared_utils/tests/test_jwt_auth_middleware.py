"""Unit tests for JWT auth middleware helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from starlette.responses import Response
from supabase import AuthError

from libs.shared_middleware.jwt_auth import (
    JWTAuthMiddleware,
    check_user_access_async,
    extract_user_data,
    get_claims_from_token,
    get_user_from_auth,
    get_user_from_auth_db,
    get_user_from_auth_redis,
    get_user_from_token,
    setup_audit_context,
)
from libs.shared_utils.http_exceptions import (
    InternalServerErrorException,
    UnauthorizedException,
)


def test_extract_user_data_returns_fields() -> None:
    """extract_user_data should read sub, email, and session_id."""
    user = {
        "sub": "user-1",
        "email": "user@example.com",
        "session_id": "sess-1",
    }
    user_id, email, session_id = extract_user_data(user)
    assert user_id == "user-1"
    assert email == "user@example.com"
    assert session_id == "sess-1"


def test_extract_user_data_empty_user() -> None:
    """extract_user_data should return Nones for empty input."""
    assert extract_user_data({}) == (None, None, None)


def test_setup_audit_context_sets_request_state() -> None:
    """setup_audit_context should populate audit fields on request.state."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    setup_audit_context(
        request,
        user_id="user-1",
        user_email="user@example.com",
        organization_id="org-1",
        session_id="sess-1",
    )
    assert request.state.audit_risk_level == "high"
    assert request.state.audit_user_context["organization_id"] == "org-1"


@pytest.mark.asyncio
async def test_get_claims_from_token_success() -> None:
    """get_claims_from_token returns claims payload from Supabase."""
    mock_client = AsyncMock()
    mock_client.auth.get_claims = AsyncMock(
        return_value={"claims": {"sub": "user-1", "email": "u@example.com"}}
    )
    claims = await get_claims_from_token("token-123", supabase_client=mock_client)
    assert claims["sub"] == "user-1"


@pytest.mark.asyncio
async def test_get_claims_from_token_missing_claims() -> None:
    """get_claims_from_token raises when claims payload is empty."""
    mock_client = AsyncMock()
    mock_client.auth.get_claims = AsyncMock(return_value={"claims": None})
    with pytest.raises(UnauthorizedException):
        await get_claims_from_token("bad-token", supabase_client=mock_client)


@pytest.mark.asyncio
async def test_get_claims_from_token_expired_auth_error() -> None:
    """Expired Supabase auth errors map to token_expired."""
    mock_client = AsyncMock()
    auth_error = AuthError("JWT expired", "invalid_jwt")
    auth_error.status = 401
    mock_client.auth.get_claims = AsyncMock(side_effect=auth_error)
    with pytest.raises(UnauthorizedException) as exc_info:
        await get_claims_from_token("expired", supabase_client=mock_client)
    assert exc_info.value.message_key == "errors.token_expired"


@pytest.mark.asyncio
async def test_check_user_access_async_grants_permission() -> None:
    """check_user_access_async returns True when user has all permissions."""
    db_connection = AsyncMock()
    db_connection.fetchrow = AsyncMock(
        side_effect=[
            {"user_permissions": ["perm.a", "perm.b"]},
            {"has_all_permissions": True},
        ]
    )
    result = await check_user_access_async(
        permission_code=["perm.a"],
        user_id="user-1",
        organization_id="org-1",
        db_connection=db_connection,
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_user_access_async_denies_missing_inputs() -> None:
    """check_user_access_async returns False for missing identifiers."""
    db_connection = AsyncMock()
    assert (
        await check_user_access_async(
            permission_code=["perm.a"],
            user_id=None,
            organization_id="org-1",
            db_connection=db_connection,
        )
        is False
    )


@pytest.mark.asyncio
async def test_jwt_middleware_skips_options() -> None:
    """JWTAuthMiddleware should bypass auth for OPTIONS requests."""
    middleware = JWTAuthMiddleware(app=MagicMock())
    request = Request({"type": "http", "method": "OPTIONS", "path": "/", "headers": []})

    async def call_next(_request):
        return Response(status_code=204)

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_jwt_middleware_sets_user_on_valid_token() -> None:
    """JWTAuthMiddleware should attach decoded user to request.state."""
    middleware = JWTAuthMiddleware(app=MagicMock())
    headers = [(b"authorization", b"Bearer valid-token")]
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": headers})

    async def call_next(req):
        assert req.state.user["sub"] == "user-1"
        return Response(status_code=200)

    with patch(
        "libs.shared_middleware.jwt_auth.get_claims_from_token",
        AsyncMock(return_value={"sub": "user-1", "email": "u@example.com"}),
    ):
        response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_jwt_middleware_returns_401_on_invalid_token() -> None:
    """JWTAuthMiddleware should return error response for invalid tokens."""
    middleware = JWTAuthMiddleware(app=MagicMock())
    headers = [(b"authorization", b"Bearer bad-token")]
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": headers})

    async def call_next(_request):
        return Response(status_code=200)

    with patch(
        "libs.shared_middleware.jwt_auth.get_claims_from_token",
        AsyncMock(side_effect=UnauthorizedException(message_key="errors.invalid_token")),
    ):
        response = await middleware.dispatch(request, call_next)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_claims_from_token_fetches_client_when_missing() -> None:
    """get_claims_from_token lazily loads Supabase client."""
    mock_client = AsyncMock()
    mock_client.auth.get_claims = AsyncMock(return_value={"claims": {"sub": "u1"}})
    with patch(
        "libs.shared_middleware.jwt_auth.get_supabase_client",
        AsyncMock(return_value=mock_client),
    ) as get_client:
        claims = await get_claims_from_token("token")
    get_client.assert_awaited_once()
    assert claims["sub"] == "u1"


@pytest.mark.asyncio
async def test_get_claims_from_token_invalid_jwt_status_400() -> None:
    """Invalid JWT (400) maps to invalid_token."""
    mock_client = AsyncMock()
    auth_error = AuthError("bad jwt", "invalid_jwt")
    auth_error.status = 400
    mock_client.auth.get_claims = AsyncMock(side_effect=auth_error)
    with pytest.raises(UnauthorizedException) as exc_info:
        await get_claims_from_token("bad", supabase_client=mock_client)
    assert exc_info.value.message_key == "errors.invalid_token"


@pytest.mark.asyncio
async def test_get_claims_from_token_generic_auth_error() -> None:
    """Unhandled auth errors map to authentication_failed."""
    mock_client = AsyncMock()
    auth_error = AuthError("other", "other")
    auth_error.status = 403
    mock_client.auth.get_claims = AsyncMock(side_effect=auth_error)
    with pytest.raises(UnauthorizedException) as exc_info:
        await get_claims_from_token("bad", supabase_client=mock_client)
    assert exc_info.value.message_key == "errors.authentication_failed"


@pytest.mark.asyncio
async def test_get_claims_from_token_unexpected_exception() -> None:
    """Unexpected exceptions map to authentication_failed."""
    mock_client = AsyncMock()
    mock_client.auth.get_claims = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(UnauthorizedException) as exc_info:
        await get_claims_from_token("bad", supabase_client=mock_client)
    assert exc_info.value.message_key == "errors.authentication_failed"


@pytest.mark.asyncio
async def test_check_user_access_empty_permissions() -> None:
    """Empty permission list returns False."""
    db_connection = AsyncMock()
    assert (
        await check_user_access_async(
            permission_code=[],
            user_id="user-1",
            organization_id="org-1",
            db_connection=db_connection,
        )
        is False
    )


@pytest.mark.asyncio
async def test_check_user_access_no_permissions_row() -> None:
    """Missing permissions array returns False."""
    db_connection = AsyncMock()
    db_connection.fetchrow = AsyncMock(return_value={"user_permissions": None})
    assert (
        await check_user_access_async(
            permission_code=["perm.a"],
            user_id="user-1",
            organization_id="org-1",
            db_connection=db_connection,
        )
        is False
    )


@pytest.mark.asyncio
async def test_check_user_access_internal_error() -> None:
    """Database failures raise InternalServerErrorException."""
    db_connection = AsyncMock()
    db_connection.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(InternalServerErrorException):
        await check_user_access_async(
            permission_code=["perm.a"],
            user_id="user-1",
            organization_id="org-1",
            db_connection=db_connection,
        )


@pytest.mark.asyncio
async def test_get_user_from_auth_redis_no_user() -> None:
    """Redis auth helper returns None when request has no JWT user."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert await get_user_from_auth_redis(request) is None


@pytest.mark.asyncio
async def test_get_user_from_auth_redis_blocked() -> None:
    """Redis auth helper returns None when session is blocked."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "session_id": "s1"}
    with patch(
        "libs.shared_middleware.jwt_auth.resolve_session_context_from_redis",
        AsyncMock(return_value=(True, None)),
    ):
        assert await get_user_from_auth_redis(request) is None


@pytest.mark.asyncio
async def test_get_user_from_auth_db_missing_session() -> None:
    """DB auth helper raises when session context is missing."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "session_id": "s1"}
    with (
        patch(
            "libs.shared_middleware.jwt_auth.coalesced_resolve_session_context_from_db",
            AsyncMock(return_value=None),
        ),
        pytest.raises(UnauthorizedException),
    ):
        await get_user_from_auth_db(request)


@pytest.mark.asyncio
async def test_get_user_from_auth_redis_hit() -> None:
    """get_user_from_auth returns user when Redis resolves session."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "email": "u@example.com", "session_id": "s1"}
    session_ctx = {"organization_id": "org-1"}
    with patch(
        "libs.shared_middleware.jwt_auth.resolve_session_context_from_redis",
        AsyncMock(return_value=(False, session_ctx)),
    ):
        user = await get_user_from_auth(request, redis_client=MagicMock())
    assert user["_session_context"] == session_ctx
    assert request.state.audit_risk_level == "low"


@pytest.mark.asyncio
async def test_get_user_from_auth_db_fallback() -> None:
    """get_user_from_auth falls back to DB when Redis misses."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "email": "u@example.com", "session_id": "s1"}
    session_ctx = {"organization_id": "org-1"}
    with (
        patch(
            "libs.shared_middleware.jwt_auth.resolve_session_context_from_redis",
            AsyncMock(return_value=(False, None)),
        ),
        patch(
            "libs.shared_middleware.jwt_auth.coalesced_resolve_session_context_from_db",
            AsyncMock(return_value=session_ctx),
        ),
    ):
        user = await get_user_from_auth(request, redis_client=MagicMock())
    assert user["_session_context"] == session_ctx


@pytest.mark.asyncio
async def test_get_user_from_token_delegates() -> None:
    """get_user_from_token delegates to get_claims_from_token."""
    with patch(
        "libs.shared_middleware.jwt_auth.get_claims_from_token",
        AsyncMock(return_value={"sub": "u1"}),
    ) as get_claims:
        result = await get_user_from_token("tok")
    get_claims.assert_awaited_once_with("tok")
    assert result["sub"] == "u1"


@pytest.mark.asyncio
async def test_jwt_middleware_skips_missing_bearer() -> None:
    """Requests without Bearer header bypass middleware auth."""
    middleware = JWTAuthMiddleware(app=MagicMock())
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    async def call_next(_request):
        return Response(status_code=200)

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_jwt_middleware_generic_exception() -> None:
    """Unexpected middleware errors return authentication_failed."""
    middleware = JWTAuthMiddleware(app=MagicMock())
    headers = [(b"authorization", b"Bearer token")]
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": headers})

    async def call_next(_request):
        return Response(status_code=200)

    with patch(
        "libs.shared_middleware.jwt_auth.get_claims_from_token",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await middleware.dispatch(request, call_next)
    assert response.status_code == 401
