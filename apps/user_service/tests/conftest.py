"""Shared pytest fixtures for user_service tests."""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

# Ensure project root is on sys.path for "apps" imports when running tests directly
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import app modules after path setup using importlib to avoid E402
_lifespan = importlib.import_module("apps.user_service.app.lifespan")
lifespan_module = _lifespan

_db = importlib.import_module("apps.user_service.app.dependencies.db")
db_conn = _db.db_conn
db_uow = _db.db_uow

_supabase = importlib.import_module("apps.user_service.app.dependencies.supabase")
supabase_anon = _supabase.supabase_anon
supabase_anon_client_with_headers = _supabase.supabase_anon_client_with_headers
supabase_service = _supabase.supabase_service

_main = importlib.import_module("apps.user_service.app.main")
app = _main.app

_jwt = importlib.import_module("libs.shared_middleware.jwt_auth")
jwt_auth = _jwt


class _FakeTransaction:
    """Fake transaction context manager for tests."""

    async def __aenter__(self):
        """Enter transaction context."""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """Exit transaction context."""
        return False


class FakeConn:
    """Minimal asyncpg-like connection used in tests."""

    async def fetch(self, *_args, **_kwargs):
        """Fetch multiple rows."""
        return []

    async def fetchrow(self, *_args, **_kwargs):
        """Fetch a single row."""
        return None

    async def fetchval(self, *_args, **_kwargs):
        """Fetch a single value."""
        return None

    async def execute(self, *_args, **_kwargs):
        """Execute a query."""
        return None

    def transaction(self):
        """Create a fake transaction."""
        return _FakeTransaction()


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class MockPool:
    """Mock database pool for tests."""

    async def acquire(self, timeout=None):
        """Acquire a fake connection."""
        del timeout  # Unused but part of API
        return FakeConn()

    async def release(self, conn):
        """Release a connection (no-op)."""

    async def close(self):
        """Close the pool (no-op)."""


async def _noop(*_args, **_kwargs):
    """No-op function for mocking."""
    return None


async def _mock_get_pool():
    """Mock get_pool function."""
    return MockPool()


@pytest.fixture(autouse=True, scope="function")
def patch_lifespan(monkeypatch):
    """Prevent real DB pool/audit startup during tests."""

    monkeypatch.setattr(lifespan_module, "get_pool", _mock_get_pool)
    monkeypatch.setattr("libs.shared_db.drivers.asyncpg_client.get_pool", _mock_get_pool)
    monkeypatch.setattr(lifespan_module.audit_logger, "start_processing", _noop)
    monkeypatch.setattr(lifespan_module, "close_pool", _noop)
    monkeypatch.setattr("libs.shared_db.drivers.asyncpg_client.close_pool", _noop)


@pytest.fixture(autouse=True, scope="function")
def mock_jwt_middleware(monkeypatch):
    """Mock JWT middleware to always set request.state.user for tests."""
    from libs.shared_middleware.jwt_auth import JWTAuthMiddleware

    async def mock_dispatch(_self, request, call_next):
        """Mock dispatch that always sets a test user."""
        # Always set a test user for authenticated endpoints
        # This allows tests to work without requiring actual JWT tokens
        if not hasattr(request.state, "user") or request.state.user is None:
            request.state.user = {
                "sub": "test-user-id",
                "email": "test@example.com",
                "user_metadata": {"organization_id": "org-123", "type": "organization_member"},
            }
        # Call the original dispatch or just call_next
        return await call_next(request)

    # Patch the dispatch method
    monkeypatch.setattr(JWTAuthMiddleware, "dispatch", mock_dispatch)


class MockUser:
    """Mock user object for tests."""

    def __init__(self, user_id=None, email="test@example.com", user_metadata=None):
        """Initialize mock user."""
        self.id = user_id or "test-user-id"
        self.email = email
        self.user_metadata = user_metadata or {}
        self.app_metadata = {"providers": []}

    def model_dump(self):
        """Dump user as dictionary."""
        return {
            "id": self.id,
            "email": self.email,
            "user_metadata": self.user_metadata,
            "app_metadata": self.app_metadata,
        }


class MockResponse:
    """Mock response object for tests."""

    def __init__(self, user=None, properties=None, users=None):
        """Initialize mock response."""
        if user is not None:
            self.user = user
        if properties is not None:
            self.properties = properties
        if users is not None:
            self.users = users


class MockProperties:
    """Mock properties object for tests."""

    def __init__(self):
        """Initialize mock properties."""
        self.action_link = "mock-link"


class MockAuthAdmin:
    """Mock Supabase auth admin client for tests."""

    async def get_user_by_id(self, user_id=None, uid=None):
        """Get user by ID."""
        mock_user = MockUser(user_id=user_id or uid)
        return MockResponse(user=mock_user)

    async def update_user_by_id(self, user_id=None, attributes=None, **kwargs):
        """Update user by ID."""
        user_id_value = user_id or kwargs.get("id", "test-user-id")
        email = (
            (attributes or {}).get("email", "test@example.com")
            if attributes
            else "test@example.com"
        )
        user_metadata = (attributes or {}).get("user_metadata", {}) if attributes else {}
        mock_user = MockUser(user_id=user_id_value, email=email, user_metadata=user_metadata)
        return MockResponse(user=mock_user)

    async def create_user(self, **kwargs):
        """Create a new user."""
        user_data = kwargs if isinstance(kwargs, dict) else {}
        mock_user = MockUser(
            user_id="test-created-user-id",
            email=user_data.get("email", "test@example.com"),
            user_metadata=user_data.get("user_metadata", {}),
        )
        return MockResponse(user=mock_user)

    async def delete_user(self, *_args, **_kwargs):
        """Delete a user."""
        return {"success": True}

    async def list_users(self, _page=1, _per_page=50):
        """List users."""
        return MockResponse(users=[])

    async def generate_link(self, **_kwargs):
        """Generate a link."""
        return MockResponse(properties=MockProperties())


class MockAuth:
    """Mock Supabase auth client for tests."""

    def __init__(self):
        """Initialize mock auth client."""
        self.admin = MockAuthAdmin()
        self._storage_key = "test_storage_key"
        self._in_memory_session = None
        self._persist_session = False
        self._storage = type(
            "Storage",
            (),
            {
                "set_item": lambda _self, _k, _v: None,
                "get_item": lambda _self, _k: None,
            },
        )()

    async def get_user(self, _access_token):
        """Get user from access token.
        Returns:
            dict: The user.
        """
        del _access_token
        return {
            "id": "test-user-id",
            "email": "test@example.com",
            "user_metadata": {},
        }

    async def sign_up(self, **kwargs):
        """Sign up a new user.
        Args:
            **kwargs: The keyword arguments.
        Returns:
            dict: The user.
        """
        email = kwargs.get("email", "test@example.com")
        options = kwargs.get("options", {})
        user_metadata = options.get("data", {}) if isinstance(options, dict) else {}
        mock_user = MockUser(user_id="test-user-id", email=email, user_metadata=user_metadata)
        return MockResponse(user=mock_user)

    async def sign_in_with_password(self, **kwargs):
        """Sign in with password.
        Args:
            **kwargs: dict: The keyword arguments.
        Returns:
            dict: The user.
        """
        return {
            "user": {
                "id": "test-user-id",
                "email": kwargs.get("email", "test@example.com"),
            },
            "session": {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
            },
        }

    async def reset_password_email(self, _email):
        """Send reset password email.
        Returns:
            dict: The message.
        """
        del _email
        return {"message": "Password reset email sent"}

    async def refresh_session(self, refresh_token: str):
        """Refresh session.
        Args:
            refresh_token (str): The refresh token to refresh the session with.
        Returns:
            dict: The session.
        """
        return {
            "user": {
                "id": "test-user-id",
                "email": "test@example.com",
            },
            "session": {
                "access_token": "test-new-access-token",
                "refresh_token": refresh_token,
            },
        }


class MockTable:
    """Mock Supabase table client for tests."""

    def __init__(self, table_name):
        """Initialize mock table."""
        self.table_name = table_name

    def select(self, *_args, **_kwargs):
        """Select query builder.
        Args:
            *_args: The arguments.
            **_kwargs: The keyword arguments.
        Returns:
            self: The query builder.
        """
        return self

    def insert(self, *_args, **_kwargs):
        """Insert query builder.
        Args:
            *_args: The arguments.
            **_kwargs: The keyword arguments.
        Returns:
            self: The query builder.
        """
        return self

    def update(self, *_args, **_kwargs):
        """Update query builder.
        Args:
            *_args: The arguments.
            **_kwargs: The keyword arguments.
        Returns:
            self: The query builder.
        """
        return self

    def delete(self, *_args, **_kwargs):
        """Delete query builder.
        Args:
            *_args: The arguments.
            **_kwargs: The keyword arguments.
        Returns:
            self: The query builder.
        """
        return self

    def _equality_filter(self, *_args, **_kwargs):
        """Equality filter implementation.
        Args:
            *_args: The arguments.
            **_kwargs: The keyword arguments.
        Returns:
            self: The query builder.
        """
        return self

    def __getattr__(self, name):
        """Handle dynamic method access for API compatibility."""
        if name == "eq":
            return self._equality_filter
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    async def execute(self):
        """Execute query.
        Returns:
            Response: The response.
        """
        return type("Response", (), {"data": [], "count": 0})()


class _StubSupabase:
    """Comprehensive mock Supabase client for tests."""

    def __init__(self):
        """Initialize stub Supabase client."""
        self.auth = MockAuth()
        self._tables = {}

    def table(self, table_name: str):
        """Get table client.
        Args:
            table_name (str): The name of the table to get the client for.
        Returns:
            MockTable: The table client.
        """
        if table_name not in self._tables:
            self._tables[table_name] = MockTable(table_name)
        return self._tables[table_name]

    async def rpc(self, _function_name, *_args, **_kwargs):
        """Call RPC function.
        Args:
            *_args: The arguments.
            **_kwargs: The keyword arguments.
        Returns:
            RPCResponse: The RPC response.
        """
        del _function_name
        return type("RPCResponse", (), {"data": None})()

    async def execute(self):
        """Execute query.
        Returns:
            Response: The response.
        """
        return type("Resp", (), {"data": True})()


async def _stub_supabase():
    """Create stub Supabase client."""
    return _StubSupabase()


async def _mock_get_supabase_client():
    """Mock get_supabase_client function."""
    return _StubSupabase()


async def _mock_get_supabase_service_client():
    """Mock get_supabase_service_client function."""
    return _StubSupabase()


def _get_user_from_auth(request: Request) -> dict:
    """Mock get_user_from_auth that sets request.state.user and returns user."""
    user = {
        "sub": "test-user-id",
        "email": "test@example.com",
        "user_metadata": {"organization_id": "org-123", "type": "organization_member"},
    }
    # Set request.state.user before get_user_from_auth checks for it
    request.state.user = user
    return user


async def _fake_db_conn():
    """Fake database connection generator."""
    yield FakeConn()


async def _fake_db_uow():
    """Fake database unit of work generator."""
    yield FakeConn()


@pytest.fixture(autouse=True)
def override_dependencies(monkeypatch):
    """Override DB, Supabase, and auth dependencies to use fast fakes."""
    monkeypatch.setattr(
        "libs.shared_db.supabase_db.client.get_supabase_client", _mock_get_supabase_client
    )
    monkeypatch.setattr(
        "libs.shared_db.supabase_db.client.get_supabase_service_client",
        _mock_get_supabase_service_client,
    )

    app.dependency_overrides[db_conn] = _fake_db_conn
    app.dependency_overrides[db_uow] = _fake_db_uow
    app.dependency_overrides[supabase_anon] = _stub_supabase
    app.dependency_overrides[supabase_service] = _stub_supabase
    app.dependency_overrides[supabase_anon_client_with_headers] = _stub_supabase
    monkeypatch.setattr(jwt_auth, "get_user_from_auth", _get_user_from_auth)

    yield
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client():
    """Create test HTTP client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
