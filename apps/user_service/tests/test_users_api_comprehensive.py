# pylint: disable=all
"""
Comprehensive Tests for Users Management API Module

This module provides complete test coverage for all user management endpoints.
Includes both unit and integration tests for each essential scenario.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from apps.user_service.app.schemas.users import CreateUserRequest
from apps.user_service.tests.test_utils import (
    FakeCursor,
    FakeConn,
    create_test_app,
    MOCK_USER_ID,
    MOCK_ORG_ID,
    MOCK_ROLE_ID,
    VALID_CURRENT_USER,
    VALID_DB_RESPONSE,
    get_async_db_conn,
    permissions_state,
    set_permission,
    reset_permissions,
    create_mock_db_conn,
)
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_middleware.jwt_auth import get_user_from_auth

MOCK_ADMIN_UUID = str(uuid.uuid4())

# Mock Supabase client before importing
with patch("supabase.create_client") as mock_create_client:
    mock_supabase = MagicMock()
    mock_create_client.return_value = mock_supabase


# Test Configuration and Fixtures
@pytest.fixture
def app():
    """Create test FastAPI application"""
    return create_test_app()


@pytest.fixture
def client(app):
    """Test client with overridden dependencies for integration tests"""
    test_client = TestClient(app)
    test_client.headers = {
        "Authorization": f"Bearer {MOCK_ADMIN_UUID}",
        "Content-Type": "application/json",
    }
    return test_client


@pytest.fixture
def fake_cursor():
    """Create fake database cursor"""
    return FakeCursor()


@pytest.fixture
def fake_conn(fake_cursor):
    """Create fake database connection"""
    return FakeConn(fake_cursor)


@pytest.fixture
def mock_request():
    """Create mock request object"""
    return Mock()


@pytest.fixture
def valid_current_user():
    """Create valid current user data"""
    return VALID_CURRENT_USER.copy()


@pytest.fixture
def mock_db_conn():
    """Mock async database connection"""
    return create_mock_db_conn()


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Setup and teardown for each test"""
    # No longer need to reset permissions since we use patch
    yield


class TestGetUserProfileEssential:
    """Essential test cases for get_user_profile endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful profile retrieval via HTTP"""
        # Setup complete profile data with proper datetime fields

        profile_data = {
            "user_id": MOCK_USER_ID,
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": "1234567890",
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.utcnow(),  # ✅ NOT a string
            "last_active_at": datetime.utcnow(),  # ✅ NOT a string
            "organization_id": MOCK_ORG_ID,
            "role_id": MOCK_ROLE_ID,
            "role_name": "Admin",
            "role_description": "Administrator",
        }

        fake_cursor.fetchone_data = profile_data
        fake_cursor.fetchall_data = []  # Permissions will be handled by FakeConn
        fake_cursor.fetchrow = profile_data
        # fake_cursor.fetchrow = profile_data

        # Override dependency
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/users/profile")  # ✅ correct path
        print("will it work")
        print(response)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["email"] == "test@example.com"  # Matches JWT token

    def test_2_invalid_token_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 2: Invalid token data via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Override JWT auth to return invalid user
        app.dependency_overrides[get_user_from_auth] = lambda: {
            "sub": "",
            "user_metadata": {},
        }

        response = client.get("/v1/admin/users/profile")

        assert response.status_code in [
            400,
            500,
        ]  # Could be either depending on validation

    def test_3_user_not_found_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 3: User not found via HTTP"""
        fake_cursor.fetchone_data = None  # User not found

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/users/profile")

        assert response.status_code == 404

    def test_4_email_mismatch_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: Email mismatch via HTTP"""
        profile_data = {
            "member_id": "member-123",
            "user_id": MOCK_USER_ID,
            "organization_id": MOCK_ORG_ID,
            "email": "different@example.com",  # Different from token email
            "full_name": "Test User",
            "avatar_url": None,
            "phone": "+1234567890",
            "timezone": "UTC",
            "status": "active",
            "role_id": MOCK_ROLE_ID,
            "role_name": "Administrator",
        }

        fake_cursor.fetchone_data = profile_data

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/users/profile")

        assert response.status_code == 403

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        fake_conn.should_fail_query = True

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/users/profile")

        assert response.status_code == 500


class TestGetUsersListEssential:
    """Essential test cases for get_users_list endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful users list retrieval via HTTP"""
        set_permission("USERS_LIST", True)

        users_data = [
            {
                "user_id": MOCK_USER_ID,  # UUID or str
                "email": "user1@example.com",
                "full_name": "User One",
                "first_name": "User",
                "last_name": "One",
                "phone": "1234567890",
                "role_name": "Admin",
                "role_id": MOCK_ROLE_ID,  # UUID or str
                "status": "active",
                "joined_at": datetime.utcnow(),
                "last_active_at": datetime.utcnow(),
            }
        ]

        fake_cursor.fetchall_data = users_data
        fake_cursor.fetchone_data = {"count": 1}
        fake_cursor.fetch = users_data
        fake_cursor.fetchrow = {"count": 1}
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/list")

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Users retrieved successfully"
            assert len(data["data"]) == 1

    def test_2_invalid_pagination_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 2: Invalid pagination via HTTP"""
        set_permission("USERS_LIST", True)

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass so we hit validation error
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/list?page=-1")

            assert response.status_code in [400, 422]  # Validation error

    def test_2a_invalid_page_size_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 2a: Invalid page_size via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Test page_size = 0
        response = client.get("/v1/admin/users/list?page=1&page_size=0")
        assert response.status_code == 422

        # Test page_size > 100
        response = client.get("/v1/admin/users/list?page=1&page_size=101")
        assert response.status_code == 422

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        set_permission("USERS_LIST", False)

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/users/list")

        assert response.status_code == 403

    def test_4_empty_list_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: Empty users list via HTTP"""
        set_permission("USERS_LIST", True)

        fake_cursor.fetchall_data = []
        fake_cursor.fetchone_data = {"count": 0}

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/list")

            assert response.status_code == 200
            data = response.json()
            assert data["total_count"] == 0
            assert len(data["data"]) == 0

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        fake_conn.should_fail_query = True

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass, then hit database error

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/list")

            assert response.status_code == 500


class TestCreateUserEssential:
    """Essential test cases for create_user endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful user creation via HTTP"""
        set_permission("USERS_CREATE", True)
        fake_cursor.fetchone_data = None  # User doesn't exist yet
        fake_conn.users_exist = False  # Ensure user doesn't exist for creation

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            request_data = {
                "email": "newuser@example.com",
                "full_name": "New User",
                "role_id": str(uuid.uuid4()),
                "organization_id": MOCK_ORG_ID,  # Add required field
                "phone": "+1234567890",
                "timezone": "UTC",
            }

            response = client.post("/v1/admin/users", json=request_data)

            assert response.status_code == 201
        data = response.json()
        assert "successfully" in data["message"]

    def test_2_invalid_email_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 2: Invalid email format via HTTP"""
        set_permission("USERS_CREATE", True)

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass so we hit validation error
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            request_data = {
                "email": "invalid-email",  # Invalid email
                "full_name": "Test User",
                "role_id": str(uuid.uuid4()),
                "organization_id": MOCK_ORG_ID,
            }

            response = client.post("/v1/admin/users", json=request_data)

            assert response.status_code == 422  # Validation error

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        # Important: Set fake data to None so user doesn't exist (avoids 409 conflict)
        fake_cursor.fetchone_data = None

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to mock the permission check function
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            request_data = {
                "email": "test@example.com",
                "full_name": "Test User",
                "role_id": str(uuid.uuid4()),
                "organization_id": MOCK_ORG_ID,  # Add required field
            }

            response = client.post("/v1/admin/users", json=request_data)

            assert response.status_code == 403

    def test_4_user_exists_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: User already exists via HTTP"""
        set_permission("USERS_CREATE", True)
        fake_cursor.fetchone_data = {"user_id": uuid.uuid4()}  # User exists
        fake_conn.users_exist = True  # Set this flag to simulate user exists

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass, then test user exists conflict
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            request_data = {
                "email": "existing@example.com",
                "full_name": "Existing User",
                "role_id": str(uuid.uuid4()),
                "organization_id": MOCK_ORG_ID,  # Add required field
            }

            response = client.post("/v1/admin/users", json=request_data)

            assert response.status_code == 409
            data = response.json()
            assert "already exists" in data["detail"]

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        # Set user doesn't exist initially (to pass the existence check)
        fake_cursor.fetchone_data = None
        fake_conn.users_exist = False  # User doesn't exist (passes existence check)
        # But then fail on insert
        fake_conn.should_fail_insert = True

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass, then hit database error
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            request_data = {
                "email": "test@example.com",
                "full_name": "Test User",
                "role_id": str(uuid.uuid4()),
                "organization_id": MOCK_ORG_ID,  # Add required field
            }

            response = client.post("/v1/admin/users", json=request_data)

            assert response.status_code == 500


class TestDeleteUserEssential:
    """Essential test cases for delete_user endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful user deletion via HTTP"""
        set_permission("USERS_DELETE", True)

        # Mock complete user data that matches the fetch_user_profile query structure
        mock_user_data = {
            "user_id": MOCK_USER_ID,
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": "1234567890",
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.utcnow(),
            "last_active_at": datetime.utcnow(),
            "organization_id": MOCK_ORG_ID,
            "role_id": MOCK_ROLE_ID,
            "role_name": "Administrator",
        }

        fake_cursor.fetchone_data = mock_user_data  # User found
        fake_conn.should_return_delete_result = True  # Ensure delete succeeds

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Mock Supabase client
        mock_supabase = Mock()
        mock_supabase.auth.admin.delete_user = Mock()

        app.dependency_overrides[get_supabase_admin_client] = lambda: mock_supabase

        # Use patch to ensure permissions pass, then test successful delete
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.delete(f"/v1/admin/users/delete/{MOCK_USER_ID}")

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "User removed successfully"

    def test_2_invalid_user_id_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 2: Invalid user ID format via HTTP"""
        fake_cursor.fetchone_data = None  # No user found
        fake_conn.should_return_delete_result = False  # User not found

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass, then test user not found
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.delete("/v1/admin/users/delete/invalid-uuid")

            # Since the function doesn't validate UUID format, it passes to DB and returns 404
            assert response.status_code == 404

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        # Reset any existing fake data
        fake_cursor.fetchone_data = {"user_id": MOCK_USER_ID}  # User exists
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to mock insufficient permissions
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            response = client.delete(f"/v1/admin/users/delete/{MOCK_USER_ID}")

            assert response.status_code == 403

    def test_4_user_not_found_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: User not found via HTTP"""
        fake_cursor.fetchone_data = None  # User not found
        fake_conn.should_return_delete_result = False  # User not found

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn
        app.dependency_overrides[get_supabase_admin_client] = (
            lambda: None
        )  # Mock Supabase

        # Use patch to ensure permissions pass, then test user not found
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.delete(f"/v1/admin/users/delete/{MOCK_USER_ID}")

            assert response.status_code == 404

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        # Set up to fail on database operations
        fake_conn.should_fail_query = True

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn
        app.dependency_overrides[get_supabase_admin_client] = (
            lambda: None
        )  # Mock Supabase

        # Use patch to ensure permissions pass, then hit database error
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.delete(f"/v1/admin/users/delete/{MOCK_USER_ID}")

            assert response.status_code == 500


class TestGetUserByIdEssential:
    """Essential test cases for get_user_by_id endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful get user by ID via HTTP"""

        profile_data = {
            "user_id": "target-user-id",
            "organization_id": MOCK_ORG_ID,
            "email": "target@example.com",
            "full_name": "Target User",
            "first_name": "Target",
            "last_name": "User",
            "avatar_url": None,
            "phone": "+1234567890",
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.utcnow(),
            "last_active_at": datetime.utcnow(),
            "role_id": MOCK_ROLE_ID,
            "role_name": "Administrator",
        }
        fake_conn.query_responses = {
            "organization_members": profile_data,
            "roles AND name AND organization_id": None,  # or expected mock return
        }
        fake_cursor.fetchone_data = profile_data
        fake_conn.query_responses = {
            "organization_members om inner join public.roles r": profile_data
        }
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/target-user-id")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["email"] == "target@example.com"

    def test_2_invalid_token_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 2: Invalid token data via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn
        app.dependency_overrides[get_user_from_auth] = lambda: {
            "sub": "",
            "user_metadata": {},
        }

        response = client.get("/v1/admin/users/test-user-id")
        assert response.status_code in [400, 500]

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            response = client.get("/v1/admin/users/test-user-id")

        assert response.status_code == 403

    def test_4_user_not_found_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: User not found via HTTP"""
        fake_cursor.fetchone_data = None  # User not found
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/nonexistent-user-id")

        assert response.status_code == 404

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        fake_conn.should_fail_query = True
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.get("/v1/admin/users/test-user-id")

        assert response.status_code == 500


class TestUpdateUserEssential:
    """Essential test cases for update_user endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful user update via HTTP"""

        # Mock user data that matches the fetch_user_profile query structure
        mock_user_data = {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": "1234567890",
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.utcnow(),
            "last_active_at": datetime.utcnow(),
            "organization_id": MOCK_ORG_ID,
            "role_id": MOCK_ROLE_ID,
            "role_name": "Administrator",
        }

        # Set up fake_conn to return the user data for both calls
        fake_conn.cursor.fetchone_data = mock_user_data

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {
            "full_name": "Updated Name",
            "phone": "+9876543210",
            "timezone": "PST",
        }

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ), patch(
            "apps.user_service.app.api.admin_management.users.users.phone_exists_for_other_user",
            return_value=False,  # No duplicate phone number found
        ):
            response = client.put(
                "/v1/admin/users/update/test-user-id", json=request_data
            )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "User updated successfully"

    def test_2_no_fields_to_update_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 2: No fields to update via HTTP"""
        # Set up fake_conn to return a user so the endpoint can proceed to validation
        mock_user = {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "phone": "1234567890",
            "timezone": "UTC",
            "avatar_url": None,
            "status": "active",
            "role_id": MOCK_ROLE_ID,
            "organization_id": MOCK_ORG_ID,
        }
        fake_cursor.fetchone_data = mock_user

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {}  # Empty request

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.put(
                "/v1/admin/users/update/test-user-id", json=request_data
            )

        assert response.status_code == 400

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {"full_name": "Updated Name"}

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            response = client.put(
                "/v1/admin/users/update/test-user-id", json=request_data
            )

        assert response.status_code == 403

    def test_4_user_not_found_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: User not found via HTTP"""
        fake_cursor.fetchone_data = None  # User not found
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {"full_name": "Updated Name"}

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.put(
                "/v1/admin/users/update/nonexistent-user-id", json=request_data
            )

        assert response.status_code == 404

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        fake_conn.should_fail_query = True
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {"full_name": "Updated Name"}

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.put(
                "/v1/admin/users/update/test-user-id", json=request_data
            )

        assert response.status_code == 500


class TestInviteUserEssential:
    """Essential test cases for invite_user endpoint (5 core scenarios)"""

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful user invitation via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Mock Supabase client
        mock_supabase = Mock()
        mock_supabase.auth.admin.invite_user_by_email.return_value = Mock(
            user=Mock(id="new-user-id")
        )
        app.dependency_overrides[get_supabase_admin_client] = lambda: mock_supabase

        request_data = {
            "email": "newuser@example.com",
            "full_name": "New User",
            "phone": "+1234567890",
            "role_id": MOCK_ROLE_ID,
            "organization_id": MOCK_ORG_ID,
        }

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.post("/v1/admin/users/invite", json=request_data)

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "Invite sent successfully"

    def test_2_invalid_token_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 2: Invalid token data via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn
        app.dependency_overrides[get_user_from_auth] = lambda: {
            "sub": "",
            "user_metadata": {},
        }

        request_data = {
            "email": "newuser@example.com",
            "full_name": "New User",
            "role_id": MOCK_ROLE_ID,
            "organization_id": MOCK_ORG_ID,
        }

        response = client.post("/v1/admin/users/invite", json=request_data)
        assert response.status_code in [400, 500]

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {
            "email": "newuser@example.com",
            "full_name": "New User",
            "role_id": MOCK_ROLE_ID,
            "organization_id": MOCK_ORG_ID,
        }

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            response = client.post("/v1/admin/users/invite", json=request_data)

        assert response.status_code == 403

    def test_4_supabase_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: Supabase invitation error via HTTP"""
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Mock Supabase to fail
        mock_supabase = Mock()
        mock_supabase.auth.admin.invite_user_by_email.side_effect = Exception(
            "Supabase error"
        )
        app.dependency_overrides[get_supabase_admin_client] = lambda: mock_supabase

        request_data = {
            "email": "newuser@example.com",
            "full_name": "New User",
            "role_id": MOCK_ROLE_ID,
            "organization_id": MOCK_ORG_ID,
        }

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            # The endpoint properly handles Supabase errors and returns HTTP response
            response = client.post("/v1/admin/users/invite", json=request_data)
            
            # Should return 409 status code with the error message
            assert response.status_code == 409
            data = response.json()
            assert "Supabase error" in data["detail"]

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        # Set up fake_conn to fail on execute (which is used for INSERT)
        fake_conn.should_fail_insert = True
        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Mock Supabase to work normally
        mock_supabase = Mock()
        mock_supabase.auth.admin.invite_user_by_email.return_value = Mock(
            user=Mock(id="new-user-id")
        )
        app.dependency_overrides[get_supabase_admin_client] = lambda: mock_supabase

        request_data = {
            "email": "newuser@example.com",
            "full_name": "New User",
            "role_id": MOCK_ROLE_ID,
            "organization_id": MOCK_ORG_ID,
        }

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.post("/v1/admin/users/invite", json=request_data)

        assert response.status_code == 500
