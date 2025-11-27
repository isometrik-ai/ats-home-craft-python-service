# pylint: disable=all

"""
Additional test cases for user profile API to increase coverage.
Tests edge cases, error paths, and different scenarios.
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, Request
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
    # Mock the JWT auth dependency
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


def _build_mock_supabase_user(metadata_override=None, identities_override=None):
    """Helper to build Supabase user data with metadata and identities."""
    from types import SimpleNamespace
    
    base_metadata = {
        "first_name": "Test",
        "last_name": "User",
        "full_name": "Test User",
        "avatar_url": "https://example.com/avatar.jpg",
        "phone": "+1234567890",
        "timezone": "UTC",
        "email": "test@example.com",
    }
    if metadata_override:
        base_metadata.update(metadata_override)

    timestamp = datetime.now(timezone.utc).isoformat()
    default_identity = SimpleNamespace(
        provider="email",
        created_at=timestamp,
        updated_at=timestamp,
        last_sign_in_at=timestamp,
        identity_data={
            "email": base_metadata.get("email", "test@example.com"),
        },
    )

    identities = identities_override if identities_override is not None else [
        default_identity
    ]

    return SimpleNamespace(
        user=SimpleNamespace(
            email=base_metadata.get("email", "test@example.com"),
            phone=base_metadata.get("phone"),
            user_metadata=base_metadata,
            identities=identities,
        )
    )


def test_get_user_profile_with_email_change(client):
    """Test user profile with pending email change."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="old@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "old@example.com"
    mock_user_data.user.email_change = "new@example.com"  # Pending email change

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "old@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        # Should use email_change as current email
        assert res.json()["data"]["email"] == "new@example.com"


def test_get_user_profile_with_phone_in_metadata(client):
    """Test user profile with phone in user_metadata."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"
    mock_user_data.user.phone = None
    mock_user_data.user.user_metadata = {"phone": "+9876543210"}

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        assert res.json()["data"]["phone"] == "+9876543210"


def test_get_user_profile_with_phone_change(client):
    """Test user profile with pending phone change."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"
    mock_user_data.user.phone = None
    mock_user_data.user.phone_change = "+9999999999"
    mock_user_data.user.user_metadata = {}

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        assert res.json()["data"]["phone"] == "+9999999999"


def test_get_user_profile_identities_with_provider_id(client):
    """Test user profile with non-email identity provider."""
    from apps.user_service.app.dependencies.common_utils import UserContext
    from types import SimpleNamespace

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_identity = SimpleNamespace(
        provider="google",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        identity_data={"provider_id": "google-123", "sub": "google-123"}
    )

    mock_user_data = _build_mock_supabase_user(
        identities_override=[mock_identity]
    )
    mock_user_data.user.email = "test@example.com"

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        identities = res.json()["data"]["identities"]
        assert len(identities) == 1
        assert identities[0]["provider"] == "google"
        assert identities[0]["provider_id"] == "google-123"


def test_get_user_profile_identities_fallback_to_jwt(client):
    """Test user profile when identities fetch fails, falls back to JWT."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(side_effect=Exception("API error"))), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        identities = res.json()["data"]["identities"]
        # Should have fallback identity from JWT
        assert len(identities) == 1
        assert identities[0]["provider"] == "email"
        assert identities[0]["provider_id"] == "test@example.com"


def test_get_user_profile_with_organization_permissions(client):
    """Test user profile with organization and permissions."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"

    mock_permissions = [
        {"id": str(uuid.uuid4()), "name": "read_users", "code": "READ_USERS", "category": "users"},
        {"id": str(uuid.uuid4()), "name": "write_users", "code": "WRITE_USERS", "category": "users"}
    ]

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Admin role",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=mock_permissions)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity",
               AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        permissions = res.json()["data"]["permissions"]
        assert len(permissions) == 2
        assert permissions[0]["permission_name"] == "read_users"
        assert permissions[1]["permission_name"] == "write_users"


def test_get_user_profile_with_verification_preference(client):
    """Test user profile with verification preference in metadata."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"
    mock_user_data.user.user_metadata = {
        "verification_preference": {
            "enabled": True,
            "type": "PHONE"
        }
    }

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Admin role",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity",
               AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "verification_preference" in data
        assert data["verification_preference"] is not None
        assert data["verification_preference"]["enabled"] is True
        assert data["verification_preference"]["type"] == "PHONE"


def test_get_user_profile_without_verification_preference(client):
    """Test user profile without verification preference in metadata."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"
    mock_user_data.user.user_metadata = {}  # No verification_preference

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Admin role",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity",
               AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "verification_preference" in data
        assert data["verification_preference"] is None


def test_get_user_profile_verification_preference_email(client):
    """Test user profile with EMAIL verification preference."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"
    mock_user_data.user.user_metadata = {
        "verification_preference": {
            "enabled": False,
            "type": "EMAIL"
        }
    }

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value={
                   "user_id": "u1", "email": "test@example.com", "full_name": "Test User",
                   "first_name": "Test", "last_name": "User", "status": "active",
                   "role_id": str(uuid.uuid4()), "role_name": "Admin", "role_description": "Admin role",
                   "organization_id": str(uuid.uuid4()), "phone": None, "timezone": "UTC",
                   "avatar_url": None, "joined_at": datetime.now(timezone.utc), "last_active_at": None,
                   "salutation": None
               })), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity",
               AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "verification_preference" in data
        assert data["verification_preference"] is not None
        assert data["verification_preference"]["enabled"] is False
        assert data["verification_preference"]["type"] == "EMAIL"


def test_get_user_profile_verification_preference_no_organization(client):
    """Test user profile with verification preference when user has no organization."""
    from apps.user_service.app.dependencies.common_utils import UserContext

    mock_user_context = UserContext(
        organization_id=None,
        user_id=str(uuid.uuid4()),
        email="test@example.com",
        user_type="organization_member"
    )

    mock_user_data = _build_mock_supabase_user()
    mock_user_data.user.email = "test@example.com"
    mock_user_data.user.user_metadata = {
        "verification_preference": {
            "enabled": True,
            "type": "PHONE"
        }
    }

    with patch("apps.user_service.app.api.admin_management.users.user_profile.extract_user_context",
               AsyncMock(return_value=mock_user_context)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_profile_by_id",
               AsyncMock(return_value=None)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_by_id",
               AsyncMock(return_value=mock_user_data)), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.get_user_permissions",
               AsyncMock(return_value=[])), \
         patch("apps.user_service.app.api.admin_management.users.user_profile.update_user_activity",
               AsyncMock()):
        res = client.get("/v1/admin/users/profile")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "verification_preference" in data
        assert data["verification_preference"] is not None
        assert data["verification_preference"]["enabled"] is True
        assert data["verification_preference"]["type"] == "PHONE"
