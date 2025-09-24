# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from libs.shared_db.postgres_db.user_service_operations.role_operations import (
    create_role,
    get_role_by_id,
    update_role,
    delete_role,
    check_role_exists,
    get_roles_list,
    get_roles_count,
    assign_permissions_to_role,
    remove_permissions_from_role,
    remove_all_permissions_from_role,
    get_role_permissions,
    check_permissions_exist,
    check_role_name_unique,
    check_role_usage
)


class TestRoleOperations:
    """Test cases for role_operations.py module."""

    # ============================================================================
    # ROLE CRUD OPERATIONS
    # ============================================================================

    @pytest.mark.asyncio
    async def test_create_role_success(self):
        """Test successful role creation."""
        name = "Test Role"
        description = "Test role description"
        organization_id = str(uuid.uuid4())
        is_default = False

        mock_created_role = {
            "id": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "organization_id": organization_id,
            "is_default": is_default,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_role]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_role(name, description, organization_id, is_default)

            assert result == mock_created_role
            mock_supabase.table.assert_called_once_with("roles")

    @pytest.mark.asyncio
    async def test_create_role_default(self):
        """Test successful role creation with default flag."""
        name = "Default Role"
        description = "Default role description"
        organization_id = str(uuid.uuid4())
        is_default = True

        mock_created_role = {
            "id": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "organization_id": organization_id,
            "is_default": is_default,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_role]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_role(name, description, organization_id, is_default)

            assert result == mock_created_role
            assert result["is_default"] is True

    @pytest.mark.asyncio
    async def test_create_role_no_data_returned(self):
        """Test role creation when no data is returned."""
        name = "Test Role"
        description = "Test role description"
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_role(name, description, organization_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_role_by_id_success(self):
        """Test successful role retrieval by ID."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_role = {
            "id": role_id,
            "name": "Test Role",
            "description": "Test role description",
            "organization_id": organization_id,
            "is_default": False,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_role]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_role_by_id(role_id, organization_id)

            assert result == mock_role
            mock_supabase.table.assert_called_once_with("roles")

    @pytest.mark.asyncio
    async def test_get_role_by_id_not_found(self):
        """Test role retrieval when role not found."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_role_by_id(role_id, organization_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_update_role_success(self):
        """Test successful role update."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {
            "name": "Updated Role",
            "description": "Updated description"
        }

        mock_updated_role = {
            "id": role_id,
            "name": "Updated Role",
            "description": "Updated description",
            "organization_id": organization_id,
            "is_default": False,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_updated_role]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_role(role_id, organization_id, update_data)

            assert result == mock_updated_role

    @pytest.mark.asyncio
    async def test_update_role_no_data_returned(self):
        """Test role update when no data is returned."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {"name": "Updated Role"}

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_role(role_id, organization_id, update_data)

            assert result == {}

    @pytest.mark.asyncio
    async def test_delete_role_success(self):
        """Test successful role deletion."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "deleted_id"}]
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await delete_role(role_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_role_no_data_returned(self):
        """Test role deletion when no data is returned."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await delete_role(role_id, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_role_exists_true(self):
        """Test role existence check when role exists."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "role_id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_exists(role_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_role_exists_false(self):
        """Test role existence check when role doesn't exist."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_exists(role_id, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_get_roles_list_success(self):
        """Test successful roles list retrieval."""
        organization_id = str(uuid.uuid4())

        mock_roles = [
            {
                "id": str(uuid.uuid4()),
                "name": "Admin",
                "description": "Administrator role",
                "organization_id": organization_id,
                "is_default": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Editor",
                "description": "Editor role",
                "organization_id": organization_id,
                "is_default": False,
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock the main roles query
            mock_roles_result = MagicMock()
            mock_roles_result.data = mock_roles

            # Mock the user count queries (one for each role)
            mock_user_count_result1 = MagicMock()
            mock_user_count_result1.count = 5
            mock_user_count_result2 = MagicMock()
            mock_user_count_result2.count = 3

            # Mock the permission queries (one for each role)
            mock_permission_result1 = MagicMock()
            mock_permission_result1.data = [{"permissions": {"category": "users"}}]
            mock_permission_result2 = MagicMock()
            mock_permission_result2.data = [{"permissions": {"category": "admin"}}]

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_roles_result)

            # Mock the user count calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute = AsyncMock(side_effect=[mock_user_count_result1, mock_user_count_result2])

            # Mock the permission calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(side_effect=[mock_permission_result1, mock_permission_result2])

            mock_get_client.return_value = mock_supabase

            result = await get_roles_list(organization_id, limit=20, offset=0)

            assert len(result) == 2
            assert result[0]["user_count"] == 5
            assert result[1]["user_count"] == 3
            assert result[0]["permission_count"] == 1
            assert result[1]["permission_count"] == 1

    @pytest.mark.asyncio
    async def test_get_roles_list_with_search(self):
        """Test roles list retrieval with search filter."""
        organization_id = str(uuid.uuid4())
        search = "admin"

        mock_roles = [
            {
                "id": str(uuid.uuid4()),
                "name": "Admin",
                "description": "Administrator role",
                "organization_id": organization_id,
                "is_default": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock the main roles query
            mock_roles_result = MagicMock()
            mock_roles_result.data = mock_roles

            # Mock the user count query
            mock_user_count_result = MagicMock()
            mock_user_count_result.count = 2

            # Mock the permission query
            mock_permission_result = MagicMock()
            mock_permission_result.data = [{"permissions": {"category": "users"}}]

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.ilike.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_roles_result)
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_user_count_result)
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_permission_result)

            mock_get_client.return_value = mock_supabase

            result = await get_roles_list(organization_id, search=search, limit=20, offset=0)

            assert len(result) == 1
            assert result[0]["user_count"] == 2
            assert result[0]["permission_count"] == 1

    @pytest.mark.asyncio
    async def test_get_roles_list_empty(self):
        """Test roles list retrieval when no roles found."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_roles_list(organization_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_roles_count_success(self):
        """Test successful roles count retrieval."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 15
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_roles_count(organization_id)

            assert result == 15

    @pytest.mark.asyncio
    async def test_get_roles_count_with_search(self):
        """Test roles count retrieval with search filter."""
        organization_id = str(uuid.uuid4())
        search = "admin"

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 3
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.ilike.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_roles_count(organization_id, search=search)

            assert result == 3

    @pytest.mark.asyncio
    async def test_get_roles_count_none(self):
        """Test roles count retrieval when count is None."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = None
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_roles_count(organization_id)

            assert result == 0

    # ============================================================================
    # PERMISSION OPERATIONS
    # ============================================================================

    @pytest.mark.asyncio
    async def test_assign_permissions_to_role_success(self):
        """Test successful permission assignment to role."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        permission_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock the remove_all_permissions_from_role call
            mock_remove_result = MagicMock()
            mock_remove_result.data = [{"id": "removed_id"}]

            # Mock the insert call
            mock_insert_result = MagicMock()
            mock_insert_result.data = [{"id": "assigned_id1"}, {"id": "assigned_id2"}]

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_remove_result)
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_insert_result)

            mock_get_client.return_value = mock_supabase

            result = await assign_permissions_to_role(role_id, organization_id, permission_ids)

            assert result is True

    @pytest.mark.asyncio
    async def test_assign_permissions_to_role_no_data_returned(self):
        """Test permission assignment when no data is returned."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        permission_ids = [str(uuid.uuid4())]

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock the remove_all_permissions_from_role call
            mock_remove_result = MagicMock()
            mock_remove_result.data = [{"id": "removed_id"}]

            # Mock the insert call with no data
            mock_insert_result = MagicMock()
            mock_insert_result.data = []

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_remove_result)
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_insert_result)

            mock_get_client.return_value = mock_supabase

            result = await assign_permissions_to_role(role_id, organization_id, permission_ids)

            assert result is False

    @pytest.mark.asyncio
    async def test_remove_permissions_from_role_success(self):
        """Test successful permission removal from role."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        permission_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "removed_id1"}, {"id": "removed_id2"}]
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.in_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await remove_permissions_from_role(role_id, organization_id, permission_ids)

            assert result is True

    @pytest.mark.asyncio
    async def test_remove_permissions_from_role_no_data_returned(self):
        """Test permission removal when no data is returned."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        permission_ids = [str(uuid.uuid4())]

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.in_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await remove_permissions_from_role(role_id, organization_id, permission_ids)

            assert result is False

    @pytest.mark.asyncio
    async def test_remove_all_permissions_from_role_success(self):
        """Test successful removal of all permissions from role."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "removed_id1"}, {"id": "removed_id2"}]
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await remove_all_permissions_from_role(role_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_remove_all_permissions_from_role_no_data_returned(self):
        """Test removal of all permissions when no data is returned."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await remove_all_permissions_from_role(role_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_get_role_permissions_success(self):
        """Test successful role permissions retrieval."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        permission_id1 = str(uuid.uuid4())
        permission_id2 = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [
                {"permission_id": permission_id1},
                {"permission_id": permission_id2}
            ]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_role_permissions(role_id, organization_id)

            assert len(result) == 2
            assert result[0] == permission_id1
            assert result[1] == permission_id2

    @pytest.mark.asyncio
    async def test_get_role_permissions_empty(self):
        """Test role permissions retrieval when no permissions found."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_role_permissions(role_id, organization_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_role_permissions_with_none_permissions(self):
        """Test role permissions retrieval when permission_id field is None."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [
                {"permission_id": None},
                {"permission_id": "perm1"}
            ]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_role_permissions(role_id, organization_id)

            assert len(result) == 1
            assert result[0] == "perm1"

    # ============================================================================
    # VALIDATION OPERATIONS
    # ============================================================================

    @pytest.mark.asyncio
    async def test_check_permissions_exist_true(self):
        """Test permission existence check when permissions exist."""
        permission_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 2  # Should match the length of permission_ids
            mock_supabase.table.return_value.select.return_value.in_.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_permissions_exist(permission_ids, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_permissions_exist_false(self):
        """Test permission existence check when permissions don't exist."""
        permission_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.in_.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_permissions_exist(permission_ids, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_permissions_exist_partial(self):
        """Test permission existence check when only some permissions exist."""
        permission_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "perm1"}]  # Only one permission exists
            mock_supabase.table.return_value.select.return_value.in_.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_permissions_exist(permission_ids, organization_id)

            assert result is False  # Should be False because not all permissions exist

    @pytest.mark.asyncio
    async def test_check_role_name_unique_true(self):
        """Test role name uniqueness check when name is unique."""
        name = "Unique Role"
        organization_id = str(uuid.uuid4())
        exclude_role_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_name_unique(name, organization_id, exclude_role_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_role_name_unique_false(self):
        """Test role name uniqueness check when name is not unique."""
        name = "Existing Role"
        organization_id = str(uuid.uuid4())
        exclude_role_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "existing_role_id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_name_unique(name, organization_id, exclude_role_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_role_name_unique_no_exclude(self):
        """Test role name uniqueness check without excluding any role."""
        name = "Test Role"
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_name_unique(name, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_role_usage_success(self):
        """Test successful role usage check."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 5
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_usage(role_id, organization_id)

            assert result == 5

    @pytest.mark.asyncio
    async def test_check_role_usage_none_count(self):
        """Test role usage check when count is None."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = None
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_usage(role_id, organization_id)

            assert result == 0

    @pytest.mark.asyncio
    async def test_check_role_usage_zero(self):
        """Test role usage check when no users are using the role."""
        role_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.role_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 0
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_role_usage(role_id, organization_id)

            assert result == 0
