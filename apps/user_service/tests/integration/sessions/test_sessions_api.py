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
        return None

    async def fake_get_user_sessions(self, filters: SessionFilter):
        del self, filters
        return {"sessions": [{"id": "s1", "user_id": "u1"}], "total_count": 1}

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
        return {"sessions": [{"id": "s2", "user_id": "u2"}], "total_count": 1}

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
    assert body["total"] == 1
