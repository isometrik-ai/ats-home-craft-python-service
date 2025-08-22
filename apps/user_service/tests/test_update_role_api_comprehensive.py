"""
Test cases for update_role API endpoint
Comprehensive test coverage including unit and integration tests
"""

import uuid
import pytest
from unittest.mock import patch, Mock, AsyncMock, MagicMock
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.datastructures import State

# Import the endpoint functions
from apps.user_service.app.api.admin_management.roles import update_role
from apps.user_service.app.schemas.admin_access_management import UpdateRoleRequest

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

    from apps.user_service.app.api.admin_management.roles import (
        router as roles_router,
        update_role,
    )
    from apps.user_service.app.schemas.admin_access_management import UpdateRoleRequest
    from libs.shared_db.postgres_db.db import get_async_db_conn

# Create test app
test_app = create_test_app()


@pytest.fixture
def app():
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def valid_update_request():
    """Valid update role request data"""
    return UpdateRoleRequest(
        name="Updated Role",
        description="Updated description",
        is_default=False,
        permission_ids=[str(uuid.uuid4())],
    )


class TestUpdateRoleEssential:
    """5 essential test cases covering all critical scenarios"""

    # TEST 1: SUCCESS CASE WITH FIELD UPDATES (Happy Path)
    @pytest.mark.asyncio
    async def test_1_success_with_updates_unit(
        self,
        mock_request,
        valid_current_user,
        mock_db_conn,
        valid_role_id,
        valid_update_request,
    ):
        """Unit Test 1: Successful role update (covers happy path)"""
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            mock_db_conn.fetchrow.side_effect = [
                {"id": uuid.uuid4(), "name": "Old Role", "description": "Old description", "is_default": False},  # Role exists
                {"valid_count": 1},  # Permission validation
                None,  # No name conflict
            ]
            mock_db_conn.fetch.return_value = []  # No current permissions
            mock_db_conn.execute.return_value = None
            mock_db_conn.executemany.return_value = None

            response = await update_role(
                role_id=valid_role_id,
                role_data=valid_update_request,
                request=mock_request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
            )

            assert response.status_code == status.HTTP_200_OK
            assert "Role updated successfully" in response.message

    def test_1_success_with_updates_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 1: Successful role update via HTTP"""
        # Clear any previous responses and set up specific ones
        fake_conn.clear_query_responses()
        fake_conn.set_query_response(
            "select id, name, is_default, description from public.roles",
            {"id": MOCK_ROLE_ID, "name": "Old Role", "description": "Old description", "is_default": False},
        )  # Role exists check
        fake_conn.set_query_response(
            "select count(*) as valid_count", {"valid_count": 1}
        )  # Permission validation passes
        fake_conn.set_query_response(
            "where name = $1 and organization_id = $2 and id != $3", None
        )  # No name conflict
        fake_conn.set_query_response(
            "select permission_id from public.role_permissions", []
        )  # No current permissions

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {
            "name": "Updated Role",
            "description": "Updated description",
            "permission_ids": [str(uuid.uuid4())],
        }

        # Use patch to ensure permissions pass
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.put(f"/v1/admin/roles/{MOCK_ROLE_ID}", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert "Role updated successfully" in data["message"]

    # TEST 2: INVALID UUID FORMAT (Input Validation)
    @pytest.mark.asyncio
    async def test_2_invalid_uuid_format_unit(
        self, mock_request, valid_current_user, mock_db_conn, valid_update_request
    ):
        """Unit Test 2: Invalid UUID format (covers input validation)"""
        invalid_role_id = "not-a-valid-uuid"

        with pytest.raises(HTTPException) as exc_info:
            await update_role(
                role_id=invalid_role_id,
                role_data=valid_update_request,
                request=mock_request,
                current_user=valid_current_user,
                db_conn=mock_db_conn,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid role ID format" in exc_info.value.detail

    def test_2_invalid_uuid_format_integration(self, client):
        """Integration Test 2: Invalid UUID format via HTTP"""
        invalid_role_id = "not-a-valid-uuid"

        request_data = {"name": "Updated Role"}

        response = client.put(f"/v1/admin/roles/{invalid_role_id}", json=request_data)

        assert response.status_code == 400
        data = response.json()
        assert "Invalid role ID format" in data["detail"]

    # TEST 3: INSUFFICIENT PERMISSIONS (Authorization)
    @pytest.mark.asyncio
    async def test_3_insufficient_permissions_unit(
        self,
        mock_request,
        valid_current_user,
        mock_db_conn,
        valid_role_id,
        valid_update_request,
    ):
        """Unit Test 3: Insufficient permissions (covers authorization)"""
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_role(
                    role_id=valid_role_id,
                    role_data=valid_update_request,
                    request=mock_request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                )

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "Insufficient permissions to access role details" in exc_info.value.detail

    def test_3_insufficient_permissions_integration(
        self, client, app, fake_cursor, fake_conn
    ):
        """Integration Test 3: Insufficient permissions via HTTP"""
        set_permission("ROLES_UPDATE", False)

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {"name": "Updated Role"}

        response = client.put(f"/v1/admin/roles/{MOCK_ROLE_ID}", json=request_data)

        assert response.status_code == 403
        data = response.json()
        assert "Insufficient permissions" in data["detail"]

    # TEST 4: ROLE NOT FOUND (Error Handling)
    @pytest.mark.asyncio
    async def test_4_role_not_found_unit(
        self,
        mock_request,
        valid_current_user,
        mock_db_conn,
        valid_role_id,
        valid_update_request,
    ):
        """Unit Test 4: Role not found (covers error handling)"""
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            mock_db_conn.fetchrow.return_value = None  # Role not found

            with pytest.raises(HTTPException) as exc_info:
                await update_role(
                    role_id=valid_role_id,
                    role_data=valid_update_request,
                    request=mock_request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                )

            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
            assert "Role not found or access denied" in exc_info.value.detail

    def test_4_role_not_found_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 4: Role not found via HTTP"""
        fake_cursor.fetchone_data = None  # Role not found

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {"name": "Updated Role", "description": "Updated description"}

        # Use patch to ensure permissions pass (so we can test the not found error)
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            response = client.put(f"/v1/admin/roles/{MOCK_ROLE_ID}", json=request_data)

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    # TEST 5: DATABASE ERROR (Exception Handling)
    @pytest.mark.asyncio
    async def test_5_database_error_unit(
        self, mock_request, valid_current_user, mock_db_conn
    ):
        """Unit Test 5: Database error (covers exception handling)"""
        valid_update_request = UpdateRoleRequest(
            name="Test Role", description="Test description"
        )

        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            side_effect=Exception("Database connection failed"),
        ):
            with pytest.raises(Exception) as exc_info:
                await update_role(
                    role_id=MOCK_ROLE_ID,
                    role_data=valid_update_request,
                    request=mock_request,
                    current_user=valid_current_user,
                    db_conn=mock_db_conn,
                )

            assert "Database connection failed" in str(exc_info.value)

    def test_5_database_error_integration(self, client, app, fake_cursor, fake_conn):
        """Integration Test 5: Database error via HTTP"""
        fake_conn.should_fail_query = True

        app.dependency_overrides[get_async_db_conn] = lambda: fake_conn

        request_data = {"name": "Test Role", "description": "Test description"}

        # Use patch to ensure permissions pass (so we can test the database error)
        with patch(
            "apps.user_service.app.dependencies.common_utils.check_user_access_async",
            return_value=True,
        ):
            # In test environment, database exceptions might bubble up directly
            try:
                response = client.put(f"/v1/admin/roles/{MOCK_ROLE_ID}", json=request_data)
                # If we get a response, it should be 500
                assert response.status_code == 500
                data = response.json()
                assert "Internal server error" in data["detail"]
            except Exception as e:
                # If exception bubbles up, verify it's the expected database error
                assert "Database" in str(e) or "Simulated DB failure" in str(e)


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
    mock_req.method = "PUT"
    mock_req.url = Mock()
    mock_req.url.path = f"/v1/admin/roles/{MOCK_ROLE_ID}"
    mock_req.query_params = {}
    
    # Add dictionary-style access for rate limiter
    mock_req.__getitem__ = Mock(side_effect=lambda key: {"path": f"/v1/admin/roles/{MOCK_ROLE_ID}"}.get(key))
    
    return mock_req


@pytest.fixture
def valid_current_user():
    """Valid JWT token data for testing"""
    return {
        "sub": MOCK_USER_ID,
        "email": "test@example.com",
        "user_metadata": {"organization_id": MOCK_ORG_ID, "type": "organization_member"},
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
