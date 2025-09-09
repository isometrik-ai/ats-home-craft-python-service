"""
Comprehensive Test Suite for Get Roles API endpoint

This module contains 5 essential test cases that provide complete code coverage for the get_roles endpoint:
1. Success case with default parameters (happy path)
2. Invalid role_type parameter (input validation)
3. Insufficient permissions (authorization)
4. Database error (exception handling)
5. Search functionality with filters (query parameter handling)

Both unit and integration testing approaches are included.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

# pylint: disable=all
# flake8: noqa
# type: ignore

import pytest
import uuid
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from typing import Optional

# Mock Supabase client before importing any modules that use it
with patch("supabase.create_client") as mock_create_client:
    mock_supabase = MagicMock()
    mock_create_client.return_value = mock_supabase

    # Import after mocking
    from apps.user_service.app.api.admin_management.roles import (
        router as roles_router,
        get_roles,
    )
    from libs.shared_middleware.jwt_auth import (
        get_user_from_auth,
        check_user_access_async,
    )
    from libs.shared_db.postgres_db.db import get_async_db_conn
    from apps.user_service.app.schemas.admin_access_management import RoleQueryParams

# Test data constants
MOCK_ADMIN_UUID = str(uuid.uuid4())
MOCK_ORG_ID = "123e4567-e89b-12d3-a456-426614174000"

# Permission mapping for testing
PERMISSION_MAPPING = {"ROLES_READ": True}

# Sample test data
TEST_ROLES_DATA = [
    {
        "id": uuid.uuid4(),
        "name": "Admin",
        "description": "Administrator role",
        "is_default": True,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),  # Add missing updated_at field
        "user_count": 5,
        "permission_count": 10,
        "permission_categories": '{"settings": 3, "users": 2, "content": 5}',
    },
    {
        "id": uuid.uuid4(),
        "name": "Editor",
        "description": "Content editor role",
        "is_default": False,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),  # Add missing updated_at field
        "user_count": 12,
        "permission_count": 7,
        "permission_categories": '{"content": 5, "media": 2}',
    },
]


# Helper classes for database mocking
class FakeCursor:
    """Mock database cursor for integration tests"""

    def __init__(self, fetchall_data=None, fetchone_data=None, error=None):
        self.fetchall_data = fetchall_data or []
        self.fetchone_data = fetchone_data
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass


class FakeConn:
    """Mock database connection for integration tests"""

    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.should_fail_permission = False
        self.should_fail_data_fetch = False

    async def fetchrow(self, query, *args):
        """Mock fetchrow for async database operations"""
        if "has_permission" in str(query) or "EXISTS" in str(query):
            if self.should_fail_permission:
                raise RuntimeError("Permission check database error")
            return {"has_permission": PERMISSION_MAPPING.get("ROLES_READ", True)}

        if "COUNT(*)" in str(query) or "total_count" in str(query):
            if self.should_fail_data_fetch:
                raise RuntimeError("Count query database error")
            return self.cursor_obj.fetchone_data

        return self.cursor_obj.fetchone_data

    async def fetch(self, query, *args):
        """Mock fetch for async database operations"""
        if self.should_fail_data_fetch:
            raise RuntimeError("Data fetch database error")
        return self.cursor_obj.fetchall_data


# Mock dependencies
async def mock_check_user_access_async(
    permission_code=None, user_id=None, organisation_id=None, db_conn=None
):
    """Mock check_user_access_async function"""
    if (
        db_conn
        and hasattr(db_conn, "should_fail_permission")
        and db_conn.should_fail_permission
    ):
        raise RuntimeError("Permission check database error")

    if permission_code == "settings.roles.manage":
        return PERMISSION_MAPPING.get("ROLES_READ", True)
    return False


# Class to control authentication behavior without global variables
class MockAuthController:
    """Controller for mock authentication behavior"""
    def __init__(self):
        self.should_fail = False

    def set_auth_failure(self, should_fail: bool):
        """Control whether authentication should fail"""
        self.should_fail = should_fail

    def mock_get_user_from_auth_conditional(self):
        """Mock that can be configured to fail authentication"""
        if self.should_fail:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
        return {
            "sub": MOCK_ADMIN_UUID,
            "email": "admin@example.com",
            "user_metadata": {"organization_id": MOCK_ORG_ID, "type": "organization_member"},
        }


# Global instance for backward compatibility
_mock_auth_controller = MockAuthController()

def mock_get_user_from_auth_conditional():
    """Mock that can be configured to fail authentication"""
    return _mock_auth_controller.mock_get_user_from_auth_conditional()


def set_mock_auth_failure(should_fail: bool):
    """Control whether authentication should fail"""
    _mock_auth_controller.set_auth_failure(should_fail)


# Create test app for integration tests
def create_test_app():
    app = FastAPI()
    app.include_router(roles_router, prefix="/v1/admin")
    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth_conditional
    app.dependency_overrides[check_user_access_async] = mock_check_user_access_async
    return app


app_instance = create_test_app()


# Shared Fixtures
@pytest.fixture
def mock_request():
    """Mock FastAPI request object"""
    from starlette.requests import Request
    from starlette.datastructures import State

    mock_req = Mock(spec=Request)
    mock_req.headers = {}
    mock_req.state = State()
    mock_req.method = "GET"
    mock_req.url = Mock()
    mock_req.url.path = "/v1/admin/roles"
    mock_req.query_params = {}

    # Add dictionary-style access for rate limiter
    mock_req.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))

    return mock_req


@pytest.fixture
def mock_db_conn():
    """Mock async database connection for unit tests"""
    return AsyncMock()


@pytest.fixture
def valid_current_user():
    """Valid JWT token data"""
    return {
        "sub": "550e8400-e29b-41d4-a716-446655440000",
        "user_id": "550e8400-e29b-41d4-a716-446655440000",  # Add user_id field that endpoint expects
        "email": "test@example.com",
        "user_metadata": {"organization_id": "123e4567-e89b-12d3-a456-426614174000", "type": "organization_member"},
        "organization_id": "123e4567-e89b-12d3-a456-426614174000",  # Add organization_id at top level
    }


@pytest.fixture
def default_query_params():
    """Default query parameters"""
    return RoleQueryParams(search=None, role_type=None, skip=0, limit=50)


@pytest.fixture
def app():
    """Create a FastAPI app for integration testing"""
    return app_instance


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
    """Fixture to create a FakeCursor instance for integration tests"""
    return FakeCursor()


@pytest.fixture
def fake_conn(fake_cursor):
    """Fixture to create a FakeConn instance for integration tests"""
    return FakeConn(fake_cursor)


@pytest.fixture(autouse=True)
def reset_test_state():
    """Reset test state after each test"""
    PERMISSION_MAPPING["ROLES_READ"] = True
    set_mock_auth_failure(False)
    yield
    PERMISSION_MAPPING["ROLES_READ"] = True
    set_mock_auth_failure(False)


# 5 ESSENTIAL TEST CASES


class TestGetRolesEssential:
    """5 essential test cases covering all critical scenarios"""

    # TEST 1: SUCCESS CASE WITH DEFAULT PARAMETERS (Happy Path)
    @pytest.mark.asyncio
    async def test_1_success_default_params_unit(
        self, valid_current_user, mock_db_conn, default_query_params
    ):
        """Unit Test 1: Successful roles retrieval with default parameters (covers happy path)"""
        from starlette.requests import Request
        from starlette.datastructures import State

        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/v1/admin/roles"
        request.query_params = {}
        request.headers = {}

        # Add dictionary-style access for rate limiter
        request.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            mock_db_conn.fetch.return_value = TEST_ROLES_DATA
            mock_db_conn.fetchrow.return_value = {"total_count": 2}

            response = await get_roles(
                request=request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
                query_params=default_query_params,
            )

            # Assertions
            assert response.status_code == status.HTTP_200_OK
            assert "Roles retrieved successfully" in response.message
            assert len(response.roles) == 2
            assert response.total_count == 2
            assert response.roles[0].name == "Admin"
            assert response.roles[0].is_default == True
            assert response.roles[1].name == "Editor"
            assert response.roles[1].is_default == False

    def test_1_success_default_params_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 1: Successful roles retrieval with default parameters via HTTP"""
        fake_cursor.fetchall_data = TEST_ROLES_DATA
        fake_cursor.fetchone_data = {"total_count": 2}

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/roles")

        assert response.status_code == 200
        data = response.json()
        assert "Roles retrieved successfully" in data["message"]
        assert len(data["roles"]) == 2
        assert data["total_count"] == 2
        assert data["roles"][0]["name"] == "Admin"
        assert data["roles"][0]["is_default"] == True

    # TEST 2: INVALID ROLE_TYPE PARAMETER (Input Validation)
    @pytest.mark.asyncio
    async def test_2_invalid_role_type_unit(
        self, valid_current_user, mock_db_conn
    ):
        """Unit Test 2: Invalid role_type parameter (covers input validation)"""
        from starlette.requests import Request
        from starlette.datastructures import State

        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/v1/admin/roles"
        request.query_params = {}
        request.headers = {}

        # Add dictionary-style access for rate limiter
        request.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))

        invalid_query_params = RoleQueryParams(
            search=None, role_type="invalid_type", skip=0, limit=50
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_roles(
                request=request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
                query_params=invalid_query_params,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Role type must be 'system' or 'custom'" in exc_info.value.detail

    def test_2_invalid_role_type_integration(self, client):
        """Integration Test 2: Invalid role_type parameter via HTTP"""
        response = client.get("/v1/admin/roles?role_type=invalid_type")

        assert response.status_code == 400
        data = response.json()
        assert "Role type must be 'system' or 'custom'" in data["detail"]

    # TEST 3: INSUFFICIENT PERMISSIONS (Authorization)
    @pytest.mark.asyncio
    async def test_3_insufficient_permissions_unit(
        self, valid_current_user, mock_db_conn, default_query_params
    ):
        """Unit Test 3: Insufficient permissions (covers authorization)"""
        from starlette.requests import Request
        from starlette.datastructures import State

        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/v1/admin/roles"
        request.query_params = {}
        request.headers = {}

        # Add dictionary-style access for rate limiter
        request.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_roles(
                    request=request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                    query_params=default_query_params,
                )

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "Insufficient permissions to access role details" in exc_info.value.detail

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        PERMISSION_MAPPING["ROLES_READ"] = False

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get("/v1/admin/roles")

        assert response.status_code == 403
        data = response.json()
        assert "Insufficient permissions" in data["detail"]

    # TEST 4: DATABASE ERROR (Exception Handling)
    @pytest.mark.asyncio
    async def test_4_database_error_unit(
        self, valid_current_user, mock_db_conn, default_query_params
    ):
        """Unit Test 4: Database error (covers exception handling)"""
        from starlette.requests import Request
        from starlette.datastructures import State

        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/v1/admin/roles"
        request.query_params = {}
        request.headers = {}

        # Add dictionary-style access for rate limiter
        request.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            side_effect=Exception("Database connection failed"),
        ):
            with pytest.raises(Exception) as exc_info:
                await get_roles(
                    request=request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                    query_params=default_query_params,
                )

            assert "Database connection failed" in str(exc_info.value)

    def test_4_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: Database error via HTTP"""
        fake_conn.should_fail_data_fetch = True  # Simulate database error

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # In test environment, database exceptions might bubble up directly
        try:
            response = client.get("/v1/admin/roles")
            # If we get a response, it should be 500
            assert response.status_code == 500
            data = response.json()
            assert "Internal server error" in data["detail"]
        except RuntimeError as e:
            # If exception bubbles up, verify it's the expected database error
            assert "Data fetch database error" in str(e)

    # TEST 5: SEARCH FUNCTIONALITY WITH FILTERS (Query Parameter Handling)
    @pytest.mark.asyncio
    async def test_5_search_with_filters_unit(
        self, valid_current_user, mock_db_conn
    ):
        """Unit Test 5: Search functionality with filters (covers query parameter handling)"""
        from starlette.requests import Request
        from starlette.datastructures import State

        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/v1/admin/roles"
        request.query_params = {}
        request.headers = {}

        # Add dictionary-style access for rate limiter
        request.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))

        search_query_params = RoleQueryParams(
            search="Admin", role_type="system", skip=5, limit=10
        )

        # Mock filtered results
        filtered_data = [role for role in TEST_ROLES_DATA if "Admin" in role["name"]]

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            mock_db_conn.fetch.return_value = filtered_data
            mock_db_conn.fetchrow.return_value = {"total_count": 1}

            response = await get_roles(
                request=request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
                query_params=search_query_params,
            )

            # Assertions
            assert response.status_code == status.HTTP_200_OK
            assert "search='Admin'" in response.message
            assert "type=system" in response.message
            assert "skip=5" in response.message
            assert "limit=10" in response.message
            assert len(response.roles) == 1
            assert response.roles[0].name == "Admin"

    def test_5_search_with_filters_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 5: Search functionality with filters via HTTP"""
        # Setup filtered data
        filtered_data = [role for role in TEST_ROLES_DATA if "Admin" in role["name"]]
        fake_cursor.fetchall_data = filtered_data
        fake_cursor.fetchone_data = {"total_count": 1}

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.get(
            "/v1/admin/roles?search=Admin&role_type=system&skip=5&limit=10"
        )

        assert response.status_code == 200
        data = response.json()
        assert "search='Admin'" in data["message"]
        assert "type=system" in data["message"]
        assert "skip=5" in data["message"]
        assert "limit=10" in data["message"]
        assert len(data["roles"]) == 1
        assert data["roles"][0]["name"] == "Admin"


# HELPER FUNCTIONS
def create_mock_role_data(
    name: str,
    description: str,
    is_default: bool = False,
    user_count: int = 0,
    permission_count: int = 0,
    permission_categories: Optional[dict] = None,
) -> dict:
    """Helper function to create mock role data"""
    return {
        "id": uuid.uuid4(),
        "name": name,
        "description": description,
        "is_default": is_default,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),  # Add missing updated_at field
        "user_count": user_count,
        "permission_count": permission_count,
        "permission_categories": permission_categories or {},
    }


def create_mock_current_user(
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict:
    """Helper function to create mock current user data"""
    return {
        "sub": user_id or "550e8400-e29b-41d4-a716-446655440000",
        "email": email or "test@example.com",
        "user_metadata": {
            "organization_id": organization_id or "123e4567-e89b-12d3-a456-426614174000", "type": "organization_member"
        },
    }
