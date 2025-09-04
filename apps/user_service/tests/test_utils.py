# pylint: disable=all
"""
Shared Test Utilities

This module contains shared classes, fixtures, and helper functions for testing.
Eliminates repetitive code across test files for roles and users APIs.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

from datetime import datetime
import uuid
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from fastapi import FastAPI, APIRouter
from fastapi.exceptions import HTTPException
from fastapi.testclient import TestClient
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.user_service.app.api.admin_management.users.users import router as users_router
from apps.user_service.app.api.admin_management.roles import router as roles_router
from apps.user_service.app.dependencies.exception_middleware import unified_exception_handler
from libs.shared_middleware.jwt_auth import get_user_from_auth, check_user_access_async
from libs.shared_db.postgres_db.db import get_async_db_conn as real_get_async_db_conn
from libs.shared_db.supabase_db.db import (
    get_supabase_admin_client as real_get_supabase_admin_client
)

# Mock data constants
MOCK_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
MOCK_ORG_ID = "123e4567-e89b-12d3-a456-426614174000"
MOCK_ROLE_ID = "987fcdeb-51a3-4def-9876-543210987654"

# Valid test data
VALID_CURRENT_USER = {
    "sub": MOCK_USER_ID,
    "email": "test@example.com",
    "user_metadata": {"organization_id": MOCK_ORG_ID, "type": "organization_member"},
}

VALID_DB_RESPONSE = {
    "id": MOCK_ROLE_ID,
    "name": "Test Role",
    "description": "Test Description",
    "is_default": False,
    "created_at": "2024-12-19T10:00:00Z",
    "updated_at": "2024-12-19T10:00:00Z",
}


class FakeCursor:
    """
    Fake database cursor for testing

    Simulates database operations with configurable responses
    """

    def __init__(self):
        self.fetchone_data = None
        self.fetchall_data = []
        self.execute_calls = []
        self.should_fail = False

    def execute(self, query, params=None):
        """Mock execute method"""
        self.execute_calls.append((query, params))
        if self.should_fail:
            raise RuntimeError("Database error")

    def fetchone(self):
        """Mock fetchone method"""
        if self.should_fail:
            raise RuntimeError("Database error")
        return self.fetchone_data

    def fetchall(self):
        """Mock fetchall method"""
        if self.should_fail:
            raise RuntimeError("Database error")
        return self.fetchall_data


class FakeConn:
    """Fake database connection for testing"""

    def __init__(self, cursor):
        self.cursor = cursor
        self.query_calls = []

        # Control flags for different behaviors
        self.should_fail_query = False
        self.should_fail_data_fetch = False
        self.should_fail_insert = False
        self.should_fail_update = False
        self.should_fail_delete = False
        self.users_exist = False
        self.should_return_delete_result = True

        # Additional tracking lists
        self.fetch_calls = []
        self.execute_calls = []

        # RST template
        self.template_exists = False
        self.last_inserted_template_id = None

        # Enhanced query response system
        self.query_responses = (
            {}
        )  # Store different responses for different query patterns

    def set_query_response(self, pattern, response):
        """Set a specific response for queries matching a pattern"""
        self.query_responses[pattern] = response

    def clear_query_responses(self):
        """Clear all query-specific responses"""
        self.query_responses = {}

    async def fetchrow(self, query, *args):
        """Mock fetchrow method that handles different query types"""
        if self.should_fail_query:
            raise HTTPException(status_code=500, detail="Simulated DB failure")

        query_lower = query.lower().strip()

        # Debug logging
        print(f"FakeConn.fetchrow called with query: {query_lower[:100]}...")
        print(f"Available query patterns: {list(self.query_responses.keys())}")

        if "from rst_templates" in query_lower and "where name" in query_lower:
            if getattr(self, "template_exists", False):
                return {"id": str(uuid.uuid4())}  # simulate found duplicate
            return None  # no duplicate

        # Handle insert for rst_templates
        if "insert into rst_templates" in query_lower and "returning" in query_lower:
            if getattr(self, "should_fail_insert", False):
                raise RuntimeError("Database insert failed")
            new_id = str(uuid.uuid4())
            self.last_inserted_template_id = new_id
            return {"id": new_id}

        # Check for specific query responses first
        for pattern, response in self.query_responses.items():
            if pattern.lower() in query_lower:
                print(f"Pattern '{pattern}' matched, returning: {response}")
                return response

        # Handle INSERT queries with should_fail_insert
        if "INSERT INTO" in query and self.should_fail_insert:
            raise HTTPException(status_code=500, detail="Database insert failed")
            # raise Exception("Database insert failed")

        # Handle INSERT queries for role creation (if not failing)
        if (
            "INSERT INTO" in query
            and "roles" in query
            and "RETURNING" in query
            and not self.should_fail_insert
        ):
            return {
                "id": MOCK_ROLE_ID,
                "name": "Test Role",
                "description": "Test Description",
                "is_default": False,
                "created_at": "2024-12-19T10:00:00",
                "updated_at": "2024-12-19T10:00:00",
            }

        # Handle role usage check queries (for delete role)
        if "count(*) as member_count" in query_lower or "member_count" in query_lower:
            if self.cursor.fetchall_data and len(self.cursor.fetchall_data) > 0:
                # Role is in use
                return {
                    "member_count": self.cursor.fetchall_data[0].get("member_count", 5)
                }
            else:
                # Role not in use
                return {"member_count": 0}

        # Handle permission validation queries (for create/update role)
        if "count(*) as valid_count" in query_lower or "valid_count" in query_lower:
            if (
                self.cursor.fetchone_data
                and isinstance(self.cursor.fetchone_data, dict)
                and "valid_count" in self.cursor.fetchone_data
            ):
                return self.cursor.fetchone_data
            else:
                # Default valid permission count for testing
                return {"valid_count": 2}

        # Handle role existence checks for name conflicts
        if "roles" in query and "name" in query and "organization_id" in query:
            print("Executing A")
            if self.cursor.fetchone_data is not None:
                return self.cursor.fetchone_data  # Role exists (will cause conflict)
            return None  # Role doesn't exist

        # Handle role existence checks for updates/deletes
        if "roles" in query and "id = $1 AND organization_id = $2" in query:
            ("Executing B")
            if self.cursor.fetchone_data is not None:
                return self.cursor.fetchone_data  # Role exists
            return None  # Role doesn't exist

        # Handle user profile queries for get_user_profile
        if (
            "organization_members" in query
            and "roles" in query
            and "om.user_id = $1" in query
        ):
            if self.cursor.fetchone_data is None:
                print("returning none")
                return None  # User not found

            # Use cursor data if provided and has required profile fields, otherwise use default


            if (
                hasattr(self.cursor.fetchone_data, "get")
                and isinstance(self.cursor.fetchone_data, dict)
                and "role_id" in self.cursor.fetchone_data
                and "email" in self.cursor.fetchone_data
            ):

                profile_data = self.cursor.fetchone_data.copy()

                # Convert string dates to datetime objects if needed
                for date_field in ["joined_at", "last_active_at"]:
                    if date_field in profile_data and isinstance(
                        profile_data[date_field], str
                    ):
                        try:
                            profile_data[date_field] = datetime.fromisoformat(
                                profile_data[date_field].replace("Z", "")
                            )
                        except (ValueError, AttributeError):
                            profile_data[date_field] = datetime.fromisoformat(
                                "2024-12-19T10:00:00"
                            )

                return profile_data

            # Return default profile data that matches the JWT token
            return {
                "member_id": "member-123",
                "user_id": MOCK_USER_ID,
                "organization_id": MOCK_ORG_ID,
                "email": "test@example.com",  # Must match JWT token email
                "full_name": "Test User",
                "avatar_url": None,
                "phone": "+1234567890",
                "timezone": "UTC",
                "status": "active",
                "joined_at": datetime.fromisoformat("2024-12-19T10:00:00"),
                "last_active_at": datetime.fromisoformat("2024-12-19T15:30:00"),
                "role_id": MOCK_ROLE_ID,
                "role_name": "Administrator",
                "role_description": "Full access",
            }

        # Default behavior - return whatever is set in cursor
        return self.cursor.fetchone_data

    async def fetch(self, query, *params):
        """Mock async fetch method"""
        self.fetch_calls.append((query, params))

        if self.should_fail_data_fetch:
            raise RuntimeError("Data fetch database error")

        if self.should_fail_query:
            raise HTTPException(status_code=500, detail="Simulated DB failure")

        # Handle get_users_list query specifically
        if "organization_members" in query and "roles" in query and "ORDER BY" in query:
            # Check if cursor has explicit empty data set
            if (
                self.cursor.fetchall_data is not None
                and len(self.cursor.fetchall_data) == 0
            ):
                return []  # Return empty list as set by test
            # Return sample user data to prevent index error and with proper datetime handling

            return [
                {
                    "user_id": "user1",
                    "email": "user1@test.com",
                    "full_name": "User One",
                    "first_name": "User",
                    "last_name": "One",
                    "phone": "1234567890",
                    "status": "active",
                    "joined_at": datetime.fromisoformat("2024-12-19T10:00:00"),
                    "last_active_at": datetime.fromisoformat("2024-12-19T15:30:00"),
                    "role_name": "Admin",
                    "role_id": "role1",
                }
            ]

        # Handle permissions queries for get_user_profile
        if (
            "role_permissions" in query
            and "permissions" in query
            and "WHERE rp.role_id = $1" in query
        ):
            return [
                {
                    "permission_id": "perm-123",
                    "permission_code": "users.manage",
                    "permission_name": "Manage Users",
                    "permission_description": "Can manage users",
                    "category": "users",
                }
            ]

        return self.cursor.fetchall_data

    async def execute(self, query, *params):
        """Mock async execute method"""
        self.execute_calls.append((query, params))

        if self.should_fail_insert and "INSERT" in query:
            raise HTTPException(status_code=500, detail="Database insert failed")
            # raise Exception("Database insert failed")

        if self.should_fail_update and "UPDATE" in query:
            raise RuntimeError("Database update failed")

        if self.should_fail_delete and "DELETE" in query:
            raise RuntimeError("Database delete failed")

        # For DELETE operations, simulate the return value
        if "DELETE" in query:
            if self.cursor.fetchone_data is None:
                return "DELETE 0"  # No rows deleted
            else:
                return "DELETE 1"  # One row deleted

        return "EXECUTE_SUCCESS"

    async def executemany(self, query, params_list):
        """Mock async executemany method"""
        self.execute_calls.append((query, params_list))

        if self.should_fail_insert and "INSERT" in query:
            raise RuntimeError("Database bulk insert failed")

    def transaction(self):
        """Mock transaction context manager"""
        return AsyncTransactionMock()


class AsyncTransactionMock:
    """Mock async transaction context manager"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# Permission state management for testing
permissions_state = {}


def set_permission(permission_name, value):
    """Set a permission for testing"""
    permissions_state[permission_name] = value


def get_permission(permission_name):
    """Get a permission value for testing"""
    return permissions_state.get(permission_name, True)


def reset_permissions():
    """Reset all permissions to default state"""
    permissions_state.clear()


# Mock dependency functions
async def get_async_db_conn():
    """Mock async database connection dependency"""
    return AsyncMock()


def get_supabase_admin_client():
    """Mock Supabase admin client dependency"""

    class MockAuth:
        class MockAdmin:
            async def delete_user(self, user_id):
                return {"message": "User deleted"}

            async def invite_user_by_email(self, email, options=None):
                class MockUser:
                    def __init__(self):
                        self.id = str(uuid.uuid4())

                class MockResponse:
                    def __init__(self):
                        self.user = MockUser()

                return MockResponse()

        def __init__(self):
            self.admin = self.MockAdmin()

    class MockSupabaseClient:
        def __init__(self):
            self.auth = MockAuth()

    return MockSupabaseClient()


# def get_user_from_auth():
#     """Mock JWT auth dependency"""
#     return VALID_CURRENT_USER


# Test app creation utilities
def create_test_app():
    """Create a test FastAPI application with mocked dependencies"""
    app = FastAPI()

    # Include routers and add exception handlers

    app.add_exception_handler(Exception, unified_exception_handler)
    app.add_exception_handler(StarletteHTTPException, unified_exception_handler)
    app.add_exception_handler(RequestValidationError, unified_exception_handler)
    app.add_exception_handler(HTTPException, unified_exception_handler)

    admin_router = APIRouter(prefix="/v1/admin")
    admin_router.include_router(users_router)
    admin_router.include_router(roles_router)

    app.include_router(admin_router)

    # Override dependencies after app is set up

    # Override JWT auth to return valid user
    app.dependency_overrides[get_user_from_auth] = lambda: VALID_CURRENT_USER

    # Override permission check function
    app.dependency_overrides[check_user_access_async] = mock_check_user_access_async

    # Override database connection
    app.dependency_overrides[real_get_async_db_conn] = get_async_db_conn

    # Override Supabase client
    app.dependency_overrides[real_get_supabase_admin_client] = get_supabase_admin_client

    return app


# Mock helper functions for creating test data
def create_mock_user_data(email, full_name):
    """Create mock user data for testing"""
    return {
        "user_id": MOCK_USER_ID,
        "organization_id": MOCK_ORG_ID,
        "email": email,
        "full_name": full_name,
        "avatar_url": None,
        "phone": "+1234567890",
        "timezone": "UTC",
        "status": "active",
        "joined_at": "2024-12-19T10:00:00Z",
        "last_active_at": "2024-12-19T15:30:00Z",
        "role_id": MOCK_ROLE_ID,
        "role_name": "Test Role",
        "role_description": "Test Description",
    }


def create_mock_permission_data(name, code):
    """Create mock permission data for testing"""
    return {
        "permission_id": str(uuid.uuid4()),
        "permission_name": name,
        "permission_code": code,
        "category": "test",
    }


def create_mock_role_data(name, description="Test Role"):
    """Create mock role data for testing"""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "description": description,
        "is_default": False,
        "created_at": "2024-12-19T10:00:00Z",
        "updated_at": "2024-12-19T10:00:00Z",
        "user_count": 0,
        "permission_count": 0,
        "permission_categories": {},
    }


# Enhanced mocking with proper permission handling
def setup_permission_check_mock(has_permission=True):
    """Setup mock for permission checking"""

    def mock_check_permission(*args, **kwargs):
        # permission_code = kwargs.get("permission_code", "")  # Not used in this mock
        if "USERS_CREATE" in permissions_state:
            return permissions_state["USERS_CREATE"]
        if "USERS_DELETE" in permissions_state:
            return permissions_state["USERS_DELETE"]
        if "USERS_LIST" in permissions_state:
            return permissions_state["USERS_LIST"]
        return has_permission

    return mock_check_permission


# Mock permission check function
async def mock_check_user_access_async(
    permission_code=None, user_id=None, organisation_id=None, db_conn=None
):
    """Mock check_user_access_async that respects permission state"""
    # Map permission codes to our test state
    if permission_code == "settings.users.manage":
        # For users, check for any user permission that's been set to False
        for perm in ["USERS_CREATE", "USERS_DELETE", "USERS_LIST"]:
            if perm in permissions_state and not permissions_state[perm]:
                return False
        # If any user permission is explicitly set to True, return True
        for perm in ["USERS_CREATE", "USERS_DELETE", "USERS_LIST"]:
            if perm in permissions_state and permissions_state[perm]:
                return True

    if permission_code == "settings.roles.manage":
        # For roles, check for any role permission that's been set to False
        for perm in ["ROLES_CREATE", "ROLES_DELETE", "ROLES_LIST"]:
            if perm in permissions_state and not permissions_state[perm]:
                return False
        # If any role permission is explicitly set to True, return True
        for perm in ["ROLES_CREATE", "ROLES_DELETE", "ROLES_LIST"]:
            if perm in permissions_state and permissions_state[perm]:
                return True

    # Default to True if no specific permission set
    return True


def create_mock_db_conn():
    """Create a properly configured mock database connection"""

    mock_conn = AsyncMock()

    # Create a mock transaction that can be used as async context manager
    mock_transaction = AsyncMock()
    # Set up the context manager methods directly
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)

    # Make transaction() return our mock transaction
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    # Mock other database methods
    mock_conn.fetchrow.return_value = None
    mock_conn.fetch.return_value = []
    mock_conn.execute.return_value = "UPDATE 1"
    mock_conn.executemany.return_value = None

    return mock_conn
