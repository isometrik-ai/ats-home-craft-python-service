# pylint: disable=all

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.admin_management.sessions.sessions import router as sessions_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(sessions_router, prefix="/v1/admin")
    app.dependency_overrides[get_user_from_auth] = lambda: {"user_id": "u", "organization_id": "o", "email": "e@e.com"}
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_sessions_list_success(client):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    later = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_list", AsyncMock(return_value=[
        {
            "id": "s",
            "user_id": "u",
            "organization_id": "o",
            "ip_address": "127.0.0.1",
            "user_agent": "agent",
            "device_fingerprint": None,
            "risk_score": 0,
            "login_timestamp": now,
            "logout_timestamp": later,
            "session_status": "active",
            "login_method": "password",
            "accessed_phi": False,
            "phi_access_purpose": None,
        }
    ])), patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_count", AsyncMock(return_value=1)):
        res = client.get("/v1/admin/sessions")
        assert res.status_code == 200
        assert res.json()["total_count"] == 1


