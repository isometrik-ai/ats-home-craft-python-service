# pylint: disable=all

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException
from apps.user_service.app.api.invites import router as invites_router
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError

# Global patches to prevent real permission checking
@pytest.fixture(autouse=True)
def mock_permission_system():
    """Mock the entire permission system to prevent real database calls."""
    with patch("apps.user_service.app.dependencies.common_utils.check_user_access_async", return_value=True), \
         patch("apps.user_service.app.dependencies.common_utils.extract_user_context") as mock_extract, \
         patch("apps.user_service.app.dependencies.common_utils.require_permission") as mock_require, \
         patch("apps.user_service.app.dependencies.common_utils.check_permissions") as mock_check_permissions, \
         patch("apps.user_service.app.api.invites.check_permissions") as mock_invites_check_permissions, \
         patch("apps.user_service.app.api.invites.require_permission") as mock_invites_require_permission, \
         patch("apps.user_service.app.dependencies.invite_utils.validate_organization_access", return_value=True) as mock_validate_org_access, \
         patch("apps.user_service.app.api.invites.validate_organization_access", return_value=True) as mock_invites_validate_org_access, \
         patch("apps.user_service.app.dependencies.invite_utils.build_invite_list_item") as mock_build_invite_list_item, \
         patch("apps.user_service.app.api.invites.build_invite_list_item") as mock_invites_build_invite_list_item:

        # Configure the mocks
        from apps.user_service.app.dependencies.common_utils import UserContext
        mock_user_context = UserContext(
            organization_id="550e8400-e29b-41d4-a716-446655440001",
            user_id="550e8400-e29b-41d4-a716-446655440000",
            email="test@example.com",
            user_type="organization_member"
        )

        mock_extract.return_value = mock_user_context
        mock_require.return_value = None  # No exception means permission granted
        mock_check_permissions.return_value = mock_user_context  # Return user context for permission checks
        mock_invites_check_permissions.return_value = mock_user_context  # Patch in invites module
        mock_invites_require_permission.return_value = None  # Patch in invites module

        # Configure build_invite_list_item to return proper data structure
        def mock_build_invite_list_item_func(invite_data):
            return {
                "invite_id": invite_data.get("id"),
                "email": invite_data.get("email"),
                "role_id": invite_data.get("role_id", str(uuid.uuid4())),
                "status": invite_data.get("status"),  # Include status field
                "invited_by": invite_data.get("invited_by"),
                "expires_at": invite_data.get("expires_at"),
                "created_at": invite_data.get("created_at"),
                "updated_at": invite_data.get("updated_at")
            }
        mock_build_invite_list_item.side_effect = mock_build_invite_list_item_func
        mock_invites_build_invite_list_item.side_effect = mock_build_invite_list_item_func

        yield


@pytest.fixture
def app():
    """Create FastAPI app with invites router for testing."""
    from apps.user_service.app.dependencies.common_utils import check_user_access_async, check_permissions, extract_user_context, require_permission
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(invites_router, prefix="/v1")

    # Mock authentication with proper structure
    def mock_get_user_from_auth():
        return {
            "sub": "550e8400-e29b-41d4-a716-446655440000",  # Valid UUID format
            "user_id": "550e8400-e29b-41d4-a716-446655440000",
            "organization_id": "550e8400-e29b-41d4-a716-446655440001",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "550e8400-e29b-41d4-a716-446655440001"}
        }

    def mock_extract_user_context(current_user):
        from apps.user_service.app.dependencies.common_utils import UserContext
        return UserContext(
            organization_id="550e8400-e29b-41d4-a716-446655440001",
            user_id="550e8400-e29b-41d4-a716-446655440000",
            email="test@example.com",
            user_type="organization_member"
        )

    async def mock_require_permission(permission_code, user_context, action_description=None, organization_id=None):
        # Always allow for testing
        pass

    async def mock_check_permissions(current_user, permission_codes, action_description=None, organization_id=None):
        return mock_extract_user_context(current_user)

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    app.dependency_overrides[check_permissions] = mock_check_permissions
    app.dependency_overrides[extract_user_context] = mock_extract_user_context
    app.dependency_overrides[require_permission] = mock_require_permission
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_invite_data():
    """Mock invitation data for testing."""
    return {
        "id": str(uuid.uuid4()),  # Changed back to "id" to match build_invite_list_item function
        "organization_id": str(uuid.uuid4()),
        "email": "newuser@example.com",
        "role_id": str(uuid.uuid4()),
        "role": "member",  # Add role field that the code expects
        "status": "pending",
        "invited_by": "550e8400-e29b-41d4-a716-446655440000",  # Changed to proper UUID
        "token_hash": "abc123xyz456",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }


@pytest.fixture
def mock_organization_data():
    """Mock organization data for testing."""
    return {
        "id": str(uuid.uuid4()),
        "name": "Test Organization",
        "slug": "test-org",
        "domain": "test.com",
        "max_users": 50,
        "member_count": 5,
        "plan_type": "premium",
        "status": "active"
    }


# ============================================================================
# ACCEPT INVITATION TESTS
# ============================================================================

class TestAcceptInvitation:
    """Test cases for POST /invite/accept endpoint."""

    def test_accept_invitation_success(self, client, mock_invite_data):
        """Test successful invitation acceptance."""
        request_data = {"token": "valid-token-123"}

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.add_user_to_organization", AsyncMock(return_value=True)), \
             patch("apps.user_service.app.api.invites.update_invite_status", AsyncMock(return_value=True)):

            response = client.post("/v1/invite/accept", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["organization_id"] == mock_invite_data["organization_id"]
            assert "accepted successfully" in data["message"]

    def test_accept_invitation_invalid_token(self, client):
        """Test invitation acceptance with invalid token."""
        request_data = {"token": "invalid-token"}

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=None)):
            response = client.post("/v1/invite/accept", json=request_data)

            assert response.status_code == 404
            assert "Invalid invitation token" in response.json()["detail"]

    def test_accept_invitation_user_already_member(self, client, mock_invite_data):
        """Test invitation acceptance when user is already a member."""
        request_data = {"token": "valid-token-123"}

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=True)):

            response = client.post("/v1/invite/accept", json=request_data)

            assert response.status_code == 409
            assert "already a member" in response.json()["detail"]

    def test_accept_invitation_database_error(self, client, mock_invite_data):
        """Test invitation acceptance with database error."""
        request_data = {"token": "valid-token-123"}

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.add_user_to_organization", AsyncMock(side_effect=DatabaseOperationError("Database error"))):

            response = client.post("/v1/invite/accept", json=request_data)

            assert response.status_code == 500
            assert "Failed to accept invitation" in response.json()["detail"]


# ============================================================================
# REJECT INVITATION TESTS
# ============================================================================

class TestRejectInvitation:
    """Test cases for POST /invite/reject endpoint."""

    def test_reject_invitation_success(self, client, mock_invite_data):
        """Test successful invitation rejection."""
        request_data = {"token": "valid-token-123"}

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.update_invite_status", AsyncMock(return_value=True)):

            response = client.post("/v1/invite/reject", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "rejected successfully" in data["message"]

    def test_reject_invitation_invalid_token(self, client):
        """Test invitation rejection with invalid token."""
        request_data = {"token": "invalid-token"}

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=None)):
            response = client.post("/v1/invite/reject", json=request_data)

            assert response.status_code == 404
            assert "Invalid invitation token" in response.json()["detail"]

    def test_reject_invitation_database_error(self, client, mock_invite_data):
        """Test invitation rejection with database error."""
        request_data = {"token": "valid-token-123"}

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_token", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.update_invite_status", AsyncMock(side_effect=DatabaseOperationError("Database error"))):

            response = client.post("/v1/invite/reject", json=request_data)

            assert response.status_code == 500
            assert "Failed to reject invitation" in response.json()["detail"]


# ============================================================================
# CLEANUP EXPIRED INVITATIONS TESTS
# ============================================================================

class TestCleanupExpiredInvitations:
    """Test cases for POST /invite/cleanup endpoint."""

    def test_cleanup_expired_invitations_success(self, client):
        """Test successful cleanup of expired invitations."""
        with patch("apps.user_service.app.api.invites.cleanup_expired_invites", AsyncMock(return_value=5)):
            response = client.post("/v1/invite/cleanup")

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert "Cleaned up 5 expired invitations" in data["message"]

    def test_cleanup_expired_invitations_no_invitations(self, client):
        """Test cleanup when no expired invitations exist."""
        with patch("apps.user_service.app.api.invites.cleanup_expired_invites", AsyncMock(return_value=0)):
            response = client.post("/v1/invite/cleanup")

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert "Cleaned up 0 expired invitations" in data["message"]

    def test_cleanup_expired_invitations_database_error(self, client):
        """Test cleanup with database error."""
        with patch("apps.user_service.app.api.invites.cleanup_expired_invites", AsyncMock(side_effect=DatabaseOperationError("Database error"))):
            response = client.post("/v1/invite/cleanup")

            assert response.status_code == 500
            assert "Failed to cleanup expired invitations" in response.json()["detail"]


# ============================================================================
# CREATE INVITATION TESTS
# ============================================================================

class TestCreateInvitation:
    """Test cases for POST /invite/{organization_id} endpoint."""

    def test_create_invitation_success(self, client, mock_organization_data):
        """Test successful invitation creation."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "newuser@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        mock_created_invite = {
            "id": str(uuid.uuid4()),
            "token_hash": "abc123xyz456",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "role_id": str(uuid.uuid4())
        }
        
        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.check_existing_invite", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.create_organization_invite", AsyncMock(return_value=mock_created_invite)), \
             patch("apps.user_service.app.api.invites.get_role_by_id", AsyncMock(return_value={"name": "member"})), \
             patch("apps.user_service.app.api.invites.send_organization_invitation_email", return_value=True):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["email"] == request_data["email"]
            assert "created successfully" in data["message"]

    def test_create_invitation_invalid_organization_id(self, client):
        """Test invitation creation with invalid organization ID."""
        invalid_org_id = "invalid-uuid"
        request_data = {
            "email": "newuser@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        response = client.post(f"/v1/invite/{invalid_org_id}", json=request_data)

        assert response.status_code == 400

    def test_create_invitation_organization_not_found(self, client):
        """Test invitation creation when organization doesn't exist."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "newuser@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=None)):
            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 404
            assert "Organization not found" in response.json()["detail"]

    def test_create_invitation_organization_capacity_exceeded(self, client, mock_organization_data):
        """Test invitation creation when organization capacity is exceeded."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "newuser@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=False):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 400
            assert "maximum user capacity" in response.json()["detail"]

    def test_create_invitation_user_already_member(self, client, mock_organization_data):
        """Test invitation creation when user is already a member."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "existing@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=True)):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 409
            assert "already a member" in response.json()["detail"]

    def test_create_invitation_existing_pending_invite(self, client, mock_organization_data):
        """Test invitation creation when pending invitation already exists."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "pending@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.check_existing_invite", AsyncMock(return_value=True)):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 409
            assert "pending invitation already exists" in response.json()["detail"]

    def test_create_invitation_invalid_email(self, client):
        """Test invitation creation with invalid email format."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "invalid-email",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)

        assert response.status_code == 422

    def test_create_invitation_invalid_role(self, client):
        """Test invitation creation with invalid role."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "newuser@example.com",
            "role": "invalid_role",
            "expires_in_days": 7
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)

        assert response.status_code == 422

    def test_create_invitation_invalid_expiration_days(self, client):
        """Test invitation creation with invalid expiration days."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "newuser@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 50  # Invalid: exceeds 30 days
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)

        assert response.status_code == 422

    def test_create_invitation_database_error(self, client, mock_organization_data):
        """Test invitation creation with database error."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "newuser@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.check_existing_invite", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.create_organization_invite", AsyncMock(side_effect=DatabaseOperationError("Database error"))):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 500
            assert "Failed to create invitation" in response.json()["detail"]


# ============================================================================
# GET ORGANIZATION INVITATIONS TESTS
# ============================================================================

class TestGetOrganizationInvitations:
    """Test cases for GET /invite/{organization_id} endpoint."""

    def test_get_organization_invitations_success(self, client, mock_invite_data):
        """Test successful retrieval of organization invitations."""
        organization_id = str(uuid.uuid4())
        mock_invitations = [mock_invite_data]

        with patch("apps.user_service.app.api.invites.get_organization_invites", AsyncMock(return_value=mock_invitations)), \
             patch("apps.user_service.app.api.invites.get_organization_invites_count", AsyncMock(return_value=1)):

            response = client.get(f"/v1/invite/{organization_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["total_count"] == 1
            assert len(data["data"]) == 1
            assert data["data"][0]["email"] == mock_invite_data["email"]

    def test_get_organization_invitations_with_pagination(self, client, mock_invite_data):
        """Test retrieval of organization invitations with pagination."""
        organization_id = str(uuid.uuid4())
        mock_invitations = [mock_invite_data]

        with patch("apps.user_service.app.api.invites.get_organization_invites", AsyncMock(return_value=mock_invitations)), \
             patch("apps.user_service.app.api.invites.get_organization_invites_count", AsyncMock(return_value=1)):

            response = client.get(f"/v1/invite/{organization_id}?page=1&page_size=10")

            assert response.status_code == 200
            data = response.json()
            assert data["page"] == 1
            assert data["page_size"] == 10

    def test_get_organization_invitations_empty_list(self, client):
        """Test retrieval when no invitations exist."""
        organization_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.get_organization_invites", AsyncMock(return_value=[])), \
             patch("apps.user_service.app.api.invites.get_organization_invites_count", AsyncMock(return_value=0)):

            response = client.get(f"/v1/invite/{organization_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["total_count"] == 0
            assert len(data["data"]) == 0

    def test_get_organization_invitations_invalid_organization_id(self, client):
        """Test retrieval with invalid organization ID."""
        invalid_org_id = "invalid-uuid"

        response = client.get(f"/v1/invite/{invalid_org_id}")

        assert response.status_code == 400


# ============================================================================
# RESEND INVITATION TESTS
# ============================================================================

class TestResendInvitation:
    """Test cases for PUT /invite/resend/{invite_id} endpoint."""

    def test_resend_invitation_success(self, client, mock_invite_data, mock_organization_data):
        """Test successful invitation resend."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.get_role_by_id", AsyncMock(return_value={"name": "member"})), \
             patch("apps.user_service.app.api.invites.send_organization_invitation_email", return_value=True):

            response = client.put(f"/v1/invite/resend/{invite_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "resent successfully" in data["message"]

    def test_resend_invitation_not_found(self, client):
        """Test resend invitation when invitation doesn't exist."""
        invite_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=None)):
            response = client.put(f"/v1/invite/resend/{invite_id}")

            assert response.status_code == 404
            assert "Invitation not found" in response.json()["detail"]

    def test_resend_invitation_invalid_invite_id(self, client):
        """Test resend invitation with invalid invite ID."""
        invalid_invite_id = "invalid-uuid"

        response = client.put(f"/v1/invite/resend/{invalid_invite_id}")

        assert response.status_code == 400

    def test_resend_invitation_email_failure(self, client, mock_invite_data, mock_organization_data):
        """Test resend invitation when email sending fails."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.get_role_by_id", AsyncMock(return_value={"name": "member"})), \
             patch("apps.user_service.app.api.invites.send_organization_invitation_email", return_value=False):

            response = client.put(f"/v1/invite/resend/{invite_id}")

            # Should still return success even if email fails
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    def test_resend_invitation_database_error(self, client, mock_invite_data, mock_organization_data):
        """Test resend invitation with database error."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.send_organization_invitation_email", side_effect=Exception("Email service error")):

            response = client.put(f"/v1/invite/resend/{invite_id}")

            assert response.status_code == 500
            assert "Failed to resend invitation email" in response.json()["detail"]


# ============================================================================
# REVOKE INVITATION TESTS
# ============================================================================

class TestRevokeInvitation:
    """Test cases for POST /invite/{invite_id}/revoke endpoint."""

    def test_revoke_invitation_success(self, client, mock_invite_data):
        """Test successful invitation revocation."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.can_revoke_invite", return_value=True), \
             patch("apps.user_service.app.api.invites.update_invite_status", AsyncMock(return_value=True)):

            response = client.post(f"/v1/invite/{invite_id}/revoke")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "revoked successfully" in data["message"]

    def test_revoke_invitation_not_found(self, client):
        """Test revoke invitation when invitation doesn't exist."""
        invite_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=None)):
            response = client.post(f"/v1/invite/{invite_id}/revoke")

            assert response.status_code == 404
            assert "Invitation not found" in response.json()["detail"]

    def test_revoke_invitation_invalid_invite_id(self, client):
        """Test revoke invitation with invalid invite ID."""
        invalid_invite_id = "invalid-uuid"

        response = client.post(f"/v1/invite/{invalid_invite_id}/revoke")

        assert response.status_code == 400

    def test_revoke_invitation_cannot_revoke(self, client, mock_invite_data):
        """Test revoke invitation when invitation cannot be revoked."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.can_revoke_invite", return_value=False):

            response = client.post(f"/v1/invite/{invite_id}/revoke")

            assert response.status_code == 400
            assert "cannot be revoked" in response.json()["detail"]

    def test_revoke_invitation_database_error(self, client, mock_invite_data):
        """Test revoke invitation with database error."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.can_revoke_invite", return_value=True), \
             patch("apps.user_service.app.api.invites.update_invite_status", AsyncMock(side_effect=DatabaseOperationError("Database error"))):

            response = client.post(f"/v1/invite/{invite_id}/revoke")

            assert response.status_code == 500
            assert "Failed to revoke invitation" in response.json()["detail"]


# ============================================================================
# DELETE INVITATION TESTS
# ============================================================================

class TestDeleteInvitation:
    """Test cases for DELETE /invite/{invite_id} endpoint."""

    def test_delete_invitation_success(self, client, mock_invite_data):
        """Test successful invitation deletion."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.delete_invite", AsyncMock(return_value=True)):

            response = client.delete(f"/v1/invite/{invite_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "deleted successfully" in data["message"]

    def test_delete_invitation_not_found(self, client):
        """Test delete invitation when invitation doesn't exist."""
        invite_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=None)):
            response = client.delete(f"/v1/invite/{invite_id}")

            assert response.status_code == 404
            assert "Invitation not found" in response.json()["detail"]

    def test_delete_invitation_invalid_invite_id(self, client):
        """Test delete invitation with invalid invite ID."""
        invalid_invite_id = "invalid-uuid"

        response = client.delete(f"/v1/invite/{invalid_invite_id}")

        assert response.status_code == 400

    def test_delete_invitation_database_error(self, client, mock_invite_data):
        """Test delete invitation with database error."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.delete_invite", AsyncMock(side_effect=DatabaseOperationError("Database error"))):

            response = client.delete(f"/v1/invite/{invite_id}")

            assert response.status_code == 500
            assert "Failed to delete invitation" in response.json()["detail"]

    def test_delete_invitation_delete_failed(self, client, mock_invite_data):
        """Test delete invitation when delete operation fails."""
        invite_id = str(uuid.uuid4())

        # Update mock data to match the current user's email
        mock_invite_data["email"] = "test@example.com"

        with patch("apps.user_service.app.api.invites.get_invite_by_id", AsyncMock(return_value=mock_invite_data)), \
             patch("apps.user_service.app.api.invites.delete_invite", AsyncMock(return_value=False)):

            response = client.delete(f"/v1/invite/{invite_id}")

            assert response.status_code == 404
            assert "Invitation not found" in response.json()["detail"]


# ============================================================================
# VALIDATION AND ERROR SCENARIOS TESTS
# ============================================================================

class TestInviteValidation:
    """Test cases for invitation validation scenarios."""

    def test_invite_validation_invalid_email_format(self, client):
        """Test invitation creation with invalid email format."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "not-an-email",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)

        assert response.status_code == 422

    def test_invite_validation_invalid_role(self, client):
        """Test invitation creation with invalid role."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "user@example.com",
            "role": "superuser",  # Invalid role
            "expires_in_days": 7
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)

        assert response.status_code == 422

    def test_invite_validation_expiration_days_out_of_range(self, client):
        """Test invitation creation with expiration days out of range."""
        organization_id = str(uuid.uuid4())

        # Test with 0 days
        request_data = {
            "email": "user@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 0
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)
        assert response.status_code == 422

        # Test with 31 days
        request_data["expires_in_days"] = 31
        response = client.post(f"/v1/invite/{organization_id}", json=request_data)
        assert response.status_code == 422

    def test_invite_validation_missing_required_fields(self, client):
        """Test invitation creation with missing required fields."""
        organization_id = str(uuid.uuid4())

        # Test without email
        request_data = {
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)
        assert response.status_code == 422

    def test_invite_validation_negative_expiration_days(self, client):
        """Test invitation creation with negative expiration days."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "user@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": -1
        }

        response = client.post(f"/v1/invite/{organization_id}", json=request_data)

        assert response.status_code == 422


# ============================================================================
# PERMISSION AND ACCESS CONTROL TESTS
# ============================================================================

class TestInvitePermissions:
    """Test cases for invitation permission and access control."""

    def test_create_invitation_permission_denied(self, client):
        """Test invitation creation with insufficient permissions."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "user@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 7
        }
        
        with patch("apps.user_service.app.api.invites.check_permissions", AsyncMock(side_effect=HTTPException(status_code=403, detail="Permission denied"))):
            response = client.post(f"/v1/invite/{organization_id}", json=request_data)
            
            assert response.status_code == 403

    def test_get_invitations_permission_denied(self, client):
        """Test get invitations with insufficient permissions."""
        organization_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.check_permissions", AsyncMock(side_effect=HTTPException(status_code=403, detail="Permission denied"))):
            response = client.get(f"/v1/invite/{organization_id}")

            assert response.status_code == 403

    def test_resend_invitation_permission_denied(self, client):
        """Test resend invitation with insufficient permissions."""
        invite_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.check_permissions", AsyncMock(side_effect=HTTPException(status_code=403, detail="Permission denied"))):
            response = client.put(f"/v1/invite/resend/{invite_id}")

            assert response.status_code == 403

    def test_revoke_invitation_permission_denied(self, client):
        """Test revoke invitation with insufficient permissions."""
        invite_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.require_permission", AsyncMock(side_effect=HTTPException(status_code=403, detail="Permission denied"))):
            response = client.post(f"/v1/invite/{invite_id}/revoke")

            assert response.status_code == 403

    def test_delete_invitation_permission_denied(self, client):
        """Test delete invitation with insufficient permissions."""
        invite_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.require_permission", AsyncMock(side_effect=HTTPException(status_code=403, detail="Permission denied"))):
            response = client.delete(f"/v1/invite/{invite_id}")

            assert response.status_code == 403


# ============================================================================
# EDGE CASES AND BOUNDARY TESTS
# ============================================================================

class TestInviteEdgeCases:
    """Test cases for edge cases and boundary conditions."""

    def test_create_invitation_with_minimum_expiration_days(self, client, mock_organization_data):
        """Test invitation creation with minimum expiration days (1 day)."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "user@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 1
        }

        mock_created_invite = {
            "id": str(uuid.uuid4()),
            "token_hash": "abc123xyz456",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        }
        
        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.check_existing_invite", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.create_organization_invite", AsyncMock(return_value=mock_created_invite)), \
             patch("apps.user_service.app.api.invites.get_role_by_id", AsyncMock(return_value={"name": "member"})), \
             patch("apps.user_service.app.api.invites.send_organization_invitation_email", return_value=True):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 201

    def test_create_invitation_with_maximum_expiration_days(self, client, mock_organization_data):
        """Test invitation creation with maximum expiration days (30 days)."""
        organization_id = str(uuid.uuid4())
        request_data = {
            "email": "user@example.com",
            "role_id": str(uuid.uuid4()),
            "expires_in_days": 30
        }

        mock_created_invite = {
            "id": str(uuid.uuid4()),
            "token_hash": "abc123xyz456",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        }
        
        with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
             patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
             patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.check_existing_invite", AsyncMock(return_value=False)), \
             patch("apps.user_service.app.api.invites.create_organization_invite", AsyncMock(return_value=mock_created_invite)), \
             patch("apps.user_service.app.api.invites.get_role_by_id", AsyncMock(return_value={"name": "member"})), \
             patch("apps.user_service.app.api.invites.send_organization_invitation_email", return_value=True):

            response = client.post(f"/v1/invite/{organization_id}", json=request_data)

            assert response.status_code == 201

    def test_get_invitations_with_large_page_size(self, client):
        """Test get invitations with maximum allowed page size."""
        organization_id = str(uuid.uuid4())

        with patch("apps.user_service.app.api.invites.get_organization_invites", AsyncMock(return_value=[])), \
             patch("apps.user_service.app.api.invites.get_organization_invites_count", AsyncMock(return_value=0)):

            response = client.get(f"/v1/invite/{organization_id}?page_size=100")

            assert response.status_code == 200

    def test_get_invitations_with_excessive_page_size(self, client):
        """Test get invitations with page size exceeding maximum."""
        organization_id = str(uuid.uuid4())

        response = client.get(f"/v1/invite/{organization_id}?page_size=101")

        assert response.status_code == 422

    def test_create_invitation_with_all_valid_roles(self, client, mock_organization_data):
        """Test invitation creation with all valid roles."""
        organization_id = str(uuid.uuid4())
        valid_roles = ["owner", "admin", "member"]

        mock_created_invite = {
            "id": str(uuid.uuid4()),
            "token_hash": "abc123xyz456",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        }

        for role in valid_roles:
            request_data = {
                "email": f"user_{role}@example.com",
                "role_id": str(uuid.uuid4()),
                "expires_in_days": 7
            }

            with patch("apps.user_service.app.api.invites.get_organisation_details_by_id", AsyncMock(return_value=mock_organization_data)), \
                 patch("apps.user_service.app.api.invites.check_organization_capacity", return_value=True), \
                 patch("apps.user_service.app.api.invites.check_user_membership", AsyncMock(return_value=False)), \
                 patch("apps.user_service.app.api.invites.check_existing_invite", AsyncMock(return_value=False)), \
                 patch("apps.user_service.app.api.invites.create_organization_invite", AsyncMock(return_value=mock_created_invite)), \
                 patch("apps.user_service.app.api.invites.get_role_by_id", AsyncMock(return_value={"name": role})), \
                 patch("apps.user_service.app.api.invites.send_organization_invitation_email", return_value=True):

                response = client.post(f"/v1/invite/{organization_id}", json=request_data)

                assert response.status_code == 201
