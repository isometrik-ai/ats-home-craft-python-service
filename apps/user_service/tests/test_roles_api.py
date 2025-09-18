# pylint: disable=all

import uuid
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.admin_management.roles import router as roles_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(roles_router, prefix="/v1/admin")

    app.dependency_overrides[get_user_from_auth] = lambda: {
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "email": "test@example.com",
    }
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_list_roles_success(client):
    # Patch role operations used by the endpoint
    with patch("apps.user_service.app.api.admin_management.roles.get_roles_list", AsyncMock(return_value=[
        {"id": str(uuid.uuid4()), "name": "Admin", "description": "", "is_default": True, "created_at": "", "updated_at": "", "user_count": 0, "permission_count": 0, "permission_categories": "{}"},
    ])), patch("apps.user_service.app.api.admin_management.roles.get_roles_count", AsyncMock(return_value=1)):
        res = client.get("/v1/admin/roles")
        assert res.status_code == 200
        body = res.json()
        assert body["total_count"] == 1
        assert body["roles"][0]["name"] == "Admin"


def test_get_role_by_id_success(client):
    role_id = str(uuid.uuid4())
    with patch("apps.user_service.app.api.admin_management.roles.get_role_by_id", AsyncMock(return_value={
        "id": role_id, "name": "Editor", "description": "", "is_default": False, "created_at": "", "updated_at": ""
    })), patch("apps.user_service.app.api.admin_management.roles.get_role_permissions", AsyncMock(return_value=[])):
        res = client.get(f"/v1/admin/roles/{role_id}")
        assert res.status_code == 200
        assert res.json()["role"]["name"] == "Editor"


def test_create_role_success(client):
    payload = {"name": "Reviewer", "description": "", "role_type": "custom", "permission_ids": []}
    with patch("apps.user_service.app.api.admin_management.roles.check_role_name_unique", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.roles.create_role", AsyncMock(return_value={
             "id": str(uuid.uuid4()), "name": payload["name"], "description": payload["description"], "is_default": False, "created_at": "", "updated_at": ""
         })), \
         patch("apps.user_service.app.api.admin_management.roles.assign_permissions_to_role", AsyncMock(return_value=True)):
        res = client.post("/v1/admin/roles", json=payload)
        assert res.status_code == 201
        assert "Role created successfully" in res.json()["message"]


def test_update_role_success(client):
    role_id = str(uuid.uuid4())
    payload = {"name": "Renamed", "description": "Updated"}
    with patch("apps.user_service.app.api.admin_management.roles.check_role_exists", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.roles.check_role_name_unique", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.roles.get_role_by_id", AsyncMock(return_value={"id": role_id, "name": "Old", "description": "", "is_default": False})), \
         patch("apps.user_service.app.api.admin_management.roles.update_role", AsyncMock(return_value={
             "id": role_id, "name": payload["name"], "description": payload["description"], "is_default": False, "created_at": "", "updated_at": ""
         })), \
         patch("apps.user_service.app.api.admin_management.roles.assign_permissions_to_role", AsyncMock(return_value=True)):
        res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
        assert res.status_code == 200
        assert "Role updated successfully" in res.json()["message"]


