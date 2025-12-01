# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from libs.shared_db.postgres_db.user_service_operations.session_operations import (
    create_session,
    get_session_by_id,
    update_session,
    check_session_exists,
    get_sessions_list,
    get_sessions_count,
    get_sessions_with_count,
    get_org_sessions_with_count
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
        "sub": str(uuid.uuid4()),  # Use "sub" instead of "user_id" for JWT token format
        "email": "e@e.com",
        "user_metadata": {
            "organization_id": str(uuid.uuid4()),
            "type": "organization_member"
        },
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


def test_sessions_list_success(client):
    """Test successful sessions list API endpoint."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    later = datetime(2025, 1, 2, tzinfo=timezone.utc)
    test_user_id = str(uuid.uuid4())
    test_org_id = str(uuid.uuid4())

    mock_user_context = UserContext(
        organization_id=test_org_id,
        user_id=test_user_id,
        email="e@e.com",
        user_type="organization_member"
    )

    with patch("apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.check_permissions",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count",
               AsyncMock(return_value={
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
    from apps.user_service.app.dependencies.common_utils import UserContext

    test_user_id = str(uuid.uuid4())
    test_org_id = str(uuid.uuid4())

    mock_user_context = UserContext(
        organization_id=test_org_id,
        user_id=test_user_id,
        email="e@e.com",
        user_type="organization_member"
    )

    with patch("apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.check_permissions",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count",
               AsyncMock(return_value={
                   "data": [],
                   "total_count": 0
               })):
        res = client.get("/v1/admin/sessions?status=active&limit=10&offset=0")
        assert res.status_code == 200
        assert res.json()["total_count"] == 0


def test_sessions_list_database_error(client):
    """Test sessions list API with database error."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    test_user_id = str(uuid.uuid4())
    test_org_id = str(uuid.uuid4())

    mock_user_context = UserContext(
        organization_id=test_org_id,
        user_id=test_user_id,
        email="e@e.com",
        user_type="organization_member"
    )

    with patch("apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.check_permissions",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count",
               AsyncMock(side_effect=DatabaseOperationError("Database connection failed"))):
        # The API doesn't have error handling, so it will raise the exception
        with pytest.raises(DatabaseOperationError):
            client.get("/v1/admin/sessions")


def test_sessions_list_no_organization_id(client):
    """Test sessions list API when user has no organization_id."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=None,
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    # Mock the extract_user_context and get_sessions_with_count functions
    mock_result = {"data": [], "total_count": 0}
    with patch("apps.user_service.app.api.admin_management.sessions.sessions.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.sessions.sessions.get_sessions_with_count",
               AsyncMock(return_value=mock_result)):
        res = client.get("/v1/admin/sessions")
        assert res.status_code == 200  # Now allows users without organization_id
        assert res.json()["total_count"] == 0


def test_org_sessions_list_success(client):
    """Test successful organization-wide sessions list API endpoint."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    test_org_id = str(uuid.uuid4())

    mock_user_context = UserContext(
        organization_id=test_org_id,
        user_id=str(uuid.uuid4()),
        email="e@e.com",
        user_type="organization_member",
    )

    with patch(
        "apps.user_service.app.api.admin_management.sessions.sessions.check_permissions",
        AsyncMock(return_value=mock_user_context),
    ), patch(
        "apps.user_service.app.api.admin_management.sessions.sessions.get_org_sessions_with_count",
        AsyncMock(
            return_value={
                "data": [
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": str(uuid.uuid4()),
                        "organization_id": test_org_id,
                        "ip_address": "127.0.0.1",
                        "user_agent": "agent",
                        "device_fingerprint": None,
                        "risk_score": 0,
                        "login_timestamp": datetime.now(timezone.utc).isoformat(),
                        "logout_timestamp": None,
                        "session_status": "active",
                        "login_method": "password",
                        "accessed_phi": False,
                        "phi_access_purpose": None,
                    }
                ],
                "total_count": 1,
            }
        ),
    ):
        res = client.get("/v1/admin/sessions/all")
        assert res.status_code == 200
        body = res.json()
        assert body["total_count"] == 1
        assert len(body["sessions"]) == 1


def test_org_sessions_list_missing_organization_id(client):
    """Test organization-wide sessions list when user has no organization_id."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=None,
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member",
    )

    with patch(
        "apps.user_service.app.api.admin_management.sessions.sessions.check_permissions",
        AsyncMock(return_value=mock_user_context),
    ):
        res = client.get("/v1/admin/sessions/all")
        assert res.status_code == 400
        assert (
            "Organization ID is required"
            in res.json().get("detail", "")
        )


def test_session_response_to_dict():
    """Test SessionResponse.to_dict() method."""
    from apps.user_service.app.api.admin_management.sessions.sessions import SessionResponse

    response = SessionResponse(message="Test message", status="success")
    result = response.to_dict()

    assert result == {"message": "Test message", "status": "success"}


def test_extract_session_id_from_token_missing():
    """Test extract_session_id_from_token when session_id is missing."""
    from apps.user_service.app.api.admin_management.sessions.sessions import extract_session_id_from_token
    from fastapi import HTTPException

    current_user = {"user_id": "123", "email": "test@example.com"}

    with pytest.raises(HTTPException) as exc_info:
        extract_session_id_from_token(current_user)

    assert exc_info.value.status_code == 400
    assert "Session ID not found in token" in exc_info.value.detail


def test_extract_session_id_from_token_empty():
    """Test extract_session_id_from_token when session_id is empty string."""
    from apps.user_service.app.api.admin_management.sessions.sessions import extract_session_id_from_token
    from fastapi import HTTPException

    current_user = {"user_id": "123", "email": "test@example.com", "session_id": ""}

    with pytest.raises(HTTPException) as exc_info:
        extract_session_id_from_token(current_user)

    assert exc_info.value.status_code == 400
    assert "Session ID not found in token" in exc_info.value.detail


def test_extract_session_id_from_token_none():
    """Test extract_session_id_from_token when session_id is None."""
    from apps.user_service.app.api.admin_management.sessions.sessions import extract_session_id_from_token
    from fastapi import HTTPException

    current_user = {"user_id": "123", "email": "test@example.com", "session_id": None}

    with pytest.raises(HTTPException) as exc_info:
        extract_session_id_from_token(current_user)

    assert exc_info.value.status_code == 400
    assert "Session ID not found in token" in exc_info.value.detail


def test_extract_session_id_from_token_success():
    """Test extract_session_id_from_token when session_id is valid."""
    from apps.user_service.app.api.admin_management.sessions.sessions import extract_session_id_from_token

    session_id = str(uuid.uuid4())
    current_user = {"user_id": "123", "email": "test@example.com", "session_id": session_id}

    result = extract_session_id_from_token(current_user)
    assert result == session_id


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
            "ip_address": "192.0.2.1",
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
            "ip_address": "192.0.2.1",
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
            "ip_address": "192.0.2.1",
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
            "ip_address": "192.0.2.1",
            "user_agent": "Mozilla/5.0",
            "session_status": "active"
        }

        mock_result = MagicMock()
        mock_result.data = [mock_session]

        # Create proper async mock chain
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_table.select.return_value = mock_query
        # Support both eq and is_ methods
        mock_query.eq.return_value = mock_query
        mock_query.is_ = MagicMock(return_value=mock_query)
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
        # Support both eq and is_ methods
        mock_query.eq.return_value = mock_query
        mock_query.is_ = MagicMock(return_value=mock_query)
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
            "accessed_phi": True,
            "phi_access_purpose": "security_review"
        }

        mock_updated_session = {
            "id": session_id,
            "user_id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "session_status": "inactive",
            "logout_timestamp": datetime.now(timezone.utc).isoformat()
        }

        mock_result = MagicMock()
        mock_result.data = [mock_updated_session]

        mock_update = MagicMock()
        mock_update.eq.return_value = mock_update
        mock_update.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.update.return_value = mock_update

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

            result = await update_session(session_id, organization_id, update_data)

            update_payload = mock_table.update.call_args[0][0]
            assert update_payload["session_status"] == "inactive"
            assert update_payload["accessed_phi"] is True
            assert update_payload["phi_access_purpose"] == "security_review"
            assert "logout_timestamp" in update_payload

            assert result == mock_updated_session

    @pytest.mark.asyncio
    async def test_update_session_not_found(self):
        """Test session update when session not found."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())
        update_data = {"session_status": "inactive"}

        mock_result = MagicMock()
        mock_result.data = []

        mock_update = MagicMock()
        mock_update.eq.return_value = mock_update
        mock_update.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.update.return_value = mock_update

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

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
                await update_session(session_id, organization_id, update_data)

    @pytest.mark.asyncio
    async def test_update_session_with_object_payload(self):
        """Test update_session when update_data is an object (covers helper access)."""
        session_id = str(uuid.uuid4())
        organization_id = str(uuid.uuid4())

        class UpdatePayload:
            session_status = "active"
            accessed_phi = False
            phi_access_purpose = "auditing"

        mock_result = MagicMock()
        mock_result.data = [{"id": session_id, "session_status": "active"}]

        mock_update = MagicMock()
        mock_update.eq.return_value = mock_update
        mock_update.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.update.return_value = mock_update

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

            result = await update_session(session_id, organization_id, UpdatePayload())

            update_payload = mock_table.update.call_args[0][0]
            assert update_payload["session_status"] == "active"
            assert update_payload["accessed_phi"] is False
            assert update_payload["phi_access_purpose"] == "auditing"

            assert result == {"id": session_id, "session_status": "active"}


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
                "ip_address": "192.0.2.1"
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
    async def test_get_sessions_list_with_search_filters(self):
        """Test sessions list retrieval with search and additional filters."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(
            search="john",
            session_status="active",
            login_method="password",
            limit=5,
            offset=5
        )

        mock_sessions = [{"id": "session1"}]

        mock_result = MagicMock()
        mock_result.data = mock_sessions

        mock_query = MagicMock()
        mock_query.eq.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.select.return_value = mock_query

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

            result = await get_sessions_list(organization_id, user_id, filters)

            mock_query.or_.assert_called_once()
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
    async def test_get_sessions_count_with_search_filters(self):
        """Test sessions count retrieval with search filters."""
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(
            search="doe",
            session_status="active",
            login_method="oauth"
        )

        mock_result = MagicMock()
        mock_result.count = 3

        mock_query = MagicMock()
        mock_query.eq.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.select.return_value = mock_query

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

            result = await get_sessions_count(organization_id, user_id, filters)

            mock_query.or_.assert_called_once()
            assert result == 3

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


# ==========================================================================
# GET SESSIONS WITH COUNT TESTS
# ==========================================================================


class TestGetSessionsWithCount:
    """Test cases for get_sessions_with_count function."""

    @pytest.mark.asyncio
    async def test_get_sessions_with_count_success(self):
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter()

        mock_result = MagicMock()
        mock_result.data = [{"id": "session1"}, {"id": "session2"}]

        mock_query = MagicMock()
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.select.return_value = mock_query

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

            result = await get_sessions_with_count(organization_id, user_id, filters)

            assert result == {
                "data": mock_result.data,
                "total_count": len(mock_result.data)
            }

    @pytest.mark.asyncio
    async def test_get_sessions_with_count_search_filters(self):
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter(search="smith", session_status="active", login_method="sso")

        mock_result = MagicMock()
        mock_result.data = [{"id": "session3"}]

        mock_query = MagicMock()
        mock_query.eq.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_result)

        mock_table = MagicMock()
        mock_table.select.return_value = mock_query

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):

            result = await get_sessions_with_count(organization_id, user_id, filters)

            mock_query.or_.assert_called_once()
            assert result == {
                "data": mock_result.data,
                "total_count": len(mock_result.data)
            }

    @pytest.mark.asyncio
    async def test_get_sessions_with_count_database_error(self):
        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = SessionFilter()

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Database connection failed"))):

            with pytest.raises(DatabaseOperationError):
                await get_sessions_with_count(organization_id, user_id, filters)


# ============================================================================
# UTILITY FUNCTION TESTS
# ============================================================================

class TestSessionUtilities:
    """Test cases for session utility functions."""

    @pytest.mark.asyncio
    async def test_get_client_ip_with_x_forwarded_for(self):
        """Test client IP extraction with X-Forwarded-For header."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "192.0.2.1, 10.0.0.1"}
        mock_request.client = None

        from apps.user_service.app.api.admin_management.sessions.sessions import get_client_ip
        result = get_client_ip(mock_request)
        assert result == "192.0.2.1"

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
            "X-Forwarded-For": "192.0.2.1",
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
            "ip_address": "192.0.2.1",
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
        mock_get_query.is_ = MagicMock(return_value=mock_get_query)
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
        mock_update_query.is_ = MagicMock(return_value=mock_update_query)
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
        mock_exists_query.is_ = MagicMock(return_value=mock_exists_query)
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
        """Test create_session with organization_id - None is now allowed, empty string still invalid"""
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

        # None is now allowed - test that it works
        mock_supabase = MagicMock()
        mock_insert_result = MagicMock()
        mock_insert_result.data = [{"id": "test-session", "user_id": "test-user", "organization_id": None}]
        mock_table = MagicMock()
        mock_insert_query = MagicMock()
        mock_table.insert.return_value = mock_insert_query
        mock_insert_query.execute = AsyncMock(return_value=mock_insert_result)
        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await create_session(session_data, None)
            assert result["id"] == "test-session"
            # Verify that auth.set_user was not called when organization_id is None
            assert not hasattr(mock_supabase, 'auth') or not mock_supabase.auth.set_user.called

        # Empty string should still raise error (if we want to keep that validation)
        # Note: Currently empty string is treated as falsy and would be None, so this might need adjustment
        # For now, we'll test that empty string is handled (it will be treated as None)

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
        # Support both eq and is_ methods - chain should return same object
        mock_final_query = MagicMock()
        mock_final_query.execute = AsyncMock(return_value=mock_update_result)
        # First eq("id", session_id) returns query, then eq("organization_id", ...) returns same query
        mock_update_query.eq = MagicMock(return_value=mock_final_query)
        mock_final_query.eq = MagicMock(return_value=mock_final_query)
        mock_final_query.is_ = MagicMock(return_value=mock_final_query)
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
        # Support both eq and is_ methods - chain should return same object
        mock_final_query = MagicMock()
        mock_final_query.execute = AsyncMock(return_value=mock_update_result)
        # First eq("id", session_id) returns query, then eq("organization_id", ...) returns same query
        mock_update_query.eq = MagicMock(return_value=mock_final_query)
        mock_final_query.eq = MagicMock(return_value=mock_final_query)
        mock_final_query.is_ = MagicMock(return_value=mock_final_query)
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
        # Support both eq and is_ methods - chain should return same object
        mock_final_query = MagicMock()
        mock_final_query.execute = AsyncMock(return_value=mock_update_result)
        # First eq("id", session_id) returns query, then eq("organization_id", ...) returns same query
        mock_update_query.eq = MagicMock(return_value=mock_final_query)
        mock_final_query.eq = MagicMock(return_value=mock_final_query)
        mock_final_query.is_ = MagicMock(return_value=mock_final_query)
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
        """Test check_session_exists with organization_id - None is now allowed"""
        # None is now allowed - test that it works with proper mock
        mock_supabase = MagicMock()
        mock_select_result = MagicMock()
        mock_select_result.data = [{"id": "session123"}]
        mock_table = MagicMock()
        mock_select_query = MagicMock()
        mock_table.select.return_value = mock_select_query
        # Mock the chain: select().eq().is_().execute()
        mock_is_query = MagicMock()
        mock_is_query.execute = AsyncMock(return_value=mock_select_result)
        mock_eq_query = MagicMock()
        mock_eq_query.is_ = MagicMock(return_value=mock_is_query)
        mock_eq_query.eq = MagicMock(return_value=mock_eq_query)
        mock_select_query.eq.return_value = mock_eq_query

        mock_supabase.table.return_value = mock_table

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await check_session_exists("session123", None)
            assert result is True
            # Verify is_ was called for NULL check
            mock_eq_query.is_.assert_called_once_with("organization_id", "null")

        # Test with valid organization_id
        mock_supabase2 = MagicMock()
        mock_select_result_empty = MagicMock()
        mock_select_result_empty.data = []
        mock_table2 = MagicMock()
        mock_select_query2 = MagicMock()
        mock_table2.select.return_value = mock_select_query2
        # Mock the chain: select().eq().eq().execute()
        mock_eq_query2 = MagicMock()
        mock_eq_query2.execute = AsyncMock(return_value=mock_select_result_empty)
        mock_eq_query2.eq = MagicMock(return_value=mock_eq_query2)
        mock_select_query2.eq.return_value = mock_eq_query2

        mock_supabase2.table.return_value = mock_table2

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase2)):
            result = await check_session_exists("session123", "org123")
            assert result is False
            # Verify eq was called for organization_id (second eq call)
            assert mock_eq_query2.eq.call_count >= 1

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


# ============================================================================
# GET ORG SESSIONS WITH COUNT TESTS
# ============================================================================

class TestGetOrgSessionsWithCount:
    """Test cases for get_org_sessions_with_count function."""

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_none_organization_id(self):
        """Test get_org_sessions_with_count with None organization_id - early return."""
        filters = SessionFilter()

        result = await get_org_sessions_with_count(None, filters)

        assert result == {"data": [], "total_count": 0}

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_no_search_no_filters(self):
        """Test get_org_sessions_with_count without search and filters."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(limit=10, offset=0)

        # Mock members data
        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            },
            {
                "user_id": "user2",
                "email": "user2@example.com",
                "first_name": "Jane",
                "last_name": "Smith"
            }
        ]

        # Mock sessions data
        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            },
            {
                "id": "session2",
                "user_id": "user2",
                "organization_id": organization_id,
                "ip_address": "192.168.1.2",
                "user_agent": "Chrome/1.0",
                "login_timestamp": "2024-01-02T00:00:00Z",
                "session_status": "active",
                "login_method": "sso"
            }
        ]

        # Mock Supabase client
        mock_supabase = MagicMock()

        # Mock members query
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        # Mock sessions query
        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        # Setup table returns
        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 2
            assert result["total_count"] == 2
            assert result["data"][0]["id"] == "session1"
            assert result["data"][0]["organization_members"]["email"] == "user1@example.com"
            assert result["data"][1]["organization_members"]["email"] == "user2@example.com"

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_matches_email(self):
        """Test get_org_sessions_with_count with search matching email."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="user1@example.com", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1
            assert result["data"][0]["id"] == "session1"

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_matches_first_name(self):
        """Test get_org_sessions_with_count with search matching first_name."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="John", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_matches_last_name(self):
        """Test get_org_sessions_with_count with search matching last_name."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="Doe", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_matches_user_agent(self):
        """Test get_org_sessions_with_count with search matching user_agent."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="Mozilla", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_matches_ip_address(self):
        """Test get_org_sessions_with_count with search matching ip_address."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="192.168.1.1", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_no_matches(self):
        """Test get_org_sessions_with_count with search that matches nothing."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="nonexistent", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 0
            assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_with_session_status_filter(self):
        """Test get_org_sessions_with_count with session_status filter."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(session_status="active", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query  # For session_status filter
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1
            # Verify session_status filter was applied
            mock_session_query.eq.assert_called_with("session_status", "active")

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_with_login_method_filter(self):
        """Test get_org_sessions_with_count with login_method filter."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(login_method="password", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query  # For login_method filter
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1
            # Verify login_method filter was applied
            mock_session_query.eq.assert_any_call("login_method", "password")

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_pagination(self):
        """Test get_org_sessions_with_count with pagination."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(limit=2, offset=1)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            },
            {
                "user_id": "user2",
                "email": "user2@example.com",
                "first_name": "Jane",
                "last_name": "Smith"
            },
            {
                "user_id": "user3",
                "email": "user3@example.com",
                "first_name": "Bob",
                "last_name": "Jones"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            },
            {
                "id": "session2",
                "user_id": "user2",
                "organization_id": organization_id,
                "ip_address": "192.168.1.2",
                "user_agent": "Chrome/1.0",
                "login_timestamp": "2024-01-02T00:00:00Z",
                "session_status": "active",
                "login_method": "sso"
            },
            {
                "id": "session3",
                "user_id": "user3",
                "organization_id": organization_id,
                "ip_address": "192.168.1.3",
                "user_agent": "Safari/1.0",
                "login_timestamp": "2024-01-03T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            # Should return 2 items (offset=1, limit=2) but total_count should be 3
            assert len(result["data"]) == 2
            assert result["total_count"] == 3
            assert result["data"][0]["id"] == "session2"
            assert result["data"][1]["id"] == "session3"

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_session_without_member(self):
        """Test get_org_sessions_with_count when session has no matching member."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(limit=10, offset=0)

        # No members
        mock_members = []

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1
            assert result["data"][0]["organization_members"] is None

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_special_characters_in_search(self):
        """Test get_org_sessions_with_count with special characters in search (+, @, etc.)."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="user+tag@example.com", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user+tag@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 1
            assert result["total_count"] == 1
            assert result["data"][0]["organization_members"]["email"] == "user+tag@example.com"

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_empty_search_string(self):
        """Test get_org_sessions_with_count with empty search string."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            # Empty search should return all sessions (no filtering)
            assert len(result["data"]) == 1
            assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_empty_members_and_sessions(self):
        """Test get_org_sessions_with_count with empty members and sessions."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(limit=10, offset=0)

        mock_members = []
        mock_sessions = []

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            assert len(result["data"]) == 0
            assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_get_org_sessions_with_count_search_matches_both_member_and_session(self):
        """Test get_org_sessions_with_count when search matches both member and session fields."""
        organization_id = str(uuid.uuid4())
        filters = SessionFilter(search="test", limit=10, offset=0)

        mock_members = [
            {
                "user_id": "user1",
                "email": "test@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        ]

        mock_sessions = [
            {
                "id": "session1",
                "user_id": "user1",
                "organization_id": organization_id,
                "ip_address": "192.168.1.1",
                "user_agent": "test-agent",
                "login_timestamp": "2024-01-01T00:00:00Z",
                "session_status": "active",
                "login_method": "password"
            }
        ]

        mock_supabase = MagicMock()
        mock_member_table = MagicMock()
        mock_member_query = MagicMock()
        mock_member_result = MagicMock()
        mock_member_result.data = mock_members
        mock_member_table.select.return_value = mock_member_query
        mock_member_query.eq.return_value = mock_member_query
        mock_member_query.execute = AsyncMock(return_value=mock_member_result)

        mock_session_table = MagicMock()
        mock_session_query = MagicMock()
        mock_session_result = MagicMock()
        mock_session_result.data = mock_sessions
        mock_session_table.select.return_value = mock_session_query
        mock_session_query.eq.return_value = mock_session_query
        mock_session_query.order.return_value = mock_session_query
        mock_session_query.execute = AsyncMock(return_value=mock_session_result)

        def table_side_effect(table_name):
            if table_name == "organization_members":
                return mock_member_table
            return mock_session_table

        mock_supabase.table.side_effect = table_side_effect

        with patch("libs.shared_db.postgres_db.user_service_operations.session_operations.get_fresh_supabase_admin_client",
                   AsyncMock(return_value=mock_supabase)):
            result = await get_org_sessions_with_count(organization_id, filters)

            # Should match because both email and user_agent contain "test"
            assert len(result["data"]) == 1
            assert result["total_count"] == 1