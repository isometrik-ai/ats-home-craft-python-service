# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
    get_user_permissions,
    create_new_user,
    update_user_info,
    delete_user,
    check_user_exists,
    check_phone_exists_for_other_user,
    get_users_details_list,
    get_users_total_count,
    update_user_activity,
    suspend_user,
    revoke_suspended_user,
    update_user_email,
    get_auth_user_by_email,
    get_organization_member_status_by_email,
    transform_users
)


class TestUserOperations:
    """Test cases for user_operations.py module."""

    @pytest.mark.asyncio
    async def test_get_user_profile_by_id_success(self):
        """Test successful user profile retrieval by ID."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_profile = {
            "id": "profile_id",
            "user_id": user_id,
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": "https://example.com/avatar.jpg",
            "phone": "+1234567890",
            "timezone": "UTC",
            "role_id": str(uuid.uuid4()),
            "status": "active",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "last_active_at": "2024-01-01T00:00:00Z",
            "joined_at": "2024-01-01T00:00:00Z",
            "organization_id": organization_id,
            "roles": {
                "id": str(uuid.uuid4()),
                "name": "Admin",
                "description": "Administrator role"
            }
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_profile]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_user_profile_by_id(user_id, organization_id)

            assert result == mock_profile
            mock_supabase.table.assert_called_once_with("organization_members")

    @pytest.mark.asyncio
    async def test_get_user_profile_by_id_not_found(self):
        """Test user profile retrieval when user not found."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_user_profile_by_id(user_id, organization_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_user_permissions_success(self):
        """Test successful user permissions retrieval."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_permissions = [
            {"id": "perm1", "name": "read_users", "code": "users.read", "category": "users", "description": "Read users"},
            {"id": "perm2", "name": "write_users", "code": "users.write", "category": "users", "description": "Write users"}
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock user role query
            mock_user_result = MagicMock()
            mock_user_result.data = [{"role_id": "role123"}]

            # Mock permissions query
            mock_permissions_result = MagicMock()
            mock_permissions_result.data = [
                {"permissions": mock_permissions[0]},
                {"permissions": mock_permissions[1]}
            ]

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(side_effect=[mock_user_result, mock_permissions_result])

            mock_get_client.return_value = mock_supabase

            result = await get_user_permissions(user_id, organization_id)

            assert len(result) == 2
            assert result[0] == mock_permissions[0]
            assert result[1] == mock_permissions[1]

    @pytest.mark.asyncio
    async def test_get_user_permissions_no_user(self):
        """Test user permissions retrieval when user doesn't exist."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_user_result = MagicMock()
            mock_user_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_user_result)
            mock_get_client.return_value = mock_supabase

            result = await get_user_permissions(user_id, organization_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_create_new_user_success(self):
        """Test successful user creation."""
        user_data = {
            "user_id": str(uuid.uuid4()),
            "email": "new@example.com",
            "full_name": "New User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "role_id": str(uuid.uuid4()),
            "status": "active",
            "organization_id": str(uuid.uuid4())
        }

        mock_created_user = {
            "id": "member_id",
            "user_id": user_data["user_id"],
            "email": user_data["email"],
            "full_name": user_data["full_name"],
            "phone": user_data["phone"],
            "timezone": user_data["timezone"],
            "role_id": user_data["role_id"],
            "status": user_data["status"],
            "organization_id": user_data["organization_id"]
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_user]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_user(user_data)

            assert result == mock_created_user
            mock_supabase.table.assert_called_once_with("organization_members")

    @pytest.mark.asyncio
    async def test_create_new_user_no_data_returned(self):
        """Test user creation when no data is returned."""
        user_data = {
            "user_id": str(uuid.uuid4()),
            "email": "new@example.com",
            "full_name": "New User",
            "organization_id": str(uuid.uuid4())
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_user(user_data)

            assert result == {}

    @pytest.mark.asyncio
    async def test_update_user_info_success(self):
        """Test successful user info update."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {
            "full_name": "Updated Name",
            "phone": "+9876543210",
            "timezone": "EST"
        }

        mock_updated_user = {
            "id": "member_id",
            "user_id": user_id,
            "email": "test@example.com",
            "full_name": "Updated Name",
            "phone": "+9876543210",
            "timezone": "EST",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_updated_user]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_user_info(user_id, organization_id, update_data)

            assert result == mock_updated_user

    @pytest.mark.asyncio
    async def test_update_user_info_no_fields_to_update(self):
        """Test user info update with no fields to update."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {}

        result = await update_user_info(user_id, organization_id, update_data)

        assert result == {}

    @pytest.mark.asyncio
    async def test_update_user_info_none_values(self):
        """Test user info update with None values."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {
            "full_name": "Updated Name",
            "phone": None,
            "timezone": None
        }

        mock_updated_user = {
            "id": "member_id",
            "user_id": user_id,
            "email": "test@example.com",
            "full_name": "Updated Name",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_updated_user]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_user_info(user_id, organization_id, update_data)

            assert result == mock_updated_user

    @pytest.mark.asyncio
    async def test_delete_user_success(self):
        """Test successful user deletion."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "deleted_id"}]
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await delete_user(user_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_user_no_data_returned(self):
        """Test user deletion when no data is returned."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await delete_user(user_id, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_user_exists_true(self):
        """Test user existence check when user exists."""
        email = "test@example.com"
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "user_id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_user_exists(email, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_user_exists_false(self):
        """Test user existence check when user doesn't exist."""
        email = "test@example.com"
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_user_exists(email, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_phone_exists_for_other_user_true(self):
        """Test phone existence check when phone exists for another user."""
        phone = "+1234567890"
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "other_user_id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_phone_exists_for_other_user(phone, organization_id, user_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_phone_exists_for_other_user_false(self):
        """Test phone existence check when phone doesn't exist for another user."""
        phone = "+1234567890"
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_phone_exists_for_other_user(phone, organization_id, user_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_phone_exists_for_other_user_no_user_id(self):
        """Test phone existence check without excluding user ID."""
        phone = "+1234567890"
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "user_id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_phone_exists_for_other_user(phone, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_get_users_details_list_success(self):
        """Test successful users list retrieval."""
        organization_id = str(uuid.uuid4())

        mock_users = [
            {
                "id": "user1",
                "user_id": str(uuid.uuid4()),
                "email": "user1@example.com",
                "full_name": "User 1",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "last_active_at": "2024-01-01T00:00:00Z"
            },
            {
                "id": "user2",
                "user_id": str(uuid.uuid4()),
                "email": "user2@example.com",
                "full_name": "User 2",
                "phone": "+9876543210",
                "timezone": "EST",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "last_active_at": "2024-01-02T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_users
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_users_details_list(organization_id, limit=20, offset=0)

            assert len(result) == 2
            assert result == mock_users

    @pytest.mark.asyncio
    async def test_get_users_details_list_with_search(self):
        """Test users list retrieval with search filter."""
        organization_id = str(uuid.uuid4())
        search = "test"

        mock_users = [
            {
                "id": "user1",
                "user_id": str(uuid.uuid4()),
                "email": "test@example.com",
                "full_name": "Test User",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "last_active_at": "2024-01-01T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_users
            mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_users_details_list(organization_id, search=search, limit=20, offset=0)

            assert len(result) == 1
            assert result == mock_users

    @pytest.mark.asyncio
    async def test_get_users_details_list_empty(self):
        """Test users list retrieval when no users found."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_users_details_list(organization_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_users_total_count_success(self):
        """Test successful users total count retrieval."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 25
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_users_total_count(organization_id)

            assert result == 25

    @pytest.mark.asyncio
    async def test_get_users_total_count_with_search(self):
        """Test users total count retrieval with search filter."""
        organization_id = str(uuid.uuid4())
        search = "test"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 5
            mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_users_total_count(organization_id, search=search)

            assert result == 5

    @pytest.mark.asyncio
    async def test_get_users_total_count_none(self):
        """Test users total count retrieval when count is None."""
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = None
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_users_total_count(organization_id)

            assert result == 0

    @pytest.mark.asyncio
    async def test_update_user_activity_success(self):
        """Test successful user activity update."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated_id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            await update_user_activity(user_id, organization_id)

            mock_supabase.table.assert_called_once_with("organization_members")

    @pytest.mark.asyncio
    async def test_suspend_user_success(self):
        """Test successful user suspension."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated_id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await suspend_user(user_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_suspend_user_no_data(self):
        """Test user suspension when no data is returned."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = None
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await suspend_user(user_id, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_revoke_suspended_user_success(self):
        """Test successful suspended user revocation."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated_id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await revoke_suspended_user(user_id, organization_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_revoke_suspended_user_no_data(self):
        """Test suspended user revocation when no data is returned."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = None
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await revoke_suspended_user(user_id, organization_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_update_user_email_success(self):
        """Test successful user email update."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        new_email = "newemail@example.com"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated_id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_user_email(user_id, organization_id, new_email)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_user_email_no_data(self):
        """Test user email update when no data is returned."""
        user_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        new_email = "newemail@example.com"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_user_email(user_id, organization_id, new_email)

            assert result is False

    @pytest.mark.asyncio
    async def test_get_auth_user_by_email_success(self):
        """Test successful auth user retrieval by email."""
        email = "test@example.com"

        mock_auth_user = MagicMock()
        mock_auth_user.email = email
        mock_auth_user.id = "auth_user_id"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_auth_user])
            mock_get_client.return_value = mock_supabase

            result = await get_auth_user_by_email(email)

            assert result == mock_auth_user

    @pytest.mark.asyncio
    async def test_get_auth_user_by_email_not_found(self):
        """Test auth user retrieval when user not found."""
        email = "test@example.com"

        mock_auth_user = MagicMock()
        mock_auth_user.email = "other@example.com"
        mock_auth_user.id = "auth_user_id"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_auth_user])
            mock_get_client.return_value = mock_supabase

            result = await get_auth_user_by_email(email)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_organization_member_status_by_email_success(self):
        """Test successful organization member status retrieval by email."""
        email = "test@example.com"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"status": "active"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organization_member_status_by_email(email)

            assert result == "active"

    @pytest.mark.asyncio
    async def test_get_organization_member_status_by_email_not_found(self):
        """Test organization member status retrieval when user not found."""
        email = "test@example.com"

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organization_member_status_by_email(email)

            assert result is None

    @pytest.mark.asyncio
    async def test_transform_users_success(self):
        """Test successful user data transformation."""
        organization_id = str(uuid.uuid4())

        users_data = [
            {
                "user_id": str(uuid.uuid4()),
                "email": "test@example.com",
                "full_name": "Test User",
                "first_name": "Test",
                "last_name": "User",
                "phone": "+1234567890",
                "role_name": "Admin",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "joined_at": datetime.now().isoformat(),
                "last_active_at": datetime.now().isoformat()
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 5
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await transform_users(users_data, organization_id)

            assert len(result) == 1
            assert result[0].user_id == users_data[0]["user_id"]
            assert result[0].email == users_data[0]["email"]
            assert result[0].full_name == users_data[0]["full_name"]
            assert result[0].permissions_count == 5

    @pytest.mark.asyncio
    async def test_transform_users_empty_data(self):
        """Test user data transformation with empty data."""
        organization_id = str(uuid.uuid4())
        users_data = []

        result = await transform_users(users_data, organization_id)

        assert result == []

    @pytest.mark.asyncio
    async def test_transform_users_no_permissions_count(self):
        """Test user data transformation when permissions count is None."""
        organization_id = str(uuid.uuid4())

        users_data = [
            {
                "user_id": str(uuid.uuid4()),
                "email": "test@example.com",
                "full_name": "Test User",
                "first_name": "Test",
                "last_name": "User",
                "phone": "+1234567890",
                "role_name": "Admin",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "joined_at": datetime.now().isoformat(),
                "last_active_at": datetime.now().isoformat()
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = None
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await transform_users(users_data, organization_id)

            assert len(result) == 1
            assert result[0].permissions_count == 0

    @pytest.mark.asyncio
    async def test_transform_users_no_joined_at(self):
        """Test user data transformation when joined_at is None."""
        organization_id = str(uuid.uuid4())

        users_data = [
            {
                "user_id": str(uuid.uuid4()),
                "email": "test@example.com",
                "full_name": "Test User",
                "first_name": "Test",
                "last_name": "User",
                "phone": "+1234567890",
                "role_name": "Admin",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "joined_at": None,
                "last_active_at": datetime.now().isoformat()
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.user_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 0
            mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await transform_users(users_data, organization_id)

            assert len(result) == 1
            assert result[0].joined_at is not None  # Should be set to current datetime
