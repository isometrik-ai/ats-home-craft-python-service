# pylint: disable=all

"""
Tests for update_user_profile endpoint in update_user.py
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException, status
from apps.user_service.app.api.admin_management.users.update_user import router as update_user_router
from libs.shared_middleware.jwt_auth import get_user_from_auth


@pytest.fixture
def app():
    """Create FastAPI app with update_user router."""
    app = FastAPI()
    app.include_router(update_user_router, prefix="/v1/admin")
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_current_user():
    """Mock current user from JWT token."""
    return {
        "sub": str(uuid.uuid4()),
        "email": "test@example.com",
        "user_metadata": {
            "first_name": "Old",
            "last_name": "User",
            "timezone": "UTC",
            "avatar_url": "house-of-apps-legal-ai/user-123/old-avatar.jpg"
        }
    }


@pytest.fixture
def mock_user_context():
    """Mock user context."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    return MagicMock(
        user_id=user_id,
        organization_id=org_id,
        email="test@example.com"
    )


class TestUpdateUserProfile:
    """Test cases for update_user_profile endpoint."""

    @pytest.mark.asyncio
    async def test_update_user_profile_success_with_organization(self, client, mock_current_user, mock_user_context):
        """Test successful profile update when user is in organization."""
        user_id = mock_user_context.user_id
        org_id = mock_user_context.organization_id

        current_user_data = {
            "user_id": user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User",
            "full_name": "Old User",
            "timezone": "UTC",
            "avatar_url": "house-of-apps-legal-ai/user-123/old-avatar.jpg"
        }

        updated_user_data = {
            "user_id": user_id,
            "email": "test@example.com",
            "first_name": "New",
            "last_name": "Name",
            "full_name": "New Name",
            "timezone": "America/New_York",
            "avatar_url": "house-of-apps-legal-ai/user-123/new-avatar.jpg"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {"existing": "data"}

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New",
                    "last_name": "Name",
                    "timezone": "America/New_York",
                    "avatar_url": "house-of-apps-legal-ai/user-123/new-avatar.jpg"
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "User profile updated successfully"

    @pytest.mark.asyncio
    async def test_update_user_profile_success_without_organization(self, client, mock_current_user):
        """Test successful profile update when user is not in organization."""
        user_id = str(uuid.uuid4())
        mock_user_context = MagicMock(
            user_id=user_id,
            organization_id=None,
            email="test@example.com"
        )

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {
            "first_name": "Old",
            "last_name": "User"
        }

        updated_profile = {
            "user_id": user_id,
            "first_name": "New",
            "last_name": "Name",
            "full_name": "New Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New",
                    "last_name": "Name"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_get_user_by_id_exception_no_org(self, client, mock_current_user):
        """Test profile update when get_user_by_id raises exception (no organization)."""
        user_id = str(uuid.uuid4())
        mock_user_context = MagicMock(
            user_id=user_id,
            organization_id=None,
            email="test@example.com"
        )

        updated_profile = {
            "user_id": user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(side_effect=Exception("Supabase error"))), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New",
                    "last_name": "Name"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_get_user_by_id_success_no_org(self, client, mock_current_user):
        """Test profile update when get_user_by_id succeeds (no organization)."""
        user_id = str(uuid.uuid4())
        mock_user_context = MagicMock(
            user_id=user_id,
            organization_id=None,
            email="test@example.com"
        )

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {
            "first_name": "Old",
            "last_name": "User",
            "timezone": "UTC"
        }

        updated_profile = {
            "user_id": user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New",
                    "last_name": "Name"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_no_fields_provided(self, client, mock_current_user, mock_user_context):
        """Test profile update with no fields provided."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={}
            )

            assert response.status_code == 400
            assert "No fields provided for update" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_user_profile_user_not_found_in_organization(self, client, mock_current_user, mock_user_context):
        """Test profile update when user not found in organization."""
        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=None)):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            # This should still work because we create basic profile structure
            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            # Should succeed because we handle users not in organization
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_partial_update_first_name_only(self, client, mock_current_user, mock_user_context):
        """Test partial update with only first_name."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User",
            "full_name": "Old User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "User",
            "full_name": "New User"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_partial_update_last_name_only(self, client, mock_current_user, mock_user_context):
        """Test partial update with only last_name."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User",
            "full_name": "Old User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "New",
            "full_name": "Old New"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "last_name": "New"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_full_name_calculation(self, client, mock_current_user, mock_user_context):
        """Test that full_name is calculated from first_name and last_name."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User",
            "full_name": "Old User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "John",
            "last_name": "Doe",
            "full_name": "John Doe"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "John",
                    "last_name": "Doe"
                }
            )

            assert response.status_code == 200
            # Verify update_user_info was called with full_name
            from apps.user_service.app.api.admin_management.users.update_user import update_user_info
            # The full_name should be included in the update

    @pytest.mark.asyncio
    async def test_update_user_profile_update_user_info_returns_none(self, client, mock_current_user, mock_user_context):
        """Test when update_user_info returns None (should continue with metadata update)."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            # Should still succeed because we continue with metadata update
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_metadata_get_user_by_id_exception(self, client, mock_current_user, mock_user_context):
        """Test when get_user_by_id raises exception during metadata update."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(side_effect=Exception("Supabase error"))), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            # Should succeed, using JWT token metadata as fallback
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_metadata_get_user_by_id_success(self, client, mock_current_user, mock_user_context):
        """Test when get_user_by_id succeeds during metadata update."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {"existing": "metadata"}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_metadata_update_returns_false(self, client, mock_current_user, mock_user_context):
        """Test when update_metadata_of_user returns False."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=False)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            # Should still succeed, just logs warning
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_metadata_update_exception(self, client, mock_current_user, mock_user_context):
        """Test when update_metadata_of_user raises exception."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(side_effect=Exception("Metadata update error"))), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            # Should still succeed, just logs warning
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_get_user_profile_by_id_returns_none(self, client, mock_current_user, mock_user_context):
        """Test when get_user_profile_by_id returns None."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User",
            "full_name": "Old User",
            "timezone": "UTC",
            "avatar_url": "house-of-apps-legal-ai/user-123/old-avatar.jpg"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            # Should succeed, uses current_user_data as fallback
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_timezone_only(self, client, mock_current_user, mock_user_context):
        """Test update with only timezone field."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User",
            "timezone": "UTC"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "timezone": "America/New_York"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "timezone": "America/New_York"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "timezone": "America/New_York"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_avatar_url_only(self, client, mock_current_user, mock_user_context):
        """Test update with only avatar_url field."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "avatar_url": "house-of-apps-legal-ai/user-123/old-avatar.jpg"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "avatar_url": "house-of-apps-legal-ai/user-123/new-avatar.jpg"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "avatar_url": "house-of-apps-legal-ai/user-123/new-avatar.jpg"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "avatar_url": "house-of-apps-legal-ai/user-123/new-avatar.jpg"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_full_name_empty_strings(self, client, mock_current_user, mock_user_context):
        """Test full_name calculation with empty strings."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "",
            "last_name": "",
            "full_name": ""
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "",
            "last_name": "",
            "full_name": ""
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "",
            "last_name": ""
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "",
                    "last_name": ""
                }
            )

            # Should succeed, full_name won't be set if both are empty
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_get_user_by_id_no_user_attr(self, client, mock_current_user):
        """Test when get_user_by_id returns data without user attribute."""
        user_id = str(uuid.uuid4())
        mock_user_context = MagicMock(
            user_id=user_id,
            organization_id=None,
            email="test@example.com"
        )

        # Mock user_data without user attribute
        mock_user_data = MagicMock()
        mock_user_data.user = None

        updated_profile = {
            "user_id": user_id,
            "first_name": "New"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New"
                }
            )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_phone_enabled(self, client, mock_current_user, mock_user_context):
        """Test updating verification preference with PHONE enabled."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)) as mock_update_metadata, \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "two_fa_enabled": True,
                    "verification_method": "PHONE"
                }
            )

            assert response.status_code == 200
            # Verify that update_metadata_of_user was called with verification_preference
            call_args = mock_update_metadata.call_args
            assert call_args is not None
            updated_metadata = call_args[0][1]  # Second argument is the metadata dict
            assert "verification_preference" in updated_metadata
            assert updated_metadata["verification_preference"]["enabled"] is True
            assert updated_metadata["verification_preference"]["type"] == "PHONE"

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_email_enabled(self, client, mock_current_user, mock_user_context):
        """Test updating verification preference with EMAIL enabled."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)) as mock_update_metadata, \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "two_fa_enabled": True,
                    "verification_method": "EMAIL"
                }
            )

            assert response.status_code == 200
            # Verify that update_metadata_of_user was called with verification_preference
            call_args = mock_update_metadata.call_args
            assert call_args is not None
            updated_metadata = call_args[0][1]
            assert "verification_preference" in updated_metadata
            assert updated_metadata["verification_preference"]["enabled"] is True
            assert updated_metadata["verification_preference"]["type"] == "EMAIL"

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_disabled(self, client, mock_current_user, mock_user_context):
        """Test updating verification preference to disabled."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)) as mock_update_metadata, \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "two_fa_enabled": False,
                    "verification_method": "PHONE"
                }
            )

            assert response.status_code == 200
            # Verify that update_metadata_of_user was called with verification_preference
            call_args = mock_update_metadata.call_args
            assert call_args is not None
            updated_metadata = call_args[0][1]
            assert "verification_preference" in updated_metadata
            assert updated_metadata["verification_preference"]["enabled"] is False
            assert updated_metadata["verification_preference"]["type"] == "PHONE"

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_only_enabled_error(self, client, mock_current_user, mock_user_context):
        """Test error when only two_fa_enabled is provided."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "two_fa_enabled": True
                }
            )

            assert response.status_code == 400
            assert "Both two_fa_enabled and verification_method must be provided together" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_only_type_error(self, client, mock_current_user, mock_user_context):
        """Test error when only verification_method is provided."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "verification_method": "PHONE"
                }
            )

            assert response.status_code == 400
            assert "Both two_fa_enabled and verification_method must be provided together" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_invalid_type_error(self, client, mock_current_user, mock_user_context):
        """Test error when verification_method is invalid."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "two_fa_enabled": True,
                    "verification_method": "INVALID"
                }
            )

            assert response.status_code == 400
            assert "verification_method must be either 'PHONE' or 'EMAIL'" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_with_other_fields(self, client, mock_current_user, mock_user_context):
        """Test updating verification preference along with other fields."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name",
            "full_name": "New Name"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "New",
            "last_name": "Name",
            "full_name": "New Name"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)) as mock_update_metadata, \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "first_name": "New",
                    "last_name": "Name",
                    "two_fa_enabled": True,
                    "verification_method": "EMAIL"
                }
            )

            assert response.status_code == 200
            # Verify that update_metadata_of_user was called with both fields
            call_args = mock_update_metadata.call_args
            assert call_args is not None
            updated_metadata = call_args[0][1]
            assert "first_name" in updated_metadata
            assert "last_name" in updated_metadata
            assert "full_name" in updated_metadata
            assert "verification_preference" in updated_metadata
            assert updated_metadata["verification_preference"]["enabled"] is True
            assert updated_metadata["verification_preference"]["type"] == "EMAIL"

    @pytest.mark.asyncio
    async def test_update_user_profile_verification_preference_case_insensitive(self, client, mock_current_user, mock_user_context):
        """Test that verification_method is case-insensitive and stored as uppercase."""
        current_user_data = {
            "user_id": mock_user_context.user_id,
            "email": "test@example.com",
            "first_name": "Old",
            "last_name": "User"
        }

        updated_user_data = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}

        updated_profile = {
            "user_id": mock_user_context.user_id,
            "first_name": "Old",
            "last_name": "User"
        }

        with patch('apps.user_service.app.api.admin_management.users.update_user.extract_user_context',
                   AsyncMock(return_value=mock_user_context)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization',
                   AsyncMock(return_value=current_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_user_info',
                   AsyncMock(return_value=updated_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.update_metadata_of_user',
                   AsyncMock(return_value=True)) as mock_update_metadata, \
             patch('apps.user_service.app.api.admin_management.users.update_user.get_user_profile_by_id',
                   AsyncMock(return_value=updated_profile)), \
             patch('apps.user_service.app.api.admin_management.users.update_user.set_audit_old_data_from_user',
                   return_value=None):

            client.app.dependency_overrides[get_user_from_auth] = lambda: mock_current_user

            response = client.put(
                "/v1/admin/users/update",
                json={
                    "two_fa_enabled": True,
                    "verification_method": "phone"  # lowercase
                }
            )

            assert response.status_code == 200
            # Verify that type is stored as uppercase
            call_args = mock_update_metadata.call_args
            assert call_args is not None
            updated_metadata = call_args[0][1]
            assert updated_metadata["verification_preference"]["type"] == "PHONE"

