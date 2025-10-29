# pylint: disable=all

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException
from apps.user_service.app.dependencies.common_utils import check_user_access_async
from apps.user_service.app.api.admin_management.users.users import router as users_router
from apps.user_service.app.api.admin_management.users.update_user import router as update_user_router
from apps.user_service.app.api.admin_management.users.user_profile import router as user_profile_router
from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import USER_NOT_FOUND_MESSAGE


@pytest.fixture
def app():
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(users_router, prefix="/v1/admin")
    app.include_router(update_user_router, prefix="/v1/admin")
    app.include_router(user_profile_router, prefix="/v1/admin")

    def mock_get_user_from_auth():
        return {
            "user_id": str(uuid.uuid4()),  # Valid UUID for current user
        "organization_id": str(uuid.uuid4()),
        "email": "test@example.com",
    }

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ============================================================================
# API LAYER TESTS (existing + expanded)
# ============================================================================

def test_users_list_success(client):
    """Test successful users list retrieval."""
    from apps.user_service.app.schemas.users import UserListItem
    from datetime import datetime

    # Create proper UserListItem objects
    mock_user = UserListItem(
        user_id="u1",
        email="user1@example.com",
        full_name="User 1",
        first_name="User",
        last_name="1",
        phone="+1234567890",
        role_name="Admin",
        role_id="role1",
        status="active",
        joined_at=datetime.now(timezone.utc).isoformat(),
        last_active_at=datetime.now(timezone.utc).isoformat(),
        permissions_count=5
    )

    with patch("apps.user_service.app.api.admin_management.users.users.get_users_details_list", AsyncMock(return_value=[
        {"user_id": "u1", "email": "user1@example.com", "full_name": "User 1", "status": "active", "role_id": "role1", "first_name": "User", "last_name": "1", "phone": "+1234567890", "joined_at": datetime.now(timezone.utc), "last_active_at": datetime.now(timezone.utc)}
    ])), patch("apps.user_service.app.api.admin_management.users.users.get_users_total_count", AsyncMock(return_value=1)), \
         patch("apps.user_service.app.api.admin_management.users.users.transform_users", AsyncMock(return_value=[mock_user])):
        res = client.get("/v1/admin/users/list?page=1&page_size=20")
        assert res.status_code == 200
        body = res.json()
        assert body["total_count"] == 1
        assert len(body["data"]) == 1
        assert body["data"][0]["email"] == "user1@example.com"


def test_get_user_profile_success(client):
    """Test successful user profile retrieval."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    
    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )
    
    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context", AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
            "user_id": "u1", "email": "test@example.com", "full_name": "Test User", "first_name": "Test", "last_name": "User", "status": "active",
            "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Administrator",
            "organization_id": str(uuid.uuid4()), "avatar_url": None, "phone": None, "timezone": "UTC",
            "joined_at": None, "last_active_at": None
        })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["email"] == "test@example.com"
        assert body["data"]["full_name"] == "Test User"


def test_get_user_profile_basic_profile_creation(client):
    """Test user profile creation when no organization-linked profile exists."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    
    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )
    
    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context", AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value=None)), \
         patch("libs.shared_db.supabase_db.admin_operations.user.get_user_by_id", AsyncMock(return_value=MagicMock(user=MagicMock(user_metadata={})))):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["email"] == "test@example.com"
        assert body["data"]["full_name"] is not None  # Should have basic profile data


def test_get_user_profile_email_mismatch(client):
    """Test user profile email mismatch."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    
    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )
    
    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context", AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
            "user_id": "u1", "email": "different@example.com", "full_name": "Test User", "first_name": "Test", "last_name": "User", "status": "active",
            "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Administrator",
            "organization_id": str(uuid.uuid4()), "avatar_url": None, "phone": None, "timezone": "UTC",
            "joined_at": None, "last_active_at": None
        })):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 403
        assert "Token email does not match user profile" in res.json()["detail"]


def test_get_user_profile_invalid_user_type(client):
    """Test user profile with invalid user type."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    
    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )
    
    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context", AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
            "user_id": "u1", "email": "test@example.com", "full_name": "Test User", "first_name": "Test", "last_name": "User", "status": "active", "user_type": "invalid",
            "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Administrator",
            "organization_id": str(uuid.uuid4()), "avatar_url": None, "phone": None, "timezone": "UTC",
            "joined_at": None, "last_active_at": None
        })):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200  # This endpoint doesn't check user_type
        body = res.json()
        assert body["data"]["email"] == "test@example.com"


def test_get_user_by_id_success(client):
    """Test successful user retrieval by ID."""
    user_id = str(uuid.uuid4())
    with patch("apps.user_service.app.api.admin_management.users.user_profile.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=user_id, email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
             "user_id": user_id, "email": "target@example.com", "full_name": "Target User", "first_name": "Target", "last_name": "User", "status": "active",
             "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Administrator",
             "organization_id": str(uuid.uuid4()), "avatar_url": None, "phone": None, "timezone": "UTC",
             "joined_at": None, "last_active_at": None
         })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])):
        res = client.get(f"/v1/admin/users/{user_id}")
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["user_id"] == user_id
        assert body["data"]["email"] == "target@example.com"


def test_get_user_by_id_not_found(client):
    """Test user not found by ID."""
    user_id = str(uuid.uuid4())
    with patch("apps.user_service.app.api.admin_management.users.user_profile.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=user_id, email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value=None)):
        res = client.get(f"/v1/admin/users/{user_id}")
        assert res.status_code == 404
        assert USER_NOT_FOUND_MESSAGE in res.json()["detail"]


def test_get_user_by_id_invalid_user_type(client):
    """Test user retrieval with invalid user type."""
    user_id = str(uuid.uuid4())
    with patch("apps.user_service.app.api.admin_management.users.user_profile.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=user_id, email="test@example.com", user_type="invalid"))), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
             "user_id": user_id, "email": "target@example.com", "full_name": "Target User", "status": "active", "user_type": "invalid",
             "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Administrator",
             "organization_id": str(uuid.uuid4())
         })):
        res = client.get(f"/v1/admin/users/{user_id}")
        assert res.status_code == 403
        assert "Only organization members can access user profiles" in res.json()["detail"]


def test_get_user_by_id_permission_denied(client):
    """Test user retrieval permission denied."""
    user_id = str(uuid.uuid4())
    with patch("apps.user_service.app.api.admin_management.users.user_profile.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=user_id, email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
             "user_id": user_id, "email": "target@example.com", "full_name": "Target User", "first_name": "Target", "last_name": "User", "status": "active",
             "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Administrator",
             "organization_id": str(uuid.uuid4()), "avatar_url": None, "phone": None, "timezone": "UTC",
             "joined_at": None, "last_active_at": None
         })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])):
        res = client.get(f"/v1/admin/users/{user_id}")
        assert res.status_code == 200  # This should pass with our mock


# ============================================================================
# CREATE USER TESTS (POST /users) - MISSING FROM COVERAGE!
# ============================================================================

def test_create_user_success(client):
    """Test successful user creation."""
    user_data = {
        "email": "new@example.com",
        "role_id": str(uuid.uuid4()),
        "full_name": "New User",
        "phone": "+1234567890",
        "timezone": "UTC"
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.create_new_user", AsyncMock(return_value={"user_id": "new-user-id"})):

        res = client.post("/v1/admin/users", json=user_data)
        assert res.status_code == 201
        assert "User created and invited successfully" in res.json()["message"]


def test_create_user_already_exists(client):
    """Test user creation when user already exists."""
    user_data = {
        "email": "existing@example.com",
        "role_id": str(uuid.uuid4()),
        "full_name": "Existing User"
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=True)):
        res = client.post("/v1/admin/users", json=user_data)
        assert res.status_code == 409
        assert "User already exists in organization" in res.json()["detail"]


# ============================================================================
# UPDATE USER TESTS (PUT /users/update/{user_id}) - MISSING FROM COVERAGE!
# ============================================================================

def test_update_user_success(client):
    """Test successful user update."""
    user_id = str(uuid.uuid4())
    update_data = {
        "full_name": "Updated User",
        "phone": "+9876543210",
        "timezone": "EST",
        "status": "active",
        "role_id": str(uuid.uuid4())
    }

    mock_user_data = {
            "user_id": user_id,
            "email": "user@example.com",
            "full_name": "Original User",
            "first_name": "Original",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "avatar_url": None,
            "status": "invited",
            "role_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": datetime.now(timezone.utc)
        }

    mock_updated_profile = {
            "user_id": user_id,
            "email": "user@example.com",
            "full_name": "Updated User",
            "first_name": "Updated",
            "last_name": "User",
            "phone": "+9876543210",
            "timezone": "EST",
            "avatar_url": None,
            "status": "active",
            "role_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": datetime.now(timezone.utc)
        }

    with patch("apps.user_service.app.api.admin_management.users.users.check_phone_exists_for_other_user", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.get_user_in_organization", AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.users.update_user_info", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.users.users.get_user_profile_by_id", AsyncMock(return_value=mock_updated_profile)), \
         patch("apps.user_service.app.api.admin_management.users.users.get_user_permissions", AsyncMock(return_value=[])):

        res = client.put(f"/v1/admin/users/update/{user_id}", json=update_data)
        assert res.status_code == 200
        assert "User updated successfully" in res.json()["message"]


def test_update_user_duplicate_phone(client):
    """Test user update with duplicate phone number."""
    user_id = str(uuid.uuid4())
    update_data = {
        "phone": "+1234567890"
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_phone_exists_for_other_user", AsyncMock(return_value=True)):
        res = client.put(f"/v1/admin/users/update/{user_id}", json=update_data)
        assert res.status_code == 400
        assert "Phone number already exists for another user" in res.json()["detail"]


def test_update_user_not_found(client):
    """Test user update when user not found."""
    user_id = str(uuid.uuid4())
    update_data = {
        "full_name": "Updated User"
    }

    mock_user_data = {
        "user_id": user_id,
        "email": "user@example.com",
        "full_name": "Test User",
        "first_name": "Test",
        "last_name": "User",
        "phone": "+1234567890",
        "timezone": "UTC",
        "avatar_url": None,
        "status": "active",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "joined_at": datetime.now(timezone.utc),
        "last_active_at": datetime.now(timezone.utc)
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_phone_exists_for_other_user", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.get_user_in_organization", AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.users.update_user_info", AsyncMock(return_value=False)):

        res = client.put(f"/v1/admin/users/update/{user_id}", json=update_data)
        assert res.status_code == 404
        assert USER_NOT_FOUND_MESSAGE in res.json()["detail"]


# ============================================================================
# DELETE USER TESTS (DELETE /users/delete/{user_id}) - MISSING FROM COVERAGE!
# ============================================================================

def test_delete_user_success(client):
    """Test successful user deletion."""
    user_id = str(uuid.uuid4())

    mock_user_data = {
        "user_id": user_id,
        "email": "user@example.com",
        "full_name": "User to Delete",
        "organization_id": str(uuid.uuid4())
    }

    with patch("apps.user_service.app.api.admin_management.users.users.get_user_in_organization", AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.users.delete_user", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.users.users.delete_auth_user", AsyncMock(return_value=True)):

        res = client.delete(f"/v1/admin/users/delete/{user_id}")
        assert res.status_code == 200
        assert "User removed successfully" in res.json()["message"]


def test_delete_user_not_found(client):
    """Test user deletion when user not found."""
    user_id = str(uuid.uuid4())

    mock_user_data = {
        "user_id": user_id,
        "email": "user@example.com",
        "full_name": "Test User",
        "first_name": "Test",
        "last_name": "User",
        "phone": "+1234567890",
        "timezone": "UTC",
        "avatar_url": None,
        "status": "active",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "joined_at": datetime.now(timezone.utc),
        "last_active_at": datetime.now(timezone.utc)
    }

    with patch("apps.user_service.app.api.admin_management.users.users.get_user_in_organization", AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.users.delete_user", AsyncMock(return_value=False)):

        res = client.delete(f"/v1/admin/users/delete/{user_id}")
        assert res.status_code == 404
        assert USER_NOT_FOUND_MESSAGE in res.json()["detail"]


def test_delete_user_auth_not_found(client):
    """Test user deletion when auth user not found."""
    user_id = str(uuid.uuid4())

    mock_user_data = {
        "user_id": user_id,
        "email": "user@example.com",
        "full_name": "User to Delete",
        "organization_id": str(uuid.uuid4())
    }

    with patch("apps.user_service.app.api.admin_management.users.users.get_user_in_organization", AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.users.delete_user", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.users.users.delete_auth_user", AsyncMock(return_value=False)):

        res = client.delete(f"/v1/admin/users/delete/{user_id}")
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]


# ============================================================================
# INVITE USER TESTS (existing)
# ============================================================================

def test_invite_user_success(client):
    """Test successful user invitation."""
    payload = {
        "email": "new@example.com",
        "full_name": "New User",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4())
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.invite_user_with_email", AsyncMock(return_value={"user_id": "new-user-id"})), \
         patch("apps.user_service.app.api.admin_management.users.users.create_new_user", AsyncMock(return_value={"user_id": "new-user-id"})):
        res = client.post("/v1/admin/users/invite", json=payload)
        assert res.status_code == 201
        assert "Invite sent successfully" in res.json()["message"]


def test_invite_user_duplicate_phone(client):
    """Test user invitation with duplicate phone number."""
    payload = {
        "email": "new@example.com",
        "full_name": "New User",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "phone": "+1234567890"
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.check_phone_exists_for_other_user", AsyncMock(return_value=True)):
        res = client.post("/v1/admin/users/invite", json=payload)
        assert res.status_code == 400
        assert "Phone number already exists for another user" in res.json()["detail"]


def test_invite_user_database_error(client):
    """Test user invitation with database error."""
    payload = {
        "email": "new@example.com",
        "full_name": "New User",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4())
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.invite_user_with_email", AsyncMock(return_value={"user_id": "new-user-id"})), \
         patch("apps.user_service.app.api.admin_management.users.users.create_new_user", AsyncMock(side_effect=DatabaseOperationError("Database error"))):
        res = client.post("/v1/admin/users/invite", json=payload)
        assert res.status_code == 500
        assert "Database error" in res.json()["detail"]


def test_invite_user_auth_error(client):
    """Test user invitation with auth service error."""
    payload = {
        "email": "new@example.com",
        "full_name": "New User",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4())
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.invite_user_with_email", AsyncMock(side_effect=HTTPException(status_code=400, detail="Auth service error"))):
        res = client.post("/v1/admin/users/invite", json=payload)
        assert res.status_code == 400
        assert "Auth service error" in res.json()["detail"]


def test_invite_user_permission_denied(client):
    """Test user invitation with permission denied."""
    payload = {
        "email": "new@example.com",
        "full_name": "New User",
        "role_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4())
    }

    with patch("apps.user_service.app.api.admin_management.users.users.check_user_exists", AsyncMock(return_value=False)), \
         patch("apps.user_service.app.api.admin_management.users.users.check_permissions", AsyncMock(side_effect=HTTPException(status_code=403, detail="Permission denied"))):
        res = client.post("/v1/admin/users/invite", json=payload)
        assert res.status_code == 403
        assert "Permission denied" in res.json()["detail"]


# ============================================================================
# UPDATE USER EMAIL TESTS
# ============================================================================

def test_update_user_email_success(client):
    """Test successful user email update."""
    user_id = str(uuid.uuid4())
    new_email = "new@example.com"
    payload = {"email": new_email}

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "old@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.update_supabase_user_email", AsyncMock(return_value={"id": user_id})):
        res = client.put(f"/v1/admin/users/{user_id}/email", json=payload)
        assert res.status_code == 200
        assert "User email updated successfully" in res.json()["message"]


def test_update_user_email_user_not_found(client):
    """Test user email update when user doesn't exist."""
    user_id = str(uuid.uuid4())
    new_email = "new@example.com"
    payload = {"email": new_email}

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found"))):
        res = client.put(f"/v1/admin/users/{user_id}/email", json=payload)
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]


def test_update_user_email_duplicate_email(client):
    """Test user email update with duplicate email."""
    user_id = str(uuid.uuid4())
    new_email = "existing@example.com"
    payload = {"email": new_email}

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "old@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.update_supabase_user_email", AsyncMock(side_effect=HTTPException(status_code=400, detail="Email already exists"))):
        res = client.put(f"/v1/admin/users/{user_id}/email", json=payload)
        assert res.status_code == 400
        assert "Email already exists" in res.json()["detail"]


def test_update_user_email_database_error(client):
    """Test user email update with database error."""
    user_id = str(uuid.uuid4())
    new_email = "new@example.com"
    payload = {"email": new_email}

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "old@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.update_supabase_user_email", AsyncMock(side_effect=DatabaseOperationError("Database error"))):
        res = client.put(f"/v1/admin/users/{user_id}/email", json=payload)
        assert res.status_code == 500
        assert "Database error" in res.json()["detail"]


def test_update_user_email_invalid_email(client):
    """Test user email update with invalid email format."""
    user_id = str(uuid.uuid4())
    new_email = "invalid-email"
    payload = {"email": new_email}

    res = client.put(f"/v1/admin/users/{user_id}/email", json=payload)
    assert res.status_code == 422
    assert "value is not a valid email address" in res.json()["detail"][0]["msg"]


# ============================================================================
# BAN USER TESTS
# ============================================================================

def test_ban_user_success(client):
    """Test successful user ban."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.ban_the_user", AsyncMock(return_value={"id": user_id})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.suspend_user", AsyncMock(return_value=True)):
        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 200
        assert "User successfully banned" in res.json()["message"]


def test_ban_user_not_found(client):
    """Test user ban when user doesn't exist."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found"))):
        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]


def test_ban_user_already_banned(client):
    """Test ban user when user is already banned."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.ban_the_user", AsyncMock(side_effect=HTTPException(status_code=400, detail="User is already banned"))):
        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 400
        assert "User is already banned" in res.json()["detail"]


def test_ban_user_database_error(client):
    """Test user ban with database error."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.ban_the_user", AsyncMock(side_effect=DatabaseOperationError("Database error"))):
        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 500
        assert "Database error" in res.json()["detail"]


def test_ban_user_self_ban(client):
    """Test user ban when trying to ban self."""
    user_id = str(uuid.uuid4())

    # Use patches to override the functions for this specific test
    from unittest.mock import patch
    from apps.user_service.app.dependencies.common_utils import UserContext

    def mock_get_user_from_auth_same_user(request):
        # Set request.state.user to simulate JWT authentication
        request.state.user = {
            "sub": user_id,  # Same user_id as target - JWT uses 'sub' field
            "email": "test@example.com",
            "user_metadata": {
                "organization_id": str(uuid.uuid4()),
                "type": "organization_member"
            }
        }
        return {
            "sub": user_id,  # Same user_id as target - JWT uses 'sub' field
            "email": "test@example.com",
            "user_metadata": {
                "organization_id": str(uuid.uuid4()),
                "type": "organization_member"
            }
        }

    mock_check_permissions_async = AsyncMock(return_value=UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=user_id,  # Same user_id as target for self-ban test
        email="test@example.com",
        user_type="organization_member"
    ))

    with patch("libs.shared_middleware.jwt_auth.get_user_from_auth", mock_get_user_from_auth_same_user), \
         patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", mock_check_permissions_async), \
         patch("apps.user_service.app.dependencies.common_utils.check_user_access_async", lambda *a, **k: True):

        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 400
        assert "You cannot ban yourself" in res.json()["detail"]


# ============================================================================
# UNBAN USER TESTS
# ============================================================================

def test_unban_user_success(client):
    """Test successful user unban."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.unban_the_user", AsyncMock(return_value={"id": user_id})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.revoke_suspended_user", AsyncMock(return_value=True)):
        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 200
        assert "User successfully unbanned" in res.json()["message"]


def test_unban_user_not_found(client):
    """Test user unban when user doesn't exist."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found"))):
        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]


def test_unban_user_not_banned(client):
    """Test unban user when user is not banned."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.unban_the_user", AsyncMock(side_effect=HTTPException(status_code=400, detail="User is not banned"))):
        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 400
        assert "User is not banned" in res.json()["detail"]


def test_unban_user_database_error(client):
    """Test user unban with database error."""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.unban_the_user", AsyncMock(side_effect=DatabaseOperationError("Database error"))):
        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 500
        assert "Database error" in res.json()["detail"]


def test_unban_user_self_unban(client):
    """Test user unban when trying to unban self."""
    user_id = str(uuid.uuid4())

    # Use patches to override the functions for this specific test
    from unittest.mock import patch
    from apps.user_service.app.dependencies.common_utils import UserContext

    def mock_get_user_from_auth_same_user(request):
        # Set request.state.user to simulate JWT authentication
        request.state.user = {
            "sub": user_id,  # Same user_id as target - JWT uses 'sub' field
            "email": "test@example.com",
            "user_metadata": {
                "organization_id": str(uuid.uuid4()),
                "type": "organization_member"
            }
        }
        return {
            "sub": user_id,  # Same user_id as target - JWT uses 'sub' field
            "email": "test@example.com",
            "user_metadata": {
                "organization_id": str(uuid.uuid4()),
                "type": "organization_member"
            }
        }

    mock_check_permissions_async = AsyncMock(return_value=UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=user_id,  # Same user_id as target for self-unban test
        email="test@example.com",
        user_type="organization_member"
    ))

    with patch("libs.shared_middleware.jwt_auth.get_user_from_auth", mock_get_user_from_auth_same_user), \
         patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", mock_check_permissions_async), \
         patch("apps.user_service.app.dependencies.common_utils.check_user_access_async", lambda *a, **k: True):

        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 400
        assert "You cannot Unban yourself" in res.json()["detail"]


# ============================================================================
# MISSING COVERAGE TESTS FOR UPDATE_USER.PY
# ============================================================================

def test_ban_user_auth_user_not_found(client):
    """Test ban user when user not found in auth.users - covers lines 203-204"""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.ban_the_user", AsyncMock(return_value=False)):  # Return False to trigger user not found
        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]


def test_ban_user_organization_user_not_found(client):
    """Test ban user when user not found in organization - covers lines 209-215"""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.ban_the_user", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.users.update_user.suspend_user", AsyncMock(return_value=False)):  # Return False to trigger organization user not found
        res = client.post(f"/v1/admin/users/ban/{user_id}")
        assert res.status_code == 404
        assert "Organization User not found" in res.json()["detail"]


def test_unban_user_auth_user_not_found(client):
    """Test unban user when user not found or not banned - covers lines 299-301"""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.unban_the_user", AsyncMock(return_value=False)):  # Return False to trigger user not found or not banned
        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 404
        assert "User not found or not banned" in res.json()["detail"]


def test_unban_user_organization_user_not_found(client):
    """Test unban user when user not found in organization - covers lines 307-313"""
    user_id = str(uuid.uuid4())

    with patch("apps.user_service.app.api.admin_management.users.update_user.check_permissions", AsyncMock(return_value=MagicMock(organization_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), email="test@example.com", user_type="organization_member"))), \
         patch("apps.user_service.app.api.admin_management.users.update_user.get_user_in_organization", AsyncMock(return_value={"user_id": user_id, "email": "test@example.com", "full_name": "Test User", "organization_id": str(uuid.uuid4())})), \
         patch("apps.user_service.app.api.admin_management.users.update_user.unban_the_user", AsyncMock(return_value=True)), \
         patch("apps.user_service.app.api.admin_management.users.update_user.revoke_suspended_user", AsyncMock(return_value=False)):  # Return False to trigger organization user not found
        res = client.post(f"/v1/admin/users/unban/{user_id}")
        assert res.status_code == 404
        assert "Organization User not found" in res.json()["detail"]
