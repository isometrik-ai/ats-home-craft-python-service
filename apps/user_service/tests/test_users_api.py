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
        })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])):
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
        })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity", AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200  # This endpoint doesn't check user_type
        body = res.json()
        assert body["data"]["email"] == "test@example.com"


def test_get_user_profile_no_organization_linked(client):
    """Test user profile when user is not linked to any organization."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    from types import SimpleNamespace

    mock_user_context = UserContext(
        organization_id=None,
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    # Mock user data from get_user_by_id
    mock_user_data = SimpleNamespace(
        user=SimpleNamespace(
            user_metadata={
                "first_name": "John",
                "last_name": "Doe",
                "full_name": "John Doe",
                "avatar_url": "https://example.com/avatar.jpg",
                "phone": "+1234567890",
                "timezone": "America/New_York"
            }
        )
    )

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context", AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value=None)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id", AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["email"] == "test@example.com"
        assert body["data"]["full_name"] == "John Doe"
        assert body["data"]["first_name"] == "John"
        assert body["data"]["last_name"] == "Doe"
        assert body["data"]["role"]["role_id"] == ""  # No role_id when no organization
        assert body["data"]["role"]["description"] == "No organization assigned"


def test_get_user_profile_no_organization_id(client):
    """Test user profile when user has no organization_id."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=None,
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context", AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id", AsyncMock(return_value={
            "user_id": "u1", "email": "test@example.com", "full_name": "Test User", "first_name": "Test", "last_name": "User", "status": "active",
            "role_id": None, "organization_id": None, "avatar_url": None, "phone": None, "timezone": "UTC",
            "joined_at": None, "last_active_at": None
        })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["email"] == "test@example.com"
        assert body["data"]["role"]["description"] == "No organization assigned"


def test_get_user_profile_no_role_id(client):
    """Test user profile when user has no role_id."""
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
            "role_id": None, "organization_id": str(uuid.uuid4()), "avatar_url": None, "phone": None, "timezone": "UTC",
            "joined_at": None, "last_active_at": None
        })), patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions", AsyncMock(return_value=[])), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity", AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["email"] == "test@example.com"
        assert body["data"]["role"]["role_id"] == ""
        assert body["data"]["role"]["description"] == "No organization assigned"


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
