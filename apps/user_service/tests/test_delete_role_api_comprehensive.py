"""
Test cases for delete_role API endpoint
Comprehensive test coverage including unit and integration tests
"""

import uuid
import pytest
from unittest.mock import patch, Mock, AsyncMock, MagicMock
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

# Import the endpoint functions
from apps.user_service.app.api.admin_management.roles import delete_role

# Import dependencies for mocking
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Import test utilities
from apps.user_service.tests.test_utils import (
    MOCK_USER_ID,
    MOCK_ORG_ID,
    MOCK_ROLE_ID,
    reset_permissions,
    set_permission,
    create_test_app,
    create_mock_db_conn,
)

# Mock Supabase client before importing
with patch("supabase.create_client") as mock_create_client:
    mock_supabase = MagicMock()
    mock_create_client.return_value = mock_supabase

    from apps.user_service.app.api.admin_management.roles import router as roles_router

# Create test app
test_app = create_test_app()


@pytest.fixture
def app():
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestDeleteRoleEssential:
    """5 essential test cases covering all critical scenarios"""

    # TEST 1: SUCCESS CASE (Happy Path)
    @pytest.mark.asyncio
    async def test_1_success_unit(
        self, valid_current_user, mock_db_conn, valid_role_id
    ):
        """Unit Test 1: Successful role deletion (covers happy path)"""
        from starlette.requests import Request
        from starlette.datastructures import State
        
        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "DELETE"
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
            mock_db_conn.fetchrow.side_effect = [
                {
                    "id": uuid.uuid4(),
                    "name": "Test Role",
                    "description": "Test role description",
                    "is_default": False,
                },  # Role exists
                {"member_count": 0},  # No members using this role
            ]
            mock_db_conn.execute.side_effect = [
                "DELETE 1",  # Permissions deleted
                "DELETE 1",  # Role deleted
            ]

            response = await delete_role(
                role_id=valid_role_id,
                request=request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
            )

            assert response.status_code == status.HTTP_200_OK
            assert "Role deleted successfully" in response.message

    def test_1_success_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 1: Successful role deletion via HTTP"""
        fake_cursor.fetchone_data = {
            "id": MOCK_ROLE_ID,
            "name": "Test Role",
            "description": "Test role description",
            "is_default": False,
        }
        
        # Set up the cursor to return empty permissions data for the permission query
        # This will be used by the fetch method when querying permissions
        fake_cursor.fetchall_data = []
        
        # Set up member count to be 0 (no users using this role) for successful deletion
        fake_conn.set_query_response(
            "SELECT COUNT(*) as member_count", 
            {"member_count": 0}
        )

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.delete(f"/v1/admin/roles/{MOCK_ROLE_ID}")

        assert response.status_code == 200
        data = response.json()
        assert "Role deleted successfully" in data["message"]

    # TEST 2: INVALID UUID FORMAT (Input Validation)
    @pytest.mark.asyncio
    async def test_2_invalid_uuid_format_unit(
        self, valid_current_user, mock_db_conn
    ):
        """Unit Test 2: Invalid UUID format (covers input validation)"""
        from starlette.requests import Request
        from starlette.datastructures import State
        
        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "DELETE"
        request.url = Mock()
        request.url.path = "/v1/admin/roles"
        request.query_params = {}
        request.headers = {}
        
        # Add dictionary-style access for rate limiter
        request.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))
        
        invalid_role_id = "not-a-valid-uuid"

        with pytest.raises(HTTPException) as exc_info:
            await delete_role(
                role_id=invalid_role_id,
                request=request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid role ID format" in exc_info.value.detail

    def test_2_invalid_uuid_format_integration(self, client):
        """Integration Test 2: Invalid UUID format via HTTP"""
        invalid_role_id = "not-a-valid-uuid"

        response = client.delete(f"/v1/admin/roles/{invalid_role_id}")

        assert response.status_code == 400
        data = response.json()
        assert "Invalid role ID format" in data["detail"]

    # TEST 3: INSUFFICIENT PERMISSIONS (Authorization)
    @pytest.mark.asyncio
    async def test_3_insufficient_permissions_unit(
        self, valid_current_user, mock_db_conn, valid_role_id
    ):
        """Unit Test 3: Insufficient permissions (covers authorization)"""
        from starlette.requests import Request
        from starlette.datastructures import State
        
        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "DELETE"
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
                await delete_role(
                    role_id=valid_role_id,
                    request=request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                )

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "Insufficient permissions to access role details" in exc_info.value.detail

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        set_permission("ROLES_DELETE", False)

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        response = client.delete(f"/v1/admin/roles/{MOCK_ROLE_ID}")

        assert response.status_code == 403
        data = response.json()
        assert "Insufficient permissions" in data["detail"]

    # TEST 4: ROLE IN USE CONFLICT (Error Handling)
    @pytest.mark.asyncio
    async def test_4_role_in_use_conflict_unit(
        self, valid_current_user, mock_db_conn, valid_role_id
    ):
        """Unit Test 4: Role in use conflict (covers error handling for conflicts)"""
        from starlette.requests import Request
        from starlette.datastructures import State
        
        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "DELETE"
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
            mock_db_conn.fetchrow.side_effect = [
                {
                    "id": uuid.uuid4(),
                    "name": "Test Role",
                    "description": "Test role description",
                    "is_default": False,
                },  # Role exists
                {"member_count": 5},  # 5 members using this role
            ]

            with pytest.raises(HTTPException) as exc_info:
                await delete_role(
                    role_id=valid_role_id,
                    request=request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                )

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert (
                "currently assigned to 5 organization member(s)"
                in exc_info.value.detail
            )

    def test_4_role_in_use_conflict_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 4: Role in use conflict via HTTP"""
        fake_cursor.fetchone_data = {
            "id": MOCK_ROLE_ID,
            "name": "Test Role",
            "description": "Test role description",
            "is_default": False,
        }
        
        # Set up the cursor to return empty permissions data for the permission query
        # This will be used by the fetch method when querying permissions
        fake_cursor.fetchall_data = []
        
        # Set up member count to be greater than 0 (users are using this role) for conflict
        fake_conn.set_query_response(
            "SELECT COUNT(*) as member_count", 
            {"member_count": 5}
        )

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass (so we can test the role in use error)
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.delete(f"/v1/admin/roles/{MOCK_ROLE_ID}")

        assert response.status_code == 409
        data = response.json()
        assert "currently assigned" in data["detail"]

    # TEST 5: DATABASE ERROR (Exception Handling)
    @pytest.mark.asyncio
    async def test_5_database_error_unit(
        self, valid_current_user, mock_db_conn
    ):
        """Unit Test 5: Database error (covers exception handling)"""
        from starlette.requests import Request
        from starlette.datastructures import State
        
        # Create a proper Request object that supports dictionary access for rate limiter
        request = Mock(spec=Request)
        request.state = State()
        request.method = "DELETE"
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
                await delete_role(
                    role_id=MOCK_ROLE_ID,
                    request=request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                )

            assert "Database connection failed" in str(exc_info.value)

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        fake_conn.should_fail_query = True

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        # Use patch to ensure permissions pass (so we can test the database error)
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            # In test environment, database exceptions might bubble up directly
            try:
                response = client.delete(f"/v1/admin/roles/{MOCK_ROLE_ID}")
                # If we get a response, it should be 500
                assert response.status_code == 500
                data = response.json()
                assert "Internal server error" in data["detail"]
            except Exception as e:
                # If exception bubbles up, verify it's the expected database error
                assert "Simulated DB failure" in str(e)


@pytest.fixture
def fake_cursor():
    """Provide a fake cursor for database operations"""
    from apps.user_service.tests.test_utils import FakeCursor

    return FakeCursor()


@pytest.fixture
def fake_conn(fake_cursor):
    """Provide a fake database connection"""
    from apps.user_service.tests.test_utils import FakeConn

    return FakeConn(fake_cursor)


@pytest.fixture
def mock_request():
    """Mock FastAPI Request object"""
    from starlette.requests import Request
    from starlette.datastructures import State
    
    mock_req = Mock(spec=Request)
    mock_req.headers = {}
    mock_req.state = State()
    mock_req.method = "DELETE"
    mock_req.url = Mock()
    mock_req.url.path = "/v1/admin/roles"
    mock_req.query_params = {}
    
    # Add dictionary-style access for rate limiter
    mock_req.__getitem__ = Mock(side_effect=lambda key: {"path": "/v1/admin/roles"}.get(key))
    
    return mock_req


@pytest.fixture
def valid_current_user():
    """Valid JWT token data for testing"""
    return {
        "sub": MOCK_USER_ID,
        "user_id": MOCK_USER_ID,  # Add user_id field that endpoint expects
        "email": "test@example.com",
        "user_metadata": {"organization_id": MOCK_ORG_ID, "type": "organization_member"},
        "organization_id": MOCK_ORG_ID,  # Add organization_id at top level
    }


@pytest.fixture
def mock_db_conn():
    """Mock async database connection"""
    return create_mock_db_conn()


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Setup and teardown for each test"""
    # Setup
    reset_permissions()
    yield
    # Teardown
    reset_permissions()


@pytest.fixture
def valid_role_id():
    """Valid role ID for testing"""
    return MOCK_ROLE_ID
