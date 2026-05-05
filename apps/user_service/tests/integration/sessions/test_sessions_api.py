"""Integration tests for sessions endpoints."""

import pytest

from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_get_sessions_list(monkeypatch, client):
    """List sessions for current user/org."""

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="member"
        )

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="admin",
            email="admin@example.com",
            organization_id="org-1",
            user_type="admin",
        )

    async def fake_get_user_sessions(self, filters: SessionFilter):
        del self, filters
        return {
            "sessions": [
                {
                    "id": "s1",
                    "user_id": "u1",
                    "user_email": "u1@example.com",
                    "user_name": "User One",
                }
            ],
            "total_count": 1,
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.sessions.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.sessions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.session_service.SessionService.get_user_sessions",
        fake_get_user_sessions,
    )

    res = await client.get("/v1/sessions?page=1&page_size=10")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "s1"
    assert body["data"][0]["user_email"] == "u1@example.com"
    assert body["data"][0]["user_name"] == "User One"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_organization_sessions(monkeypatch, client):
    """List sessions across organization."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="admin", email="admin@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get_org_sessions(self, filters: SessionFilter):
        del self, filters
        return {
            "sessions": [
                {
                    "id": "s2",
                    "user_id": "u2",
                    "user_email": "u2@example.com",
                    "user_name": "User Two",
                }
            ],
            "total_count": 1,
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.sessions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.session_service.SessionService.get_organization_sessions",
        fake_get_org_sessions,
    )

    res = await client.get("/v1/sessions/all?page=1&page_size=10")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "s2"
    assert body["data"][0]["user_email"] == "u2@example.com"
    assert body["data"][0]["user_name"] == "User Two"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_revoke_session(monkeypatch, client):
    """Revoke a specific session."""

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="admin",
            email="admin@example.com",
            organization_id="org-1",
            user_type="admin",
        )

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="admin",
            email="admin@example.com",
            organization_id="org-1",
            user_type="admin",
        )

    async def fake_require_organization_creator(user_context, organization_id, db_connection):
        del user_context, organization_id, db_connection
        return None

    async def fake_get_session_org_id(self, session_id: str):
        del self
        assert session_id == "sess-1"
        return "org-1"

    async def fake_revoke_session_by_id(self, **kwargs):
        del self
        assert kwargs["session_id"] == "sess-1"

    monkeypatch.setattr(
        "apps.user_service.app.api.sessions.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.sessions.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.sessions.require_organization_creator",
        fake_require_organization_creator,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.db.repositories.session_repository.SessionRepository"
            ".get_session_organization_id"
        ),
        fake_get_session_org_id,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.session_service.SessionService.revoke_session_by_id",
        fake_revoke_session_by_id,
    )

    res = await client.delete(
        "/v1/sessions/sess-1",
    )
    body = assert_success(res, 200)
    assert body["data"]["revoked"] is True


@pytest.mark.asyncio
async def test_missing_session_returns_unauthorized(monkeypatch):
    """If session row is missing, auth dependency must raise 401."""
    import importlib

    from starlette.requests import Request

    from apps.user_service.tests.conftest import FakeConn
    from libs.shared_middleware import jwt_auth
    from libs.shared_utils.http_exceptions import UnauthorizedException

    # conftest patches `jwt_auth.get_user_from_auth`; reload module to test real implementation
    jwt_auth = importlib.reload(jwt_auth)

    async def fake_get_valid_session_context(self, session_id: str):
        del self
        assert session_id == "test-session-id"
        return None

    monkeypatch.setattr(
        (
            "apps.user_service.app.db.repositories.session_repository.SessionRepository"
            ".get_valid_session_context"
        ),
        fake_get_valid_session_context,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/sessions",
            "headers": [],
            "query_string": b"",
        }
    )
    request.state.user = {
        "sub": "test-user-id",
        "email": "test@example.com",
        "session_id": "test-session-id",
    }

    with pytest.raises(UnauthorizedException):
        await jwt_auth.get_user_from_auth(request, db_connection=FakeConn())
