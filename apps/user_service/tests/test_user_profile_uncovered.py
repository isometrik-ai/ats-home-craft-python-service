# pylint: disable=all

"""
Test to cover the uncovered line in user_profile.py (line 131).
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from apps.user_service.app.api.admin_management.users.user_profile import router as user_profile_router
from apps.user_service.app.dependencies.common_utils import UserContext
from libs.shared_middleware.jwt_auth import get_user_from_auth


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Disable rate limiting for tests."""
    class DummyLimiter:
        def __init__(self, *args, **kwargs):
            self.enabled = False
            self._auto_check = False

        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator

        def __call__(self, *args, **kwargs):
            return self

        def hit(self, *args, **kwargs):
            return True

        def get_window_stats(self, *args, **kwargs):
            return (0, 0)

        def _check_request_limit(self, *args, **kwargs):
            pass

        def _inject_headers(self, response, *args, **kwargs):
            return response

    dummy_limiter = DummyLimiter()
    
    with patch('apps.user_service.app.app_instance.limiter', dummy_limiter), \
         patch('apps.user_service.app.api.admin_management.users.user_profile.limiter', dummy_limiter):
        yield


@pytest.fixture
def app():
    """Create FastAPI app for testing."""
    app = FastAPI()
    app.include_router(user_profile_router, prefix="/v1/admin")
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    def mock_get_user_from_auth():
        return {
            "sub": str(uuid.uuid4()),
            "email": "test@example.com",
            "user_metadata": {}
        }
    
    from apps.user_service.app.dependencies.common_utils import check_user_access_async
    
    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return TestClient(app)


def test_get_user_profile_phone_in_phone_field(client):
    """Test user profile with phone in phone field (not metadata) - covers line 131."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    from types import SimpleNamespace

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    # Create user data with phone in phone field, not in user_metadata
    mock_user_data = SimpleNamespace(
        user=SimpleNamespace(
            email="test@example.com",
            phone="+1234567890",  # Phone in phone field
            user_metadata={},  # No phone in metadata
            phone_change=None,
            identities=[]
        )
    )

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        # Should use phone from phone field (line 131)
        assert res.json()["data"]["phone"] == "+1234567890"

