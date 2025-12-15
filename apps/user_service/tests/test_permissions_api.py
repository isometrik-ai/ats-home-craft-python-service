"""Test cases for permissions API endpoints.

Tests the permissions API endpoints in apps/user_service/app/api/admin_management/permissions
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from libs.shared_utils.common_query import SETTINGS_ROLES_MANAGE, SETTINGS_USERS_VIEW


@pytest.fixture
def app():
    """Create FastAPI app with permissions router for testing."""
    from fastapi import FastAPI

    from apps.user_service.app.api.admin_management.permissions import (
        router as permissions_router,
    )
    from apps.user_service.app.dependencies.common_utils import check_permissions
    from libs.shared_middleware.jwt_auth import get_user_from_auth

    app = FastAPI()
    app.include_router(permissions_router, prefix="/v1/admin")

    # Mock user context for all tests
    mock_user_context = MagicMock()
    mock_user_context.user_id = str(uuid.uuid4())
    mock_user_context.organization_id = str(uuid.uuid4())
    mock_user_context.email = "test@example.com"
    mock_user_context.user_type = "organization_member"

    app.dependency_overrides[get_user_from_auth] = lambda: {
        "user_id": mock_user_context.user_id,
        "organization_id": mock_user_context.organization_id,
        "email": mock_user_context.email,
    }
    app.dependency_overrides[check_permissions] = lambda *a, **k: mock_user_context
    return app


@pytest.fixture
def client(app):
    """Create test client for the FastAPI app."""
    return TestClient(app)


class TestGetPermissions:
    """Test cases for GET /permissions endpoint."""

    def test_permissions_list_success(self, client):
        """Test successful permissions list retrieval."""
        mock_permissions = [
            {
                "id": "p1",
                "name": "Manage Roles",
                "code": SETTINGS_ROLES_MANAGE,
                "category": "settings",
                "description": "Can manage roles",
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
            },
            {
                "id": "p2",
                "name": "View Users",
                "code": SETTINGS_USERS_VIEW,
                "category": "settings",
                "description": "Can view users",
                "created_at": datetime(2025, 1, 2, tzinfo=timezone.utc).isoformat(),
            },
        ]

        with patch(
            "apps.user_service.app.api.admin_management.permissions.get_all_permissions",
            AsyncMock(return_value=mock_permissions),
        ):
            with patch(
                "apps.user_service.app.api.admin_management.permissions.format_permissions_data",
                MagicMock(return_value=mock_permissions),
            ):
                res = client.get("/v1/admin/permissions")
                assert res.status_code == 200
                data = res.json()
                assert len(data["permissions"]) == 2
                # status_code is not included in response body
                assert "message" in data

    def test_permissions_list_no_permissions_found(self, client):
        """Test permissions list when no permissions are found."""
        with patch(
            "apps.user_service.app.api.admin_management.permissions.get_all_permissions",
            AsyncMock(return_value=[]),
        ):
            res = client.get("/v1/admin/permissions")
            assert res.status_code == 404
            data = res.json()
            assert "No permissions found" in data["detail"]


class TestGetPermissionById:
    """Test cases for GET /permissions/{permission_id} endpoint."""

    def test_get_permission_by_id_success(self, client):
        """Test successful permission retrieval by ID."""
        permission_id = str(uuid.uuid4())
        mock_permission = {
            "id": permission_id,
            "name": "Manage Roles",
            "code": SETTINGS_ROLES_MANAGE,
            "category": "settings",
            "description": "Can manage roles",
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
        }

        with patch(
            "apps.user_service.app.api.admin_management.permissions.get_permission_details_by_id",
            AsyncMock(return_value=mock_permission),
        ):
            with patch(
                "apps.user_service.app.api.admin_management.permissions.format_permissions_data",
                MagicMock(return_value=[mock_permission]),
            ):
                res = client.get(f"/v1/admin/permissions/{permission_id}")
                assert res.status_code == 200
                data = res.json()
                assert len(data["permissions"]) == 1
                assert data["permissions"][0]["id"] == permission_id
                # status_code is not included in response body

    def test_get_permission_by_id_not_found(self, client):
        """Test permission retrieval when permission not found."""
        permission_id = str(uuid.uuid4())

        with patch(
            "apps.user_service.app.api.admin_management.permissions.get_permission_details_by_id",
            AsyncMock(return_value=None),
        ):
            res = client.get(f"/v1/admin/permissions/{permission_id}")
            assert res.status_code == 404
            data = res.json()
            assert "Permission not found" in data["detail"]

    def test_get_permission_by_id_invalid_uuid(self, client):
        """Test permission retrieval with invalid UUID format."""
        invalid_id = "invalid-uuid"

        res = client.get(f"/v1/admin/permissions/{invalid_id}")
        assert res.status_code == 400
        data = res.json()
        assert "Invalid permission ID format" in data["detail"]


class TestCreatePermission:
    """Test cases for POST /permissions endpoint."""

    def test_create_permission_success(self, client):
        """Test successful permission creation."""
        permission_data = {
            "name": "Create Projects",
            "code": "projects.create",
            "description": "Allows creating new projects",
            "category": "projects",
        }

        created_permission = {
            "id": str(uuid.uuid4()),
            "name": permission_data["name"],
            "code": permission_data["code"],
            "description": permission_data["description"],
            "category": permission_data["category"],
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
        }

        with patch(
            "apps.user_service.app.api.admin_management.permissions.create_new_permission",
            AsyncMock(return_value=created_permission),
        ):
            with patch(
                "apps.user_service.app.api.admin_management.permissions.format_permissions_data",
                MagicMock(return_value=[created_permission]),
            ):
                res = client.post("/v1/admin/permissions", json=permission_data)
                assert res.status_code == 201
                data = res.json()
                # status_code is not included in response body
                assert "message" in data
                assert len(data["permissions"]) == 1
                assert data["permissions"][0]["name"] == permission_data["name"]

    def test_create_permission_creation_failed(self, client):
        """Test permission creation when creation fails."""
        permission_data = {
            "name": "Create Projects",
            "code": "projects.create",
            "description": "Allows creating new projects",
            "category": "projects",
        }

        with patch(
            "apps.user_service.app.api.admin_management.permissions.create_new_permission",
            AsyncMock(return_value=None),
        ):
            res = client.post("/v1/admin/permissions", json=permission_data)
            assert res.status_code == 400
            data = res.json()
            assert "Failed to create permission" in data["detail"]

    def test_create_permission_invalid_data(self, client):
        """Test permission creation with invalid data."""
        invalid_data = {
            "name": "",  # Empty name should fail validation
            "code": "projects.create",
            "description": "Allows creating new projects",
            "category": "projects",
        }

        res = client.post("/v1/admin/permissions", json=invalid_data)
        assert res.status_code == 422  # Validation error


class TestDeletePermission:
    """Test cases for DELETE /permissions/{permission_id} endpoint."""

    def test_delete_permission_success(self, client):
        """Test successful permission deletion."""
        # Use UUID string, not int
        permission_id = str(uuid.uuid4())

        with (
            patch(
                (
                    "apps.user_service.app.api.admin_management.permissions."
                    "get_permission_details_by_id"
                ),
                AsyncMock(
                    return_value={
                        "id": permission_id,
                        "name": "Test Permission",
                        "code": "test_code",
                        "category": "test",
                        "description": "Test description",
                    }
                ),
            ),
            patch(
                "apps.user_service.app.api.admin_management.permissions.delete_permission",
                AsyncMock(return_value=True),
            ),
        ):
            res = client.delete(f"/v1/admin/permissions/{permission_id}")
            assert res.status_code == 204

    def test_delete_permission_invalid_id(self, client):
        """Test permission deletion with invalid ID format."""
        invalid_id = "invalid-id"

        res = client.delete(f"/v1/admin/permissions/{invalid_id}")
        # The endpoint expects an int, so this should return 422
        assert res.status_code == 422


class TestPermissionOperationsIntegration:
    """Integration tests for permission operations."""

    def test_permission_lifecycle(self, client):
        """Test complete permission lifecycle: create -> get -> list."""
        # 1. Create permission
        permission_data = {
            "name": "Test Permission",
            "code": "test.permission",
            "description": "Test permission for integration",
            "category": "test",
        }

        created_permission = {
            "id": str(uuid.uuid4()),
            "name": permission_data["name"],
            "code": permission_data["code"],
            "description": permission_data["description"],
            "category": permission_data["category"],
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
        }

        with patch(
            "apps.user_service.app.api.admin_management.permissions.create_new_permission",
            AsyncMock(return_value=created_permission),
        ):
            with patch(
                "apps.user_service.app.api.admin_management.permissions.format_permissions_data",
                MagicMock(return_value=[created_permission]),
            ):
                # Create permission
                create_res = client.post("/v1/admin/permissions", json=permission_data)
                assert create_res.status_code == 201

                # Get permission by ID
                with patch(
                    (
                        "apps.user_service.app.api.admin_management.permissions."
                        "get_permission_details_by_id"
                    ),
                    AsyncMock(return_value=created_permission),
                ):
                    get_res = client.get(f"/v1/admin/permissions/{created_permission['id']}")
                    assert get_res.status_code == 200
                    get_data = get_res.json()
                    assert get_data["permissions"][0]["name"] == permission_data["name"]

                # List all permissions
                with patch(
                    "apps.user_service.app.api.admin_management.permissions.get_all_permissions",
                    AsyncMock(return_value=[created_permission]),
                ):
                    list_res = client.get("/v1/admin/permissions")
                    assert list_res.status_code == 200
                    list_data = list_res.json()
                    assert len(list_data["permissions"]) == 1

    def test_permissions_with_different_categories(self, client):
        """Test permissions with different categories."""
        mock_permissions = [
            {
                "id": "p1",
                "name": "Manage Users",
                "code": "users.manage",
                "category": "users",
                "description": "Manage user accounts",
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
            },
            {
                "id": "p2",
                "name": "View Reports",
                "code": "reports.view",
                "category": "reports",
                "description": "View system reports",
                "created_at": datetime(2025, 1, 2, tzinfo=timezone.utc).isoformat(),
            },
        ]

        with patch(
            "apps.user_service.app.api.admin_management.permissions.get_all_permissions",
            AsyncMock(return_value=mock_permissions),
        ):
            with patch(
                "apps.user_service.app.api.admin_management.permissions.format_permissions_data",
                MagicMock(return_value=mock_permissions),
            ):
                res = client.get("/v1/admin/permissions")
                assert res.status_code == 200
                data = res.json()
                assert len(data["permissions"]) == 2

                # Check categories
                categories = [p["category"] for p in data["permissions"]]
                assert "users" in categories
                assert "reports" in categories
