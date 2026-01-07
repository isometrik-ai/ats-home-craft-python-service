"""Unit tests for SessionService."""

import datetime

import pytest

from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.services.session_service import SessionService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import BadRequestException


class _FakeSessionRepo:
    """Lightweight fake repository for sessions."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}

    async def get_sessions_with_count(self, organization_id, user_id, filters):
        """Return fake user session data."""
        self.calls["get_sessions_with_count"] = (organization_id, user_id, filters)
        return {
            "data": [
                {
                    "id": "s1",
                    "user_id": user_id,
                    "organization_id": organization_id,
                    "ip_address": "1.1.1.1",
                    "user_agent": "agent",
                    "device_fingerprint": "df",
                    "risk_score": 1,
                    "login_timestamp": datetime.datetime(2024, 1, 1),
                    "logout_timestamp": datetime.datetime(2024, 1, 2),
                    "session_status": "active",
                    "login_method": "pwd",
                    "accessed_phi": False,
                    "phi_access_purpose": None,
                }
            ],
            "total_count": 1,
        }

    async def get_org_sessions_with_count(self, organization_id, filters):
        """Return fake org session data."""
        self.calls["get_org_sessions_with_count"] = (organization_id, filters)
        return {
            "data": [
                {
                    "id": "s2",
                    "user_id": "u2",
                    "organization_id": organization_id,
                    "ip_address": "2.2.2.2",
                    "user_agent": "agent2",
                    "device_fingerprint": "df2",
                    "risk_score": 2,
                    "login_timestamp": datetime.datetime(2024, 2, 1),
                    "logout_timestamp": None,
                    "session_status": "revoked",
                    "login_method": "otp",
                    "accessed_phi": True,
                    "phi_access_purpose": "care",
                }
            ],
            "total_count": 1,
        }


def _ctx(org_id="org-1", user_id="u1"):
    """Reusable user context."""
    return UserContext(
        user_id=user_id,
        email="user@example.com",
        organization_id=org_id,
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_get_user_sessions_formats(monkeypatch):
    """Formats sessions and forwards filters."""

    fake_repo = _FakeSessionRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.session_service.SessionRepository",
        lambda db_connection=None: fake_repo,
    )

    service = SessionService(user_context=_ctx(), db_connection=None)
    filters = SessionFilter(page=1, page_size=10)

    result = await service.get_user_sessions(filters)

    assert result["total_count"] == 1
    session = result["sessions"][0]
    assert session.id == "s1"
    assert session.login_timestamp.startswith("2024")
    assert fake_repo.calls["get_sessions_with_count"][2] == filters


@pytest.mark.asyncio
async def test_get_organization_sessions_requires_org(monkeypatch):
    """Raises when organization_id missing."""

    fake_repo = _FakeSessionRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.session_service.SessionRepository",
        lambda db_connection=None: fake_repo,
    )

    service = SessionService(user_context=_ctx(org_id=None), db_connection=None)
    filters = SessionFilter(page=1, page_size=10)

    with pytest.raises(BadRequestException):
        await service.get_organization_sessions(filters)


@pytest.mark.asyncio
async def test_get_organization_sessions_formats(monkeypatch):
    """Formats org sessions and forwards filters."""

    fake_repo = _FakeSessionRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.session_service.SessionRepository",
        lambda db_connection=None: fake_repo,
    )

    service = SessionService(user_context=_ctx(org_id="org-9"), db_connection=None)
    filters = SessionFilter(page=1, page_size=5)

    result = await service.get_organization_sessions(filters)

    assert result["total_count"] == 1
    session = result["sessions"][0]
    assert session.organization_id == "org-9"
    assert session.logout_timestamp == ""
    assert fake_repo.calls["get_org_sessions_with_count"][1] == filters
