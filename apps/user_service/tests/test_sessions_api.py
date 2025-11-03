# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import HTTPException
from datetime import datetime, timezone
from libs.shared_db.postgres_db.user_service_operations.session_operations import (
    create_session,
    get_session_by_id,
    update_session,
    check_session_exists,
    get_sessions_list,
    get_sessions_count
)
from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    DatabaseOperationError
)
from apps.user_service.app.schemas.auth import SessionFilter


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.admin_management.sessions.sessions import router as sessions_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(sessions_router, prefix="/v1/admin")
    app.dependency_overrides[get_user_from_auth] = lambda: {
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "email": "e@e.com",
        "role": "admin",
        "permissions": ["*"],
        "session_id": "test-session-id"
    }
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# # ============================================================================
# # API LAYER TESTS (existing + expanded)
# # ============================================================================

class TestSessionEndpoints:
    pass
#     """Test cases for session API endpoints."""

#     @pytest.mark.asyncio
#     async def test_start_session_success(self, client):
#         """Test successful session creation."""
#         session_id = str(uuid.uuid4())
#         user_id = str(uuid.uuid4())

#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id=user_id,
#             email="e@e.com",
#             user_type="organization_member"
#         )

#         # Mock session existence check
#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.check_session_exists',
#                   AsyncMock(return_value=False)):

#             # Mock session creation
#             mock_created_session = {
#                 "id": session_id,
#                 "user_id": user_id,
#                 "organization_id": "o",
#                 "ip_address": "127.0.0.1",
#                 "user_agent": "test-agent",
#                 "device_fingerprint": "fp123",
#                 "risk_score": 0,
#                 "login_timestamp": datetime.now(timezone.utc),
#                 "logout_timestamp": None,
#                 "session_status": "active",
#                 "login_method": "password",
#                 "accessed_phi": False,
#                 "phi_access_purpose": None
#             }

#             with patch('apps.user_service.app.api.admin_management.sessions.sessions.create_session',
#                       AsyncMock(return_value=mock_created_session)):

#                     response = client.post("/v1/admin/sessions", headers={
#                         "User-Agent": "test-agent",
#                         "X-Device-Fingerprint": "fp123",
#                         "Authorization": f"Bearer {session_id}"
#                     })

#                     assert response.status_code == 201
#                     data = response.json()
#                     assert data["message"] == "Session created successfully"
#                     assert data["session"]["id"] == session_id
#                     assert data["session"]["user_id"] == user_id
#                     assert data["session"]["session_status"] == "active"

#     @pytest.mark.asyncio
#     async def test_start_session_already_exists(self, client):
#         """Test session creation when session already exists."""
#         from apps.user_service.app.dependencies.common_utils import UserContext
        
#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id="u",
#             email="e@e.com",
#             user_type="organization_member"
#         )
        
#         # Mock session existence check to return True
#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.check_session_exists',
#                   AsyncMock(return_value=True)):

#             response = client.post("/v1/admin/sessions", headers={
#                 "User-Agent": "test-agent",
#                 "X-Device-Fingerprint": "fp123",
#                 "Authorization": "Bearer test-session-id"
#             })

#             assert response.status_code == 409
#             data = response.json()
#             assert data["detail"] == "Session already exists"

#     @pytest.mark.asyncio
#     async def test_start_session_missing_session_id(self, client):
#         """Test session creation with missing session_id in token."""
#         user_id = str(uuid.uuid4())

#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id=user_id,
#             email="e@e.com",
#             user_type="organization_member"
#         )

#         # Mock the JWT token without session_id
#         with patch('libs.shared_middleware.jwt_auth.get_user_from_auth',
#                   return_value={"user_id": user_id, "organization_id": "o", "email": "e@e.com", "role": "admin", "permissions": ["*"]}), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)):

#             # Mock extract_session_id_from_token to raise HTTPException
#             with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_session_id_from_token',
#                       side_effect=HTTPException(status_code=400, detail="Session ID not found in token")):

#                 response = client.post("/v1/admin/sessions", headers={
#                     "User-Agent": "test-agent",
#                     "X-Device-Fingerprint": "fp123"
#                 })

#                 assert response.status_code == 400
#                 data = response.json()
#                 assert data["detail"] == "Session ID not found in token"

#     @pytest.mark.asyncio
#     async def test_start_session_database_error(self, client):
#         """Test session creation with database error."""
#         session_id = "test-session-id"

#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id="u",
#             email="e@e.com",
#             user_type="organization_member"
#         )

#         # Mock extract_session_id_from_token to return session_id
#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_session_id_from_token',
#                   return_value=session_id), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)):

#             # Mock check_session_exists to raise error
#             with patch('apps.user_service.app.api.admin_management.sessions.sessions.check_session_exists',
#                       AsyncMock(side_effect=DatabaseOperationError("Database error"))):

#                 response = client.post("/v1/admin/sessions", headers={
#                     "User-Agent": "test-agent",
#                     "X-Device-Fingerprint": "fp123",
#                     "Authorization": "Bearer test-session-id"
#                 })

#                 assert response.status_code == 500
#                 data = response.json()
#                 assert "Database error" in data["detail"]

#     @pytest.mark.asyncio
#     async def test_update_session_logout_success(self, client):
#         """Test successful session logout."""
#         session_id = "test-session-id"
#         user_id = str(uuid.uuid4())

#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id=user_id,
#             email="e@e.com",
#             user_type="organization_member"
#         )

#         # Mock existing session
#         mock_existing_session = {
#             "id": session_id,
#             "user_id": user_id,
#             "organization_id": "o",
#             "ip_address": "127.0.0.1",
#             "user_agent": "test-agent",
#             "device_fingerprint": "fp123",
#             "risk_score": 0,
#             "login_timestamp": datetime.now(timezone.utc),
#             "logout_timestamp": None,
#             "session_status": "active",
#             "login_method": "password",
#             "accessed_phi": False,
#             "phi_access_purpose": None
#         }

#         # Mock updated session
#         mock_updated_session = {
#             **mock_existing_session,
#             "session_status": "inactive",
#             "logout_timestamp": datetime.now(timezone.utc)
#         }

#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.get_session_by_id',
#                   AsyncMock(return_value=mock_existing_session)):

#             with patch('apps.user_service.app.api.admin_management.sessions.sessions.update_session',
#                       AsyncMock(return_value=mock_updated_session)):

#                 response = client.put("/v1/admin/sessions/logout", headers={
#                     "Authorization": "Bearer test-session-id"
#                 })

#                 assert response.status_code == 200
#                 data = response.json()
#                 assert data["message"] == "Session logout updated successfully"
#                 assert data["session"]["session_status"] == "inactive"

#     @pytest.mark.asyncio
#     async def test_update_session_logout_missing_session_id(self, client):
#         """Test session logout with missing session_id in token."""
#         # Mock extract_session_id_from_token to raise HTTPException
#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_session_id_from_token',
#                   side_effect=HTTPException(status_code=400, detail="Session ID not found in token")):

#             response = client.put("/v1/admin/sessions/logout", headers={
#                 "Authorization": "Bearer test-session-id"
#             })

#             assert response.status_code == 400
#             data = response.json()
#             assert data["detail"] == "Session ID not found in token"

#     @pytest.mark.asyncio
#     async def test_update_session_logout_session_not_found(self, client):
#         """Test session logout when session not found."""
#         from apps.user_service.app.dependencies.common_utils import UserContext
        
#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id="u",
#             email="e@e.com",
#             user_type="organization_member"
#         )
        
#         # Mock session not found
#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.get_session_by_id',
#                   AsyncMock(return_value=None)):

#             response = client.put("/v1/admin/sessions/logout", headers={
#                 "Authorization": "Bearer test-session-id"
#             })

#             assert response.status_code == 404
#             data = response.json()
#             assert data["detail"] == "Session not found or access denied"

#     @pytest.mark.asyncio
#     async def test_update_session_logout_database_error(self, client):
#         """Test session logout with database error."""
#         session_id = "test-session-id"

#         mock_user_context = UserContext(
#             organization_id="o",
#             user_id="u",
#             email="e@e.com",
#             user_type="organization_member"
#         )

#         # Mock extract_session_id_from_token to return session_id
#         with patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_session_id_from_token',
#                   return_value=session_id), \
#              patch('apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context', AsyncMock(return_value=mock_user_context)):

#             # Mock get_session_by_id to raise error
#             with patch('apps.user_service.app.api.admin_management.sessions.sessions.get_session_by_id',
#                       AsyncMock(side_effect=DatabaseOperationError("Database error"))):

#                 response = client.put("/v1/admin/sessions/logout", headers={
#                     "Authorization": "Bearer test-session-id"
#                 })

#                 assert response.status_code == 500
#                 data = response.json()
#                 assert "Database error" in data["detail"]

def test_sessions_list_success(client):
    """Test successful sessions list API endpoint."""
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    later = datetime(2025, 1, 2, tzinfo=timezone.utc)
    test_user_id = str(uuid.uuid4())
    test_org_id = str(uuid.uuid4())
    with patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count", AsyncMock(return_value={
        "data": [{
            "id": str(uuid.uuid4()),
            "user_id": test_user_id,
            "organization_id": test_org_id,
            "ip_address": "127.0.0.1",
            "user_agent": "agent",
            "device_fingerprint": None,
            "risk_score": 0,
            "login_timestamp": now.isoformat(),
            "logout_timestamp": later.isoformat(),
            "session_status": "active",
            "login_method": "password",
            "accessed_phi": False,
            "phi_access_purpose": None,
        }],
        "total_count": 1
    })):
        res = client.get("/v1/admin/sessions")
        assert res.status_code == 200
        assert res.json()["total_count"] == 1


def test_sessions_list_with_filters(client):
    """Test sessions list API with query parameters."""
    with patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count", AsyncMock(return_value={
        "data": [],
        "total_count": 0
    })):
        res = client.get("/v1/admin/sessions?status=active&limit=10&offset=0")
        assert res.status_code == 200
        assert res.json()["total_count"] == 0


def test_sessions_list_database_error(client):
    """Test sessions list API with database error."""
    with patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count",
               AsyncMock(side_effect=DatabaseOperationError("Database connection failed"))):
        # The API doesn't have error handling, so it will raise the exception
        with pytest.raises(DatabaseOperationError):
            client.get("/v1/admin/sessions")


# ============================================================================
# DATABASE OPERATIONS TESTS (new comprehensive tests)
# ============================================================================

class TestCreateSession:
    """Test cases for create_session function."""

    @pytest.mark.asyncio
    async def test_create_session_success(self):
        """Test successful session creation."""
        session_data = {
            "session_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "device_fingerprint": "fp123",
            "risk_score": 0.5,
            "login_method": "password",
            "accessed_phi": False,
            "phi_access_purpose": None
        }
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = [{"id": "session123", "user_id": session_data["user_id"]}]

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await create_session(session_data, organization_id)
            assert result == {"id": "session123", "user_id": session_data["user_id"]}

    @pytest.mark.asyncio
    async def test_create_session_no_data_returned(self):
        """Test session creation when no data is returned."""
        session_data = {
            "session_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "device_fingerprint": "fp123",
            "risk_score": 0.5,
            "login_method": "password",
            "accessed_phi": False,
            "phi_access_purpose": None
        }
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = []

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await create_session(session_data, organization_id)
            assert result == {}

    @pytest.mark.asyncio
    async def test_create_session_database_error(self):
        """Test session creation with database error."""
        session_data = {
            "session_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "device_fingerprint": "fp123",
            "risk_score": 0.5,
            "login_method": "password",
            "accessed_phi": False,
            "phi_access_purpose": None
        }
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await create_session(session_data, organization_id)


class TestGetSessionById:
    """Test cases for get_session_by_id function."""

    @pytest.mark.asyncio
    async def test_get_session_by_id_success(self):
        """Test successful session retrieval by ID."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_session = {
            "id": session_id,
            "user_id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "session_status": "active"
        }

        mock_result = MagicMock()
        mock_result.data = [mock_session]

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await get_session_by_id(session_id, organization_id)
            assert result == mock_session

    @pytest.mark.asyncio
    async def test_get_session_by_id_not_found(self):
        """Test session not found by ID."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = []

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await get_session_by_id(session_id, organization_id)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_session_by_id_database_error(self):
        """Test session retrieval with database error."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await get_session_by_id(session_id, organization_id)


class TestUpdateSession:
    """Test cases for update_session function."""

    @pytest.mark.asyncio
    async def test_update_session_success(self):
        """Test successful session update."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {
            "session_status": "inactive",
            "logout_timestamp": datetime.now(timezone.utc).isoformat()
        }

        mock_updated_session = {
            "id": session_id,
            "user_id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "session_status": "inactive",
            "logout_timestamp": update_data["logout_timestamp"]
        }

        mock_result = MagicMock()
        mock_result.data = [mock_updated_session]

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await update_session(session_id, organization_id, update_data)
            assert result == mock_updated_session

    @pytest.mark.asyncio
    async def test_update_session_not_found(self):
        """Test session update when session not found."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {"session_status": "inactive"}

        mock_result = MagicMock()
        mock_result.data = []

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await update_session(session_id, organization_id, update_data)
            assert result == {}

    @pytest.mark.asyncio
    async def test_update_session_database_error(self):
        """Test session update with database error."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {"session_status": "inactive"}

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await update_session(session_id, update_data, organization_id)


class TestCheckSessionExists:
    """Test cases for check_session_exists function."""

    @pytest.mark.asyncio
    async def test_check_session_exists_true(self):
        """Test session exists check returning True."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = [{"id": session_id}]

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await check_session_exists(session_id, organization_id)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_session_exists_false(self):
        """Test session exists check returning False."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = []

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await check_session_exists(session_id, organization_id)
            assert result is False

    @pytest.mark.asyncio
    async def test_check_session_exists_database_error(self):
        """Test session exists check with database error."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await check_session_exists(session_id, organization_id)


class TestGetSessionsList:
    """Test cases for get_sessions_list function."""

    @pytest.mark.asyncio
    async def test_get_sessions_list_success(self):
        """Test successful sessions list retrieval."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(
            session_status="active",
            limit=10,
            offset=0
        )

        mock_sessions = [
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "organization_id": organization_id,
                "session_status": "active",
                "ip_address": "192.168.1.1"
            }
        ]

        mock_result = MagicMock()
        mock_result.data = mock_sessions

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await get_sessions_list(organization_id, user_id, filters)
            assert result == mock_sessions

    @pytest.mark.asyncio
    async def test_get_sessions_list_empty_result(self):
        """Test sessions list with empty result."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(session_status="inactive")

        mock_result = MagicMock()
        mock_result.data = []

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await get_sessions_list(organization_id, user_id, filters)
            assert result == []

    @pytest.mark.asyncio
    async def test_get_sessions_list_database_error(self):
        """Test sessions list with database error."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter()

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await get_sessions_list(organization_id, user_id, filters)


class TestGetSessionsCount:
    """Test cases for get_sessions_count function."""

    @pytest.mark.asyncio
    async def test_get_sessions_count_success(self):
        """Test successful sessions count retrieval."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(session_status="active")

        mock_result = MagicMock()
        mock_result.count = 5

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await get_sessions_count(organization_id, user_id, filters)
            assert result == 5

    @pytest.mark.asyncio
    async def test_get_sessions_count_zero(self):
        """Test sessions count returning zero."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(session_status="inactive")

        mock_result = MagicMock()
        mock_result.count = 0

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):

            result = await get_sessions_count(organization_id, user_id, filters)
            assert result == 0

    @pytest.mark.asyncio
    async def test_get_sessions_count_database_error(self):
        """Test sessions count with database error."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter()

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await get_sessions_count(organization_id, user_id, filters)


# ============================================================================
# UTILITY FUNCTION TESTS
# ============================================================================

class TestSessionUtilities:
    """Test cases for session utility functions."""

    @pytest.mark.asyncio
    async def test_get_client_ip_with_x_forwarded_for(self):
        """Test client IP extraction with X-Forwarded-For header."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}
        mock_request.client = None

        from apps.user_service.app.api.admin_management.sessions.sessions import get_client_ip
        result = get_client_ip(mock_request)
        assert result == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_get_client_ip_with_x_real_ip(self):
        """Test client IP extraction with X-Real-IP header."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Real-IP": "192.168.1.2"}
        mock_request.client = None

        from apps.user_service.app.api.admin_management.sessions.sessions import get_client_ip
        result = get_client_ip(mock_request)
        assert result == "192.168.1.2"

    @pytest.mark.asyncio
    async def test_get_client_ip_fallback(self):
        """Test client IP extraction fallback to client host."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client.host = "192.168.1.3"

        from apps.user_service.app.api.admin_management.sessions.sessions import get_client_ip
        result = get_client_ip(mock_request)
        assert result == "192.168.1.3"

    @pytest.mark.asyncio
    async def test_get_user_agent_success(self):
        """Test user agent extraction from headers."""
        mock_request = MagicMock()
        mock_request.headers = {"User-Agent": "Mozilla/5.0"}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_user_agent
        result = get_user_agent(mock_request)
        assert result == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_get_user_agent_missing(self):
        """Test user agent extraction when header is missing."""
        mock_request = MagicMock()
        mock_request.headers = {}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_user_agent
        result = get_user_agent(mock_request)
        assert result == "unknown"

    @pytest.mark.asyncio
    async def test_get_device_fingerprint_success(self):
        """Test device fingerprint extraction from headers."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Device-Fingerprint": "fp123"}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_device_fingerprint
        result = get_device_fingerprint(mock_request)
        assert result == "fp123"

    @pytest.mark.asyncio
    async def test_get_device_fingerprint_missing(self):
        """Test device fingerprint extraction when header is missing."""
        mock_request = MagicMock()
        mock_request.headers = {}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_device_fingerprint
        result = get_device_fingerprint(mock_request)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_risk_score_high_risk(self):
        """Test risk score calculation for high-risk scenario."""
        mock_request = MagicMock()
        mock_request.headers = {
            "X-Forwarded-For": "192.168.1.1",
            "User-Agent": None
        }

        from apps.user_service.app.api.admin_management.sessions.sessions import get_risk_score
        result = get_risk_score(mock_request)
        # Should be 30 (20 for no User-Agent + 10 for proxy)
        assert result == 30

    @pytest.mark.asyncio
    async def test_get_risk_score_low_risk(self):
        """Test risk score calculation for low-risk scenario."""
        mock_request = MagicMock()
        mock_request.headers = {
            "User-Agent": "Mozilla/5.0",
            "X-Device-Fingerprint": "fp123"
        }

        from apps.user_service.app.api.admin_management.sessions.sessions import get_risk_score
        result = get_risk_score(mock_request)
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_login_method_mfa(self):
        """Test login method detection for MFA."""
        mock_request = MagicMock()
        mock_request.headers = {"X-MFA-Token": "token123"}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_login_method
        result = get_login_method(mock_request)
        assert result == "mfa"

    @pytest.mark.asyncio
    async def test_get_login_method_sso(self):
        """Test login method detection for SSO."""
        mock_request = MagicMock()
        mock_request.headers = {"X-SSO-Provider": "google"}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_login_method
        result = get_login_method(mock_request)
        assert result == "sso"

    @pytest.mark.asyncio
    async def test_get_login_method_password(self):
        """Test login method detection default to password."""
        mock_request = MagicMock()
        mock_request.headers = {}

        from apps.user_service.app.api.admin_management.sessions.sessions import get_login_method
        result = get_login_method(mock_request)
        assert result == "password"

    # These functions test synchronous utility functions, so they don't need the asyncio mark
    def test_build_session_filter_message_no_filters(self):
        """Test filter message building with no filters."""
        from apps.user_service.app.api.admin_management.sessions.sessions import build_session_filter_message
        result = build_session_filter_message()
        assert result == "Sessions retrieved successfully (page 1, 20 per page)"

    def test_build_session_filter_message_all_filters(self):
        """Test filter message building with all filters."""
        from apps.user_service.app.api.admin_management.sessions.sessions import build_session_filter_message
        result = build_session_filter_message(
            search="test",
            session_status="active",
            login_method="mfa",
            page=2,
            page_size=10
        )
        expected = "Sessions retrieved successfully (page 2, 10 per page) with filters: search='test', status='active', login_method='mfa'"
        assert result == expected

    @pytest.mark.asyncio
    async def test_extract_session_data_from_request(self):
        """Test complete session data extraction from request."""
        mock_request = MagicMock()
        mock_request.headers = {
            "X-Forwarded-For": "192.168.1.1",
            "User-Agent": "Mozilla/5.0",
            "X-Device-Fingerprint": "fp123",
            "X-MFA-Token": "token123"
        }
        mock_request.client = None

        from apps.user_service.app.api.admin_management.sessions.sessions import _extract_session_data_from_request
        result = _extract_session_data_from_request(mock_request)

        assert result == {
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "device_fingerprint": "fp123",
            "risk_score": 10,  # 10 for proxy
            "login_method": "mfa"
        }

# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestSessionOperationsIntegration:
    """Integration tests for session operations."""

    @pytest.mark.asyncio
    async def test_full_session_lifecycle(self):
        """Test complete session lifecycle: create -> get -> update -> check exists."""
        session_data = {
            "session_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "device_fingerprint": "fp123",
            "risk_score": 0.5,
            "login_method": "password",
            "accessed_phi": False,
            "phi_access_purpose": None
        }
        organization_id = str(uuid.uuid4())

        # Test each operation separately with proper mocking

        # 1. Test create session
        mock_insert_result = MagicMock()
        mock_insert_result.data = [{"id": "session123", "user_id": session_data["user_id"]}]
        mock_table = MagicMock()
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute = AsyncMock(return_value=mock_insert_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_table)))):
            created_session = await create_session(session_data, organization_id)
            assert created_session["id"] == "session123"

        # 2. Test get session
        mock_get_result = MagicMock()
        mock_get_result.data = [{"id": "session123", "user_id": session_data["user_id"], "session_status": "active"}]
        mock_get_table = MagicMock()
        mock_get_query = MagicMock()
        mock_get_table.select.return_value = mock_get_query
        mock_get_query.eq.return_value = mock_get_query
        mock_get_query.execute = AsyncMock(return_value=mock_get_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_get_table)))):
            retrieved_session = await get_session_by_id("session123", organization_id)
            assert retrieved_session["session_status"] == "active"

        # 3. Test update session
        mock_update_result = MagicMock()
        mock_update_result.data = [{"id": "session123", "user_id": session_data["user_id"], "session_status": "inactive"}]
        mock_update_table = MagicMock()
        mock_update_query = MagicMock()
        mock_update_table.update.return_value = mock_update_query
        mock_update_query.eq.return_value = mock_update_query
        mock_update_query.execute = AsyncMock(return_value=mock_update_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_update_table)))):
            updated_session = await update_session("session123", organization_id, {"session_status": "inactive"})
            assert updated_session["session_status"] == "inactive"

        # 4. Test check exists
        mock_exists_result = MagicMock()
        mock_exists_result.data = [{"id": "session123"}]
        mock_exists_table = MagicMock()
        mock_exists_query = MagicMock()
        mock_exists_table.select.return_value = mock_exists_query
        mock_exists_query.eq.return_value = mock_exists_query
        mock_exists_query.limit.return_value = mock_exists_query
        mock_exists_query.execute = AsyncMock(return_value=mock_exists_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_exists_table)))):
            exists = await check_session_exists("session123", organization_id)
            assert exists is True

    @pytest.mark.asyncio
    async def test_session_operations_with_filters(self):
        """Test session operations with various filters."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        # Test sessions list with proper mocking
        mock_sessions = [
            {"id": "s1", "user_id": user_id, "session_status": "active"},
            {"id": "s2", "user_id": user_id, "session_status": "active"}
        ]
        mock_list_result = MagicMock()
        mock_list_result.data = mock_sessions
        mock_list_table = MagicMock()
        mock_list_query = MagicMock()
        mock_list_table.select.return_value = mock_list_query
        mock_list_query.eq.return_value = mock_list_query
        mock_list_query.order.return_value = mock_list_query
        mock_list_query.range.return_value = mock_list_query
        mock_list_query.execute = AsyncMock(return_value=mock_list_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_list_table)))):
            filters = SessionFilter(
                session_status="active",
                limit=10,
                offset=0
            )
            sessions = await get_sessions_list(organization_id, user_id, filters)
            assert len(sessions) == 2

        # Test sessions count with proper mocking
        mock_count_result = MagicMock()
        mock_count_result.count = 2
        mock_count_table = MagicMock()
        mock_count_query = MagicMock()
        mock_count_table.select.return_value = mock_count_query
        mock_count_query.eq.return_value = mock_count_query
        mock_count_query.execute = AsyncMock(return_value=mock_count_result)

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(table=MagicMock(return_value=mock_count_table)))):
            count = await get_sessions_count(organization_id, user_id, filters)
            assert count == 2


# ============================================================================
# MISSING COVERAGE TESTS FOR SESSION_OPERATIONS.PY
# ============================================================================

class TestSessionOperationsCoverage:
    """Test cases to increase coverage for session_operations.py"""

    @pytest.mark.asyncio
    async def test_create_session_invalid_organization_id(self):
        """Test create_session with invalid organization_id - covers line 38"""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DataValidationError

        session_data = {
            "session_id": "test-session",
            "user_id": "test-user",
            "ip_address": "127.0.0.1",
            "user_agent": "test-agent",
            "device_fingerprint": "fp123",
            "risk_score": 0,
            "login_method": "password"
        }

        with pytest.raises(DataValidationError) as exc_info:
            await create_session(session_data, None)
        assert "Organization ID cannot be None or empty" in str(exc_info.value)

        with pytest.raises(DataValidationError) as exc_info:
            await create_session(session_data, "")
        assert "Organization ID cannot be None or empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_session_invalid_session_id(self):
        """Test create_session with invalid session_id - covers line 41"""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DataValidationError

        session_data = {
            "session_id": None,
            "user_id": "test-user",
            "ip_address": "127.0.0.1",
            "user_agent": "test-agent",
            "device_fingerprint": "fp123",
            "risk_score": 0,
            "login_method": "password"
        }

        with pytest.raises(DataValidationError) as exc_info:
            await create_session(session_data, "org123")
        assert "Session ID cannot be None or empty" in str(exc_info.value)

        session_data["session_id"] = ""
        with pytest.raises(DataValidationError) as exc_info:
            await create_session(session_data, "org123")
        assert "Session ID cannot be None or empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_session_invalid_user_id(self):
        """Test create_session with invalid user_id - covers line 44"""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DataValidationError

        session_data = {
            "session_id": "test-session",
            "user_id": None,
            "ip_address": "127.0.0.1",
            "user_agent": "test-agent",
            "device_fingerprint": "fp123",
            "risk_score": 0,
            "login_method": "password"
        }

        with pytest.raises(DataValidationError) as exc_info:
            await create_session(session_data, "org123")
        assert "User ID cannot be None or empty" in str(exc_info.value)

        session_data["user_id"] = ""
        with pytest.raises(DataValidationError) as exc_info:
            await create_session(session_data, "org123")
        assert "User ID cannot be None or empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_session_user_context_failure(self):
        """Test create_session when user context setting fails - covers lines 73-74"""
        session_data = {
            "session_id": "test-session",
            "user_id": "test-user",
            "ip_address": "127.0.0.1",
            "user_agent": "test-agent",
            "device_fingerprint": "fp123",
            "risk_score": 0,
            "login_method": "password",
            "user_email": "test@example.com"
        }

        # Mock Supabase client with auth that raises exception
        mock_supabase = MagicMock()
        mock_auth = MagicMock()
        mock_auth.set_user.side_effect = Exception("Auth context error")
        mock_supabase.auth = mock_auth

        # Mock successful insert
        mock_insert_result = MagicMock()
        mock_insert_result.data = [{"id": "test-session", "user_id": "test-user"}]
        mock_table = MagicMock()
        mock_insert_query = MagicMock()
        mock_table.insert.return_value = mock_insert_query
        mock_insert_query.execute = AsyncMock(return_value=mock_insert_result)
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await create_session(session_data, "org123")
            assert result["id"] == "test-session"
            assert result["user_id"] == "test-user"

    @pytest.mark.asyncio
    async def test_update_session_with_session_status(self):
        """Test update_session with session_status field - covers line 119"""
        session_id = "test-session"
        organization_id = "org123"
        update_data = {"session_status": "inactive"}

        # Mock Supabase client
        mock_supabase = MagicMock()
        mock_update_result = MagicMock()
        mock_update_result.data = [{"id": session_id, "session_status": "inactive"}]
        mock_table = MagicMock()
        mock_update_query = MagicMock()
        mock_table.update.return_value = mock_update_query
        mock_update_query.eq.return_value = mock_update_query
        mock_update_query.execute = AsyncMock(return_value=mock_update_result)
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await update_session(session_id, organization_id, update_data)
            assert result["session_status"] == "inactive"

    @pytest.mark.asyncio
    async def test_update_session_with_accessed_phi(self):
        """Test update_session with accessed_phi field - covers line 128"""
        session_id = "test-session"
        organization_id = "org123"
        update_data = {"accessed_phi": True}

        # Mock Supabase client
        mock_supabase = MagicMock()
        mock_update_result = MagicMock()
        mock_update_result.data = [{"id": session_id, "accessed_phi": True}]
        mock_table = MagicMock()
        mock_update_query = MagicMock()
        mock_table.update.return_value = mock_update_query
        mock_update_query.eq.return_value = mock_update_query
        mock_update_query.execute = AsyncMock(return_value=mock_update_result)
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await update_session(session_id, organization_id, update_data)
            assert result["accessed_phi"] is True

    @pytest.mark.asyncio
    async def test_update_session_with_phi_access_purpose(self):
        """Test update_session with phi_access_purpose field - covers line 132"""
        session_id = "test-session"
        organization_id = "org123"
        update_data = {"phi_access_purpose": "medical_review"}

        # Mock Supabase client
        mock_supabase = MagicMock()
        mock_update_result = MagicMock()
        mock_update_result.data = [{"id": session_id, "phi_access_purpose": "medical_review"}]
        mock_table = MagicMock()
        mock_update_query = MagicMock()
        mock_table.update.return_value = mock_update_query
        mock_update_query.eq.return_value = mock_update_query
        mock_update_query.execute = AsyncMock(return_value=mock_update_result)
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await update_session(session_id, organization_id, update_data)
            assert result["phi_access_purpose"] == "medical_review"

    @pytest.mark.asyncio
    async def test_check_session_exists_invalid_session_id(self):
        """Test check_session_exists with invalid session_id - covers line 150"""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DataValidationError

        with pytest.raises(DataValidationError) as exc_info:
            await check_session_exists(None, "org123")
        assert "Session ID cannot be None or empty" in str(exc_info.value)

        with pytest.raises(DataValidationError) as exc_info:
            await check_session_exists("", "org123")
        assert "Session ID cannot be None or empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_check_session_exists_invalid_organization_id(self):
        """Test check_session_exists with invalid organization_id - covers line 153"""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DataValidationError

        with pytest.raises(DataValidationError) as exc_info:
            await check_session_exists("session123", None)
        assert "Organization ID cannot be None or empty" in str(exc_info.value)

        with pytest.raises(DataValidationError) as exc_info:
            await check_session_exists("session123", "")
        assert "Organization ID cannot be None or empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_sessions_list_with_search(self):
        """Test get_sessions_list with search filter - covers lines 188, 191"""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(
            search="test@example.com",
            limit=10,
            offset=0
        )

        # Mock Supabase client with search functionality
        mock_supabase = MagicMock()
        mock_sessions = [
            {"id": "s1", "user_id": "user1", "session_status": "active"},
            {"id": "s2", "user_id": "user2", "session_status": "active"}
        ]
        mock_list_result = MagicMock()
        mock_list_result.data = mock_sessions
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_list_result)
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            sessions = await get_sessions_list(organization_id, user_id, filters)
            assert len(sessions) == 2
            # Verify or_ was called for search
            mock_query.or_.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_sessions_count_with_search(self):
        """Test get_sessions_count with search filter - covers lines 221, 225"""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(
            search="test@example.com",
            limit=10,
            offset=0
        )

        # Mock Supabase client with search functionality
        mock_supabase = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.count = 2

        # Create a mock chain that handles the double select call
        mock_table = MagicMock()
        mock_initial_query = MagicMock()
        mock_search_query = MagicMock()
    
        # First select call (initial)
        mock_table.select.return_value = mock_initial_query
        mock_initial_query.eq.return_value = mock_initial_query
    
        # Second select call (for search) - this returns a new query object
        mock_initial_query.select.return_value = mock_search_query
        mock_search_query.or_.return_value = mock_search_query
        mock_search_query.execute = AsyncMock(return_value=mock_count_result)

        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            count = await get_sessions_count(organization_id, user_id, filters)
            assert count == 2
            # Verify or_ was called for search
            mock_search_query.or_.assert_called_once()
