# pylint: disable=all

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.admin_management.permissions import router as permissions_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(permissions_router, prefix="/v1/admin")
    app.dependency_overrides[get_user_from_auth] = lambda: {"user_id": "u", "organization_id": "o", "email": "e@e.com"}
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_permissions_list_success(client):
    with patch("apps.user_service.app.api.admin_management.permissions.get_all_permissions", AsyncMock(return_value=[
        {"id": "p1", "name": "Manage Roles", "code": "settings.roles.manage", "category": "settings",
         "description": "Can manage roles", "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc)}
    ])):
        res = client.get("/v1/admin/permissions")
        assert res.status_code == 200
        data = res.json()
        assert len(data["permissions"]) == 1
