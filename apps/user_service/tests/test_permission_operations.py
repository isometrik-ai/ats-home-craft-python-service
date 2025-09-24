# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from libs.shared_db.postgres_db.user_service_operations.permission_operations import (
    create_new_permission,
    get_permission_details_by_id,
    get_all_permissions
)
from apps.user_service.app.schemas.admin_access_management import CreatePermissionRequest


class TestPermissionOperations:
    """Test cases for permission_operations.py module."""

    # ============================================================================
    # PERMISSION CRUD OPERATIONS
    # ============================================================================

    @pytest.mark.asyncio
    async def test_create_new_permission_success(self):
        """Test successful permission creation."""
        organization_id = str(uuid.uuid4())
        permission_data = CreatePermissionRequest(
            name="Test Permission",
            code="test.permission",
            category="test",
            description="Test permission description"
        )

        mock_created_permission = {
            "id": str(uuid.uuid4()),
            "name": permission_data.name,
            "code": permission_data.code,
            "category": permission_data.category,
            "description": permission_data.description,
            "organization_id": organization_id,
            "created_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_permission]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_permission(permission_data, organization_id)

            assert result == mock_created_permission
            mock_supabase.table.assert_called_once_with("permissions")

    @pytest.mark.asyncio
    async def test_create_new_permission_no_data_returned(self):
        """Test permission creation when no data is returned."""
        organization_id = str(uuid.uuid4())
        permission_data = CreatePermissionRequest(
            name="Test Permission",
            code="test.permission",
            category="test",
            description="Test permission description"
        )

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_permission(permission_data, organization_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_create_new_permission_with_minimal_data(self):
        """Test permission creation with minimal required data."""
        organization_id = str(uuid.uuid4())
        permission_data = CreatePermissionRequest(
            name="Minimal Permission",
            code="minimal.permission",
            category="minimal",
            description=""
        )

        mock_created_permission = {
            "id": str(uuid.uuid4()),
            "name": permission_data.name,
            "code": permission_data.code,
            "category": permission_data.category,
            "description": permission_data.description,
            "organization_id": organization_id,
            "created_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_permission]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_permission(permission_data, organization_id)

            assert result == mock_created_permission
            assert result["description"] == ""

    @pytest.mark.asyncio
    async def test_get_permission_details_by_id_success(self):
        """Test successful permission retrieval by ID."""
        permission_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_permission = {
            "id": permission_id,
            "name": "Test Permission",
            "code": "test.permission",
            "category": "test",
            "description": "Test permission description",
            "organization_id": organization_id,
            "created_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_permission]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_permission_details_by_id(permission_id, organization_id)

            assert result == mock_permission
            mock_supabase.table.assert_called_once_with("permissions")

    @pytest.mark.asyncio
    async def test_get_permission_details_by_id_not_found(self):
        """Test permission retrieval when permission not found."""
        permission_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_permission_details_by_id(permission_id, organization_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_permission_details_by_id_with_role_count(self):
        """Test permission retrieval with role count information."""
        permission_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_permission = {
            "id": permission_id,
            "name": "Test Permission",
            "code": "test.permission",
            "category": "test",
            "description": "Test permission description",
            "organization_id": organization_id,
            "created_at": "2024-01-01T00:00:00Z",
            "role_count": 3
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_permission]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_permission_details_by_id(permission_id, organization_id)

            assert result == mock_permission
            assert result["role_count"] == 3

    @pytest.mark.asyncio
    async def test_get_all_permissions_success(self):
        """Test successful retrieval of all permissions."""
        organization_id = str(uuid.uuid4())

        mock_permissions = [
            {
                "id": str(uuid.uuid4()),
                "name": "Read Users",
                "code": "users.read",
                "category": "users",
                "description": "Read users permission",
                "organization_id": organization_id,
                "created_at": "2024-01-01T00:00:00Z"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Write Users",
                "code": "users.write",
                "category": "users",
                "description": "Write users permission",
                "organization_id": organization_id,
                "created_at": "2024-01-02T00:00:00Z"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Admin Access",
                "code": "admin.access",
                "category": "admin",
                "description": "Admin access permission",
                "organization_id": organization_id,
                "created_at": "2024-01-03T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_permissions
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_all_permissions(organization_id)

            assert len(result) == 3
            assert result == mock_permissions
            mock_supabase.table.assert_called_once_with("permissions")

    @pytest.mark.asyncio
    async def test_get_all_permissions_empty(self):
        """Test retrieval of all permissions when no permissions found."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_all_permissions(organization_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_all_permissions_with_categories(self):
        """Test retrieval of all permissions with different categories."""
        organization_id = str(uuid.uuid4())

        mock_permissions = [
            {
                "id": str(uuid.uuid4()),
                "name": "Reports View",
                "code": "reports.view",
                "category": "reports",
                "description": "View reports",
                "organization_id": organization_id,
                "created_at": "2024-01-03T00:00:00Z"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "System Admin",
                "code": "system.admin",
                "category": "system",
                "description": "System administration",
                "organization_id": organization_id,
                "created_at": "2024-01-02T00:00:00Z"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "User Management",
                "code": "users.manage",
                "category": "users",
                "description": "Manage users",
                "organization_id": organization_id,
                "created_at": "2024-01-01T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_permissions
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_all_permissions(organization_id)

            assert len(result) == 3
            # Check that permissions are sorted by category and name
            categories = [perm["category"] for perm in result]
            assert categories == ["reports", "system", "users"]

    @pytest.mark.asyncio
    async def test_get_all_permissions_with_role_counts(self):
        """Test retrieval of all permissions with role count information."""
        organization_id = str(uuid.uuid4())

        mock_permissions = [
            {
                "id": str(uuid.uuid4()),
                "name": "Read Users",
                "code": "users.read",
                "category": "users",
                "description": "Read users permission",
                "organization_id": organization_id,
                "created_at": "2024-01-01T00:00:00Z",
                "role_count": 5
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Write Users",
                "code": "users.write",
                "category": "users",
                "description": "Write users permission",
                "organization_id": organization_id,
                "created_at": "2024-01-02T00:00:00Z",
                "role_count": 2
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_permissions
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_all_permissions(organization_id)

            assert len(result) == 2
            assert result[0]["role_count"] == 5
            assert result[1]["role_count"] == 2

    @pytest.mark.asyncio
    async def test_create_new_permission_with_special_characters(self):
        """Test permission creation with special characters in data."""
        organization_id = str(uuid.uuid4())
        permission_data = CreatePermissionRequest(
            name="Special & Permission",
            code="special.permission@test",
            category="special-category",
            description="Permission with special chars: @#$%^&*()"
        )

        mock_created_permission = {
            "id": str(uuid.uuid4()),
            "name": permission_data.name,
            "code": permission_data.code,
            "category": permission_data.category,
            "description": permission_data.description,
            "organization_id": organization_id,
            "created_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_permission]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_permission(permission_data, organization_id)

            assert result == mock_created_permission
            assert result["name"] == "Special & Permission"
            assert result["code"] == "special.permission@test"
            assert result["category"] == "special-category"

    @pytest.mark.asyncio
    async def test_get_permission_details_by_id_with_long_description(self):
        """Test permission retrieval with long description."""
        permission_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        long_description = "This is a very long description that contains multiple sentences. " * 10

        mock_permission = {
            "id": permission_id,
            "name": "Test Permission",
            "code": "test.permission",
            "category": "test",
            "description": long_description,
            "organization_id": organization_id,
            "created_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_permission]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_permission_details_by_id(permission_id, organization_id)

            assert result == mock_permission
            assert len(result["description"]) > 100

    @pytest.mark.asyncio
    async def test_get_all_permissions_with_unicode_characters(self):
        """Test retrieval of all permissions with unicode characters."""
        organization_id = str(uuid.uuid4())

        mock_permissions = [
            {
                "id": str(uuid.uuid4()),
                "name": "用户管理",
                "code": "users.manage",
                "category": "用户",
                "description": "管理用户权限",
                "organization_id": organization_id,
                "created_at": "2024-01-01T00:00:00Z"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "システム管理",
                "code": "system.admin",
                "category": "システム",
                "description": "システム管理権限",
                "organization_id": organization_id,
                "created_at": "2024-01-02T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.permission_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_permissions
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_all_permissions(organization_id)

            assert len(result) == 2
            assert result[0]["name"] == "用户管理"
            assert result[1]["name"] == "システム管理"
