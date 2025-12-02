# pylint: disable=all
"""
Test cases for invite_operations.py module

This module tests all database operations for organization invitations.
"""

import pytest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from libs.shared_db.postgres_db.user_service_operations.invite_operations import (
    create_organization_invite,
    get_invite_by_token,
    get_invite_by_id,
    get_organization_invites,
    get_organization_invites_count,
    update_invite_status,
    delete_invite,
    check_existing_invite,
    check_user_membership,
    add_user_to_organization
)


class TestCreateOrganizationInvite:
    """Test cases for create_organization_invite function."""

    @pytest.mark.asyncio
    async def test_create_organization_invite_success(self):
        """Test successful invitation creation."""
        invite_data = {
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "role_id": str(uuid.uuid4()),
            "invited_by": str(uuid.uuid4()),
            "first_name": "Test",
            "last_name": "User",
            "phone": None,
            "salutation": None
        }

        mock_result = MagicMock()
        mock_result.data = [{
            "id": str(uuid.uuid4()),
            "organization_id": invite_data["organization_id"],
            "email": invite_data["email"],
            "role_id": invite_data["role_id"],
            "token_hash": "hashed_token",
            "status": "pending",
            "expires_at": (datetime.now() + timedelta(days=7)).isoformat(),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_table.insert.return_value = mock_insert
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await create_organization_invite(invite_data)

            assert result["id"] is not None
            assert result["organization_id"] == invite_data["organization_id"]
            assert result["email"] == invite_data["email"]
            assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_create_organization_invite_no_data_returned(self):
        """Test invitation creation when no data is returned."""
        invite_data = {
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "role_id": str(uuid.uuid4()),
            "invited_by": str(uuid.uuid4()),
            "first_name": "Test",
            "last_name": "User",
            "phone": None,
            "salutation": None
        }

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_table.insert.return_value = mock_insert
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await create_organization_invite(invite_data)

            assert result == {}

    @pytest.mark.asyncio
    async def test_create_organization_invite_default_expiration(self):
        """Test invitation creation with default expiration days."""
        invite_data = {
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "role_id": str(uuid.uuid4()),
            "invited_by": str(uuid.uuid4()),
            "first_name": "Test",
            "last_name": "User",
            "phone": None,
            "salutation": None
        }

        mock_result = MagicMock()
        mock_result.data = [{"id": str(uuid.uuid4())}]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_table.insert.return_value = mock_insert
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await create_organization_invite(invite_data)

            assert result["id"] is not None


class TestGetInviteByToken:
    """Test cases for get_invite_by_token function."""

    @pytest.mark.asyncio
    async def test_get_invite_by_token_success(self):
        """Test successful token retrieval."""
        token = "hashed_token_123"
        mock_invite_data = {
            "id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "role_id": str(uuid.uuid4()),
            "token_hash": token,
            "status": "pending",
            "organizations": {
                "name": "Test Org",
                "slug": "test-org",
                "domain": "test.com"
            }
        }

        mock_result = MagicMock()
        mock_result.data = [mock_invite_data]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_eq.limit.return_value = mock_limit
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_invite_by_token(token)

            assert result["id"] == mock_invite_data["id"]
            assert result["token_hash"] == token
            assert result["organizations"]["name"] == "Test Org"

    @pytest.mark.asyncio
    async def test_get_invite_by_token_not_found(self):
        """Test token retrieval when invitation not found."""
        token = "nonexistent_token"

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_eq.limit.return_value = mock_limit
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_invite_by_token(token)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_invite_by_token_none_data(self):
        """Test token retrieval when data is None."""
        token = "hashed_token_123"

        mock_result = MagicMock()
        mock_result.data = None

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_eq.limit.return_value = mock_limit
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_invite_by_token(token)

            assert result is None


class TestGetInviteById:
    """Test cases for get_invite_by_id function."""

    @pytest.mark.asyncio
    async def test_get_invite_by_id_success(self):
        """Test successful ID retrieval."""
        invite_id = str(uuid.uuid4())
        mock_invite_data = {
            "id": invite_id,
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "role_id": str(uuid.uuid4()),
            "status": "pending",
            "organizations": {
                "name": "Test Org",
                "slug": "test-org",
                "domain": "test.com"
            }
        }

        mock_result = MagicMock()
        mock_result.data = [mock_invite_data]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_eq.limit.return_value = mock_limit
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_invite_by_id(invite_id)

            assert result["id"] == invite_id
            assert result["organizations"]["name"] == "Test Org"

    @pytest.mark.asyncio
    async def test_get_invite_by_id_not_found(self):
        """Test ID retrieval when invitation not found."""
        invite_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_eq.limit.return_value = mock_limit
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_invite_by_id(invite_id)

            assert result is None


class TestGetOrganizationInvites:
    """Test cases for get_organization_invites function."""

    @pytest.mark.asyncio
    async def test_get_organization_invites_success(self):
        """Test successful organization invites retrieval."""
        organization_id = str(uuid.uuid4())
        mock_invites = [
            {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "email": "user1@example.com",
                "status": "pending"
            },
            {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "email": "user2@example.com",
                "status": "accepted"
            }
        ]

        mock_result = MagicMock()
        mock_result.data = mock_invites

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_fresh_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_order = MagicMock()
            mock_limit = MagicMock()
            mock_offset = MagicMock()
            mock_offset.execute = AsyncMock(return_value=mock_result)
            mock_limit.offset.return_value = mock_offset
            mock_order.limit.return_value = mock_limit
            mock_eq.order.return_value = mock_order
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_organization_invites(organization_id, limit=20, offset=0)

            assert len(result) == 2
            assert result[0]["organization_id"] == organization_id
            assert result[1]["organization_id"] == organization_id

    @pytest.mark.asyncio
    async def test_get_organization_invites_none_data(self):
        """Test organization invites retrieval when data is None."""
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = None

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_fresh_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_order = MagicMock()
            mock_limit = MagicMock()
            mock_offset = MagicMock()
            mock_offset.execute = AsyncMock(return_value=mock_result)
            mock_limit.offset.return_value = mock_offset
            mock_order.limit.return_value = mock_limit
            mock_eq.order.return_value = mock_order
            mock_select.eq.return_value = mock_eq
            mock_table.select.return_value = mock_select
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_organization_invites(organization_id)

            assert result == []


class TestGetOrganizationInvitesCount:
    """Test cases for get_organization_invites_count function."""

    @pytest.mark.asyncio
    async def test_get_organization_invites_count_success(self):
        """Test successful count retrieval."""
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.count = 5

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_count = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_count.eq.return_value = mock_eq
            mock_table.select.return_value = mock_count
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_organization_invites_count(organization_id)

            assert result == 5

    @pytest.mark.asyncio
    async def test_get_organization_invites_count_none_count(self):
        """Test count retrieval when count is None."""
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.count = None

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_count = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_count.eq.return_value = mock_eq
            mock_table.select.return_value = mock_count
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_organization_invites_count(organization_id)

            assert result == 0


class TestUpdateInviteStatus:
    """Test cases for update_invite_status function."""

    @pytest.mark.asyncio
    async def test_update_invite_status_success(self):
        """Test successful status update."""
        invite_id = str(uuid.uuid4())
        status = "accepted"
        accepted_by = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = [{"id": invite_id, "status": status}]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_fresh_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_update = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_update.eq.return_value = mock_eq
            mock_table.update.return_value = mock_update
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await update_invite_status(invite_id, status, accepted_by)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_invite_status_without_accepted_by(self):
        """Test status update without accepted_by parameter."""
        invite_id = str(uuid.uuid4())
        status = "rejected"

        mock_result = MagicMock()
        mock_result.data = [{"id": invite_id, "status": status}]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_fresh_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_update = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_update.eq.return_value = mock_eq
            mock_table.update.return_value = mock_update
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await update_invite_status(invite_id, status)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_invite_status_no_data(self):
        """Test status update when no data is returned."""
        invite_id = str(uuid.uuid4())
        status = "accepted"

        mock_result = MagicMock()
        mock_result.data = None

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_fresh_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_update = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_update.eq.return_value = mock_eq
            mock_table.update.return_value = mock_update
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await update_invite_status(invite_id, status)

            assert result is False

    @pytest.mark.asyncio
    async def test_update_invite_status_empty_data(self):
        """Test status update when empty data is returned."""
        invite_id = str(uuid.uuid4())
        status = "accepted"

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_fresh_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_update = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_update.eq.return_value = mock_eq
            mock_table.update.return_value = mock_update
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await update_invite_status(invite_id, status)

            assert result is False


class TestDeleteInvite:
    """Test cases for delete_invite function."""

    @pytest.mark.asyncio
    async def test_delete_invite_success(self):
        """Test successful invitation deletion."""
        invite_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = [{"id": invite_id}]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_delete = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_delete.eq.return_value = mock_eq
            mock_table.delete.return_value = mock_delete
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await delete_invite(invite_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_invite_no_data(self):
        """Test invitation deletion when no data is returned."""
        invite_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = None

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_delete = MagicMock()
            mock_eq = MagicMock()
            mock_eq.execute = AsyncMock(return_value=mock_result)
            mock_delete.eq.return_value = mock_eq
            mock_table.delete.return_value = mock_delete
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await delete_invite(invite_id)

            assert result is False


class TestCheckExistingInvite:
    """Test cases for check_existing_invite function."""

    @pytest.mark.asyncio
    async def test_check_existing_invite_found(self):
        """Test when existing invitation is found."""
        organization_id = str(uuid.uuid4())
        email = "test@example.com"

        mock_invite_data = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "email": email,
            "status": "pending"
        }

        mock_result = MagicMock()
        mock_result.data = [mock_invite_data]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value = mock_limit
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await check_existing_invite(organization_id, email)

            assert result["id"] == mock_invite_data["id"]
            assert result["email"] == email

    @pytest.mark.asyncio
    async def test_check_existing_invite_with_status(self):
        """Test checking existing invitation with specific status."""
        organization_id = str(uuid.uuid4())
        email = "test@example.com"
        status = "pending"

        mock_invite_data = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "email": email,
            "status": status
        }

        mock_result = MagicMock()
        mock_result.data = [mock_invite_data]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value = mock_limit
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await check_existing_invite(organization_id, email, status)

            assert result["status"] == status

    @pytest.mark.asyncio
    async def test_check_existing_invite_not_found(self):
        """Test when no existing invitation is found."""
        organization_id = str(uuid.uuid4())
        email = "test@example.com"

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value = mock_limit
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await check_existing_invite(organization_id, email)

            assert result is None


class TestCheckUserMembership:
    """Test cases for check_user_membership function."""

    @pytest.mark.asyncio
    async def test_check_user_membership_found(self):
        """Test when user membership is found."""
        organization_id = str(uuid.uuid4())
        email = "member@example.com"

        mock_member_data = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "email": email,
            "status": "active"
        }

        mock_result = MagicMock()
        mock_result.data = [mock_member_data]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value = mock_limit
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await check_user_membership(organization_id, email)

            assert result["email"] == email
            assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_check_user_membership_not_found(self):
        """Test when user membership is not found."""
        organization_id = str(uuid.uuid4())
        email = "nonmember@example.com"

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_limit = MagicMock()
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value = mock_limit
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await check_user_membership(organization_id, email)

            assert result is None


class TestAddUserToOrganization:
    """Test cases for add_user_to_organization function."""

    @pytest.mark.asyncio
    async def test_add_user_to_organization_success(self):
        """Test successful user addition to organization."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        email = "newuser@example.com"
        role_id = str(uuid.uuid4())
        role_name = "member"
        invited_by = str(uuid.uuid4())

        invite_data = {
            "user_id": user_id,
            "first_name": "Test",
            "last_name": "User",
            "phone": None,
            "timezone": "UTC",
            "salutation": None
        }

        mock_member_data = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "user_id": user_id,
            "email": email,
            "role_id": role_id,
            "role_name": role_name,
            "status": "active"
        }

        mock_result = MagicMock()
        mock_result.data = [mock_member_data]

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client, \
             patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.create_new_user", AsyncMock(return_value=mock_member_data)) as mock_create_user, \
             patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.is_isometrik_enabled", return_value=False):
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_table.insert.return_value = mock_insert
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await add_user_to_organization(
                organization_id, invite_data, email, role_id, role_name, invited_by, isometrik_credentials={}
            )

            assert result["id"] is not None
            assert result["organization_id"] == organization_id
            assert result["email"] == email
            assert result["role_name"] == role_name

    @pytest.mark.asyncio
    async def test_add_user_to_organization_no_data(self):
        """Test user addition when no data is returned."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        email = "newuser@example.com"
        role_id = str(uuid.uuid4())
        role_name = "member"
        invited_by = str(uuid.uuid4())

        invite_data = {
            "user_id": user_id,
            "first_name": "Test",
            "last_name": "User",
            "phone": None,
            "timezone": "UTC",
            "salutation": None
        }

        mock_result = MagicMock()
        mock_result.data = []

        with patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.get_supabase_admin_client") as mock_get_client, \
             patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.create_new_user", AsyncMock(return_value={})) as mock_create_user, \
             patch("libs.shared_db.postgres_db.user_service_operations.invite_operations.is_isometrik_enabled", return_value=False):
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_table.insert.return_value = mock_insert
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await add_user_to_organization(
                organization_id, invite_data, email, role_id, role_name, invited_by, isometrik_credentials={}
            )

            assert result == {}
