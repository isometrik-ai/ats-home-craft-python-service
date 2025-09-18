# pylint: disable=all

import uuid
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.admin_management.users.users import router as users_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(users_router, prefix="/v1/admin")

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


def test_users_list_success(client):
    with patch("apps.user_service.app.api.admin_management.users.users.get_users_details_list", AsyncMock(return_value=[
        {"id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "email": "u@example.com", "full_name": "User", "first_name": "User", "last_name": "One", "phone": None, "role_id": str(uuid.uuid4()), "role_name": "Admin", "status": "active", "joined_at": None, "last_active_at": None}
    ])), patch("apps.user_service.app.api.admin_management.users.users.get_users_total_count", AsyncMock(return_value=1)):
        res = client.get("/v1/admin/users/list")
        assert res.status_code == 200
        data = res.json()
        assert data["total_count"] == 1


# def test_get_user_profile_success(client):
#     user_id = str(uuid.uuid4())
#     now = datetime.now(timezone.utc)
#     with patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
#         "id": str(uuid.uuid4()),
#         "user_id": user_id,
#         "email": "u@example.com",
#         "full_name": "User",
#         "first_name": "User",
#         "last_name": "One",
#         "role_id": str(uuid.uuid4()),
#         "role_name": "Admin",
#         "status": "active",
#         "organization_id": str(uuid.uuid4()),
#         "avatar_url": None,
#         "phone": None,
#         "timezone": "UTC",
#         "joined_at": now,
#         "last_active_at": now
#     })):
#         res = client.get(f"/v1/admin/users/{user_id}")
#         assert res.status_code == 200
#         assert res.json()["user"]["user_id"] == user_id


def test_invite_user_success(client):
    payload = {"email": "new@example.com", "full_name": "New User", "role_id": str(uuid.uuid4())}
    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.invite_user_with_email", AsyncMock(return_value={"user_id": "new-user-id"})), \
         patch("apps.user_service.app.api.admin_management.users.users.create_new_user", AsyncMock(return_value={"id": "new-user-id"})):
        res = client.post("/v1/admin/users/invite", json=payload)
        assert res.status_code == 201
        assert "Invite sent successfully" in res.json()["message"]


# def test_update_user_success(client):
#     user_id = str(uuid.uuid4())
#     payload = {"full_name": "Updated"}
#     now = datetime.now(timezone.utc)
#     full_profile = {
#         "user_id": user_id,
#         "email": "u@example.com",
#         "full_name": "Old Name",
#         "first_name": "Old",
#         "last_name": "Name",
#         "role_id": str(uuid.uuid4()),
#         "role_name": "Admin",
#         "status": "active",
#         "organization_id": str(uuid.uuid4()),
#         "avatar_url": None,
#         "phone": None,
#         "timezone": "UTC",
#         "joined_at": now,
#         "last_active_at": now
#     }
#     with patch("apps.user_service.app.dependencies.common_utils.get_user_in_organization", AsyncMock(return_value=full_profile)), \
#          patch("apps.user_service.app.api.admin_management.users.users.update_user_info", AsyncMock(return_value={"id": user_id, "full_name": payload["full_name"]})), \
#          patch("apps.user_service.app.dependencies.user_utils.transform_users", AsyncMock(return_value=[full_profile])):
#         res = client.put(f"/v1/admin/users/update/{user_id}", json=payload)
#         assert res.status_code == 200
#         assert "User updated successfully" in res.json()["message"]

