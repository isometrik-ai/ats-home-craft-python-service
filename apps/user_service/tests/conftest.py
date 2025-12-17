"""Global test configuration: network-free mocks and shared fixtures."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

# Reuse the in-memory Supabase mock from a local helper


def _build_supabase_mock():
    """Build a mock Supabase client."""
    # pylint: disable=too-complex

    class MockAuth:
        """Mock authentication client."""

        class MockAdmin:
            """Mock admin client."""

            async def delete_user(self):
                """Delete a user."""
                return {"message": "User deleted"}

            async def invite_user_by_email(self):
                """Invite a user by email."""
                return SimpleNamespace(user=SimpleNamespace(id=str(uuid.uuid4())))

        def __init__(self):
            """Initialize the mock authentication client."""
            self.admin = self.MockAdmin()

    class MockTable:
        """Mock table client."""

        def __init__(self, table_name: str):
            """Initialize the mock table client."""
            self.table_name = table_name

        def select(self, *_, **__):
            """Select a record."""
            return self

        def insert(self, _):
            """Insert a record."""
            return self

        def update(self, _):
            """Update a record."""
            return self

        def delete(self):
            """Delete a record."""
            return self

        def neq(self, *_, **__):
            """Not equal to."""
            return self

        def ilike(self, *_, **__):
            """Case-insensitive like."""
            return self

        def like(self, *_, **__):
            """Like."""
            return self

        def or_(self, *_, **__):
            """Or."""
            return self

        def order(self, *_, **__):
            """Order by."""
            return self

        def range(self, *_, **__):
            """Range."""
            return self

        async def execute(self):
            """Execute the query."""
            return SimpleNamespace(data=[], count=0)

    class MockSupabase:
        """Mock Supabase client."""

        def __init__(self):
            """Initialize the mock Supabase client."""
            self.auth = MockAuth()

        def table(self, name: str):
            """Table."""
            return MockTable(table_name=name)

    return MockSupabase()


@pytest.fixture(scope="session", autouse=True)
def patch_operations_to_use_mock_supabase():
    """Ensure all operations modules use the in-memory Supabase mock."""
    # pylint: disable=too-complex
    try:
        import apps.user_service.app.api.admin_management.organisation as org_api
        import apps.user_service.app.api.admin_management.permissions as perms_api
        import apps.user_service.app.api.admin_management.roles as roles_api
        import apps.user_service.app.api.admin_management.sessions.sessions as sessions_api
        import apps.user_service.app.api.admin_management.users.users as users_api
        import apps.user_service.app.api.audit_logs.audit_logs as audit_api
        import apps.user_service.app.api.auth as auth_api
        import libs.shared_db.postgres_db.user_service_operations.audit_operations as audit_ops
        import libs.shared_db.postgres_db.user_service_operations.organisation_operations as org_ops
        import libs.shared_db.postgres_db.user_service_operations.permission_operations as perm_ops
        import libs.shared_db.postgres_db.user_service_operations.role_operations as role_ops
        import libs.shared_db.postgres_db.user_service_operations.session_operations as session_ops
        import libs.shared_db.postgres_db.user_service_operations.user_operations as user_ops

        # Import Supabase admin operations where real admin client is used by common_utils
        import libs.shared_db.supabase_db.admin_operations.user as supa_admin_user
        import libs.shared_db.supabase_db.db as supa_db
        from apps.user_service.app.utils import common_utils
    except Exception:
        return

    async def _get_supabase_mock_async():
        return _build_supabase_mock()

    for module in [user_ops, role_ops, perm_ops, org_ops, session_ops, audit_ops]:
        module.get_supabase_admin_client = _get_supabase_mock_async
    # Also patch common_utils and the core supabase db module to avoid real client creation
    common_utils.get_supabase_admin_client = _get_supabase_mock_async
    supa_db.get_supabase_admin_client = _get_supabase_mock_async
    # Patch Supabase admin operations module used by extract_user_context (get_user_by_id)
    supa_admin_user.get_supabase_admin_client = _get_supabase_mock_async

    # Patch common_utils references used by endpoints to avoid stale symbols
    if hasattr(user_ops, "get_user_profile_by_id"):
        common_utils.get_user_profile_by_id = user_ops.get_user_profile_by_id

    # Patch check_permissions at each API module to a simple allow mock
    async def _mock_check_permissions(
        current_user: dict,
    ) -> SimpleNamespace:
        """Mock check permissions."""
        org_id = current_user.get("organization_id") or current_user.get("organisation_id") or "o"
        user_id = current_user.get("user_id") or current_user.get("sub") or "u"
        email = current_user.get("email") or "e@e.com"
        user_type = current_user.get("user_type") or "organization_member"
        return SimpleNamespace(
            organization_id=org_id, user_id=user_id, email=email, user_type=user_type
        )

    for api_mod in [
        roles_api,
        users_api,
        perms_api,
        org_api,
        sessions_api,
        audit_api,
    ]:
        api_mod.check_permissions = _mock_check_permissions

    # Disable rate limiting and audit decorator inside tests
    def _noop_audit(*_a, **_k):
        """Noop audit decorator."""

        def _decorator(fn):
            return fn

        return _decorator

    try:
        from apps.user_service.app import app_instance

        if getattr(app_instance, "limiter", None):
            app_instance.limiter.enabled = False
    except Exception:
        pass

    for api_mod in [
        roles_api,
        users_api,
        perms_api,
        org_api,
        sessions_api,
        audit_api,
        auth_api,
    ]:
        if getattr(api_mod, "limiter", None):
            api_mod.limiter.enabled = False
        if hasattr(api_mod, "audit_api_call"):
            api_mod.audit_api_call = _noop_audit

    # Patch extract_user_context where used to return a rich context
    def _mock_extract_user_context(current_user):
        """Mock extract user context."""

        return SimpleNamespace(
            user_id=current_user.get("user_id") or current_user.get("sub") or "u",
            email=current_user.get("email") or "e@e.com",
            organization_id=current_user.get("organization_id")
            or current_user.get("organisation_id")
            or "o",
            user_type=current_user.get("user_type") or "organization_member",
        )

    for api_mod in [org_api, sessions_api, audit_api, perms_api]:
        api_mod.extract_user_context = _mock_extract_user_context

    # Patch require_permission in organisation API to a no-op
    async def _require_permission_noop(*_a, **_k):
        """Mock require permission noop."""
        return True

    org_api.require_permission = _require_permission_noop


@pytest.fixture(scope="session", autouse=True)
def ensure_loop():
    """Ensure event loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
