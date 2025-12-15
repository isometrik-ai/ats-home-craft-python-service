"""Test cases for roles API endpoints.

Tests the roles API endpoints in apps/user_service/app/api/admin_management/roles.py
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Create FastAPI app with roles router for testing."""
    from fastapi import FastAPI

    from apps.user_service.app.api.admin_management.roles import router as roles_router
    from apps.user_service.app.dependencies.common_utils import (
        UserContext,
        check_permissions,
        check_user_access_async,
    )
    from libs.shared_middleware.jwt_auth import get_user_from_auth

    app = FastAPI()
    app.include_router(roles_router, prefix="/v1/admin")

    app.dependency_overrides[get_user_from_auth] = lambda: {
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "email": "test@example.com",
    }
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    app.dependency_overrides[check_permissions] = AsyncMock(
        return_value=UserContext(
            user_id=str(uuid.uuid4()),
            email="test@example.com",
            organization_id=str(uuid.uuid4()),
        )
    )
    return app


@pytest.fixture
def client(app):
    """Create test client for the app."""
    return TestClient(app)


class TestGetRoles:
    """Test cases for GET /v1/admin/roles endpoint."""

    def test_list_roles_success(self, client):
        """Test successful roles list retrieval."""
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_list",
                AsyncMock(
                    return_value=[
                        {
                            "id": str(uuid.uuid4()),
                            "name": "Admin",
                            "description": "",
                            "is_default": True,
                            "created_at": "",
                            "updated_at": "",
                            "user_count": 0,
                            "permission_count": 0,
                            "permission_categories": "{}",
                        },
                    ]
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_count",
                AsyncMock(return_value=1),
            ),
        ):
            res = client.get("/v1/admin/roles")
            assert res.status_code == 200
            body = res.json()
            assert body["total_count"] == 1
            assert body["roles"][0]["name"] == "Admin"

    def test_list_roles_with_filters(self, client):
        """Test roles list with query parameters."""
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_list",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_count",
                AsyncMock(return_value=0),
            ),
        ):
            res = client.get("/v1/admin/roles?search=admin&role_type=custom&skip=0&limit=10")
            assert res.status_code == 200
            body = res.json()
            assert body["total_count"] == 0
            assert body["roles"] == []

    def test_list_roles_invalid_role_type(self, client):
        """Test roles list with invalid role type (API doesn't validate, so returns 200)."""
        res = client.get("/v1/admin/roles?role_type=invalid")
        assert res.status_code == 200

    def test_list_roles_database_error(self, client):
        """Test roles list with database error."""
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_list",
                AsyncMock(side_effect=Exception("Database error")),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_count",
                AsyncMock(side_effect=Exception("Database error")),
            ),
        ):
            # Since there's no error handling decorator, the exception will be raised directly
            with pytest.raises(Exception, match="Database error"):
                client.get("/v1/admin/roles")


class TestGetRoleById:
    """Test cases for GET /v1/admin/roles/{role_id} endpoint."""

    def test_get_role_by_id_success(self, client):
        """Test successful role retrieval by ID."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "Editor",
                        "description": "",
                        "is_default": False,
                        "created_at": "",
                        "updated_at": "",
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_permissions",
                AsyncMock(return_value=[]),
            ),
        ):
            res = client.get(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 200
            assert res.json()["role"]["name"] == "Editor"

    def test_get_role_by_id_invalid_uuid(self, client):
        """Test role retrieval with invalid UUID."""
        res = client.get("/v1/admin/roles/invalid-uuid")
        assert res.status_code == 400
        assert "Invalid role ID format" in res.json()["detail"]

    def test_get_role_by_id_not_found(self, client):
        """Test role retrieval when role doesn't exist."""
        role_id = str(uuid.uuid4())
        with patch(
            "apps.user_service.app.api.admin_management.roles.get_role_by_id",
            AsyncMock(return_value=None),
        ):
            res = client.get(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 404
            assert "Role not found" in res.json()["detail"]

    def test_get_role_by_id_database_error(self, client):
        """Test role retrieval with database error."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(side_effect=Exception("Database error")),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_permissions",
                AsyncMock(side_effect=Exception("Database error")),
            ),
        ):
            # Since there's no error handling decorator, the exception will be raised directly
            with pytest.raises(Exception, match="Database error"):
                client.get(f"/v1/admin/roles/{role_id}")


class TestCreateRole:
    """Test cases for POST /v1/admin/roles endpoint."""

    def test_create_role_success(self, client):
        """Test successful role creation."""
        payload = {
            "name": "Reviewer",
            "description": "",
            "role_type": "custom",
            "permission_ids": [],
        }
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_name_unique",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.create_role",
                AsyncMock(
                    return_value={
                        "id": str(uuid.uuid4()),
                        "name": payload["name"],
                        "description": payload["description"],
                        "is_default": False,
                        "created_at": "",
                        "updated_at": "",
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.assign_permissions_to_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.post("/v1/admin/roles", json=payload)
            assert res.status_code == 201
            assert "Role created successfully" in res.json()["message"]

    def test_create_role_duplicate_name(self, client):
        """Test role creation with duplicate name."""
        payload = {
            "name": "Admin",
            "description": "",
            "role_type": "custom",
            "permission_ids": [],
        }
        with patch(
            "apps.user_service.app.api.admin_management.roles.check_role_name_unique",
            AsyncMock(return_value=False),
        ):
            res = client.post("/v1/admin/roles", json=payload)
            assert res.status_code == 400
            assert "Role name already exists" in res.json()["detail"]

    def test_create_role_invalid_data(self, client):
        """Test role creation with invalid data."""
        payload = {
            "name": "",
            "description": "",
            "role_type": "invalid",
            "permission_ids": [],
        }
        res = client.post("/v1/admin/roles", json=payload)
        assert res.status_code == 422

    def test_create_role_missing_required_fields(self, client):
        """Test role creation with missing required fields."""
        payload = {"description": "Test role"}
        res = client.post("/v1/admin/roles", json=payload)
        assert res.status_code == 422

    def test_create_role_database_error(self, client):
        """Test role creation with database error."""
        payload = {
            "name": "TestRole",
            "description": "",
            "role_type": "custom",
            "permission_ids": [],
        }
        with patch(
            (
                "apps.user_service.app.api.admin_management.roles."
                "check_permission_exist_in_organization"
            ),
            AsyncMock(side_effect=Exception("Database error")),
        ):
            # Since there's no error handling decorator, the exception will be raised directly
            with pytest.raises(Exception, match="Database error"):
                client.post("/v1/admin/roles", json=payload)

    def test_create_role_invalid_role_type(self, client):
        """Test role creation with invalid role type (role_type field is ignored by API)."""
        payload = {
            "name": "TestRole",
            "description": "",
            "role_type": "invalid_type",
            "permission_ids": [],
        }

        with patch(
            "apps.user_service.app.api.admin_management.roles.create_role",
            AsyncMock(
                return_value={
                    "id": str(uuid.uuid4()),
                    "created_at": "2023-01-01T00:00:00Z",
                }
            ),
        ):
            res = client.post("/v1/admin/roles", json=payload)
            # The API ignores the role_type field since it's not part of the schema
            assert res.status_code == 201

    def test_create_role_invalid_permission_ids(self, client):
        """Test role creation with invalid permission IDs."""
        payload = {
            "name": "TestRole",
            "description": "",
            "role_type": "custom",
            "permission_ids": ["invalid-uuid"],
        }
        res = client.post("/v1/admin/roles", json=payload)
        assert res.status_code == 400
        assert "Invalid permission ID format" in res.json()["detail"]

    def test_create_role_permissions_not_exist(self, client):
        """Test role creation with non-existent permission IDs."""
        payload = {
            "name": "TestRole",
            "description": "",
            "role_type": "custom",
            "permission_ids": [str(uuid.uuid4())],
        }
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.validate_uuid_format",
                MagicMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions_exist",
                AsyncMock(return_value=False),
            ),
        ):
            res = client.post("/v1/admin/roles", json=payload)
            assert res.status_code == 400
            assert "One or more permission IDs are invalid" in res.json()["detail"]


class TestUpdateRole:
    """Test cases for PUT /v1/admin/roles/{role_id} endpoint."""

    def test_update_role_success(self, client):
        """Test successful role update."""
        role_id = str(uuid.uuid4())
        payload = {"name": "Renamed", "description": "Updated"}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_name_unique",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "Old",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.update_role",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": payload["name"],
                        "description": payload["description"],
                        "is_default": False,
                        "created_at": "",
                        "updated_at": "",
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.assign_permissions_to_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 200
            assert "Role updated successfully" in res.json()["message"]

    def test_update_role_invalid_uuid(self, client):
        """Test role update with invalid UUID."""
        payload = {"name": "Updated", "description": "Updated"}
        res = client.put("/v1/admin/roles/invalid-uuid", json=payload)
        assert res.status_code == 400
        assert "Invalid role_ID format" in res.json()["detail"]

    def test_update_role_not_found(self, client):
        """Test role update when role doesn't exist."""
        role_id = str(uuid.uuid4())
        payload = {"name": "Updated", "description": "Updated"}
        with patch(
            "apps.user_service.app.api.admin_management.roles.check_role_exists",
            AsyncMock(return_value=False),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 404
            assert "Role not found" in res.json()["detail"]

    def test_update_role_duplicate_name(self, client):
        """Test role update with duplicate name."""
        role_id = str(uuid.uuid4())
        payload = {"name": "Admin", "description": "Updated"}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "Old",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_name_unique",
                AsyncMock(return_value=False),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 400
            assert "Role name already exists" in res.json()["detail"]

    def test_update_role_invalid_data(self, client):
        """Test role update with invalid data."""
        role_id = str(uuid.uuid4())
        payload = {"name": "", "description": "Updated"}
        res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
        assert res.status_code == 422

    def test_update_role_database_error(self, client):
        """Test role update with database error."""
        role_id = str(uuid.uuid4())
        payload = {"name": "Updated", "description": "Updated"}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(side_effect=Exception("Database error")),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(side_effect=Exception("Database error")),
            ),
        ):
            # Since there's no error handling decorator, the exception will be raised directly
            with pytest.raises(Exception, match="Database error"):
                client.put(f"/v1/admin/roles/{role_id}", json=payload)

    def test_update_role_no_changes(self, client):
        """Test role update with no changes."""
        role_id = str(uuid.uuid4())
        payload = {}  # Empty payload - no changes
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 200
            assert "No changes were made to the role" in res.json()["message"]

    def test_update_role_with_permissions(self, client):
        """Test role update with permission changes."""
        role_id = str(uuid.uuid4())
        permission_id = str(uuid.uuid4())
        payload = {"permission_ids": [permission_id]}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.validate_uuid_format",
                MagicMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions_exist",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.assign_permissions_to_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 200
            assert "Role updated successfully" in res.json()["message"]

    def test_update_role_remove_all_permissions(self, client):
        """Test role update removing all permissions."""
        role_id = str(uuid.uuid4())
        payload = {"permission_ids": []}  # Empty permissions list
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.assign_permissions_to_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 200
            assert "permissions (removed all)" in res.json()["message"]

    def test_update_role_with_is_default(self, client):
        """Test role update with is_default field."""
        role_id = str(uuid.uuid4())
        payload = {"is_default": True}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.update_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 200
            assert "Role updated successfully" in res.json()["message"]

    def test_update_role_invalid_permission_ids(self, client):
        """Test role update with invalid permission IDs."""
        role_id = str(uuid.uuid4())
        payload = {"permission_ids": ["invalid-uuid"]}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 400
            assert "Invalid permission ID format" in res.json()["detail"]

    def test_update_role_permissions_not_exist(self, client):
        """Test role update with non-existent permission IDs."""
        role_id = str(uuid.uuid4())
        permission_id = str(uuid.uuid4())
        payload = {"permission_ids": [permission_id]}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.validate_uuid_format",
                MagicMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions_exist",
                AsyncMock(return_value=False),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 400
            assert "One or more permission IDs are invalid" in res.json()["detail"]


class TestDeleteRole:
    """Test cases for DELETE /v1/admin/roles/{role_id} endpoint."""

    def test_delete_role_success(self, client):
        """Test successful role deletion."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_usage",
                AsyncMock(return_value=0),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.delete_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.delete(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 204

    def test_delete_role_invalid_uuid(self, client):
        """Test role deletion with invalid UUID."""
        res = client.delete("/v1/admin/roles/invalid-uuid")
        assert res.status_code == 400
        assert "Invalid role ID format" in res.json()["detail"]

    def test_delete_role_not_found(self, client):
        """Test role deletion when role doesn't exist."""
        role_id = str(uuid.uuid4())
        with patch(
            "apps.user_service.app.api.admin_management.roles.check_role_exists",
            AsyncMock(return_value=False),
        ):
            res = client.delete(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 404
            assert "Role not found" in res.json()["detail"]

    def test_delete_role_default_role(self, client):
        """Test deletion of default role (currently allowed by API)."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "Admin",
                        "description": "",
                        "is_default": True,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_usage",
                AsyncMock(return_value=0),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_permissions",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.delete_role",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.delete(f"/v1/admin/roles/{role_id}")
            # The API currently allows deletion of default roles
            assert res.status_code == 204

    def test_delete_role_database_error(self, client):
        """Test role deletion with database error."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(side_effect=Exception("Database error")),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(side_effect=Exception("Database error")),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_usage",
                AsyncMock(side_effect=Exception("Database error")),
            ),
        ):
            # Since there's no error handling decorator, the exception will be raised directly
            with pytest.raises(Exception, match="Database error"):
                client.delete(f"/v1/admin/roles/{role_id}")

    def test_delete_role_in_use(self, client):
        """Test role deletion when role is in use by members."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_usage",
                AsyncMock(return_value=3),
            ),
        ):  # 3 members using this role
            res = client.delete(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 409
            assert (
                "Cannot delete role. It is currently assigned to 3 organization member(s)"
                in res.json()["detail"]
            )

    def test_delete_role_not_found_after_check(self, client):
        """Test role deletion when role is not found after initial check."""
        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_usage",
                AsyncMock(return_value=0),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.delete_role",
                AsyncMock(return_value=False),
            ),
        ):  # Role not found during deletion
            res = client.delete(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 404
            assert "Role not found or already deleted" in res.json()["detail"]


class TestPermissionDenied:
    """Test cases for permission denied scenarios."""

    @pytest.fixture
    def app_without_permission_override(self):
        """Create app without permission override for testing permission denied scenarios."""
        from fastapi import FastAPI

        from apps.user_service.app.api.admin_management.roles import (
            router as roles_router,
        )
        from apps.user_service.app.dependencies.common_utils import (
            check_user_access_async,
        )
        from libs.shared_middleware.jwt_auth import get_user_from_auth

        app = FastAPI()
        app.include_router(roles_router, prefix="/v1/admin")

        app.dependency_overrides[get_user_from_auth] = lambda: {
            "user_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
        }
        app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
        # No check_permissions override - let it use the real function
        return app

    def test_get_roles_permission_denied(self, app_without_permission_override):
        """Test roles list with insufficient permissions."""
        client = TestClient(app_without_permission_override)

        # Patch at the module level where it's imported
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions",
                AsyncMock(
                    side_effect=HTTPException(status_code=403, detail="Insufficient permissions")
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_list",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_roles_count",
                AsyncMock(return_value=0),
            ),
        ):
            res = client.get("/v1/admin/roles")
            assert res.status_code == 403
            assert "Insufficient permissions" in res.json()["detail"]

    def test_create_role_permission_denied(self, app_without_permission_override):
        """Test role creation with insufficient permissions."""
        client = TestClient(app_without_permission_override)

        payload = {
            "name": "TestRole",
            "description": "",
            "role_type": "custom",
            "permission_ids": [],
        }
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions",
                AsyncMock(
                    side_effect=HTTPException(status_code=403, detail="Insufficient permissions")
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions_exist",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_name_unique",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.create_role",
                AsyncMock(return_value={"id": str(uuid.uuid4()), "created_at": datetime.now()}),
            ),
        ):
            res = client.post("/v1/admin/roles", json=payload)
            assert res.status_code == 403
            assert "Insufficient permissions" in res.json()["detail"]

    def test_update_role_permission_denied(self, app_without_permission_override):
        """Test role update with insufficient permissions."""
        client = TestClient(app_without_permission_override)

        role_id = str(uuid.uuid4())
        payload = {"name": "Updated", "description": "Updated"}
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions",
                AsyncMock(
                    side_effect=HTTPException(status_code=403, detail="Insufficient permissions")
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "Old",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_name_unique",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.put(f"/v1/admin/roles/{role_id}", json=payload)
            assert res.status_code == 403
            assert "Insufficient permissions" in res.json()["detail"]

    def test_delete_role_permission_denied(self, app_without_permission_override):
        """Test role deletion with insufficient permissions."""
        client = TestClient(app_without_permission_override)

        role_id = str(uuid.uuid4())
        with (
            patch(
                "apps.user_service.app.api.admin_management.roles.check_permissions",
                AsyncMock(
                    side_effect=HTTPException(status_code=403, detail="Insufficient permissions")
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_exists",
                AsyncMock(return_value=True),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.get_role_by_id",
                AsyncMock(
                    return_value={
                        "id": role_id,
                        "name": "TestRole",
                        "description": "",
                        "is_default": False,
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.roles.check_role_usage",
                AsyncMock(return_value=0),
            ),
        ):
            res = client.delete(f"/v1/admin/roles/{role_id}")
            assert res.status_code == 403
            assert "Insufficient permissions" in res.json()["detail"]


# Note: RoleResponse class does not exist in the roles module
# These tests have been removed as they reference a non-existent class
