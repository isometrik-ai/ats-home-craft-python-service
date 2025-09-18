"""
Global test configuration: network-free mocks and shared fixtures.
"""

# pylint: disable=all

import asyncio
import pytest


# Reuse the in-memory Supabase mock from a local helper
def _build_supabase_mock():
    from types import SimpleNamespace
    import uuid

    class MockAuth:
        class MockAdmin:
            async def delete_user(self, user_id):
                return {"message": "User deleted"}

            async def invite_user_by_email(self, email, options=None):
                return SimpleNamespace(user=SimpleNamespace(id=str(uuid.uuid4())))

        def __init__(self):
            self.admin = self.MockAdmin()

    class MockTable:
        def __init__(self, table_name):
            self.table_name = table_name
        def select(self, *_, **__):
            return self
        def insert(self, _):
            return self
        def update(self, _):
            return self
        def delete(self):
            return self
        def eq(self, *_, **__):
            return self
        def neq(self, *_, **__):
            return self
        def ilike(self, *_, **__):
            return self
        def like(self, *_, **__):
            return self
        def order(self, *_, **__):
            return self
        def range(self, *_, **__):
            return self
        async def execute(self):
            return SimpleNamespace(data=[])

    class MockSupabase:
        def __init__(self):
            self.auth = MockAuth()
        def table(self, name):
            return MockTable(name)

    return MockSupabase()


@pytest.fixture(scope="session", autouse=True)
def patch_operations_to_use_mock_supabase():
    """Ensure all operations modules use the in-memory Supabase mock."""
    try:
        import libs.shared_db.postgres_db.user_service_operations.user_operations as user_ops
        import libs.shared_db.postgres_db.user_service_operations.role_operations as role_ops
        import libs.shared_db.postgres_db.user_service_operations.permission_operations as perm_ops
        import libs.shared_db.postgres_db.user_service_operations.organisation_operations as org_ops
        import libs.shared_db.postgres_db.user_service_operations.session_operations as session_ops
        import libs.shared_db.postgres_db.user_service_operations.audit_operations as audit_ops
        import apps.user_service.app.dependencies.common_utils as common_utils
        import apps.user_service.app.api.admin_management.roles as roles_api
        import apps.user_service.app.api.admin_management.users.users as users_api
        import apps.user_service.app.api.admin_management.users.user_profile as user_profile_api
        import apps.user_service.app.api.admin_management.permissions as perms_api
        import apps.user_service.app.api.admin_management.organisation as org_api
        import apps.user_service.app.api.admin_management.sessions.sessions as sessions_api
        import apps.user_service.app.api.audit_logs.audit_logs as audit_api
    except Exception:
        return

    async def _get_supabase_mock_async():
        return _build_supabase_mock()

    for module in [user_ops, role_ops, perm_ops, org_ops, session_ops, audit_ops]:
        setattr(module, "get_supabase_admin_client", _get_supabase_mock_async)

    # Patch common_utils references used by endpoints to avoid stale symbols
    if hasattr(user_ops, "get_user_profile_by_id"):
        common_utils.get_user_profile_by_id = user_ops.get_user_profile_by_id

    # Patch check_permissions at each API module to a simple allow mock
    async def _mock_check_permissions(current_user, _code, *_args):
        from types import SimpleNamespace
        org_id = current_user.get("organization_id") or current_user.get("organisation_id") or "o"
        user_id = current_user.get("user_id") or current_user.get("sub") or "u"
        email = current_user.get("email") or "e@e.com"
        user_type = current_user.get("user_type") or "organization_member"
        return SimpleNamespace(organization_id=org_id, user_id=user_id, email=email, user_type=user_type)

    for api_mod in [roles_api, users_api, user_profile_api, perms_api, org_api, sessions_api, audit_api]:
        setattr(api_mod, "check_permissions", _mock_check_permissions)

    # Disable rate limiting and audit decorator inside tests
    class _DummyLimiter:
        def limit(self, *_args, **_kwargs):
            def _decorator(fn):
                return fn
            return _decorator

    def _noop_audit(*_a, **_k):
        def _decorator(fn):
            return fn
        return _decorator

    for api_mod in [roles_api, users_api, user_profile_api, perms_api, org_api, sessions_api, audit_api]:
        setattr(api_mod, "limiter", _DummyLimiter())
        if hasattr(api_mod, "audit_api_call"):
            setattr(api_mod, "audit_api_call", _noop_audit)

    # Patch extract_user_context where used to return a rich context
    def _mock_extract_user_context(current_user):
        from types import SimpleNamespace
        return SimpleNamespace(
            user_id=current_user.get("user_id") or current_user.get("sub") or "u",
            email=current_user.get("email") or "e@e.com",
            organization_id=current_user.get("organization_id") or current_user.get("organisation_id") or "o",
            user_type=current_user.get("user_type") or "organization_member",
        )

    for api_mod in [org_api, sessions_api, audit_api, perms_api, user_profile_api]:
        setattr(api_mod, "extract_user_context", _mock_extract_user_context)

    # Patch require_permission in organisation API to a no-op
    async def _require_permission_noop(*_a, **_k):
        return True
    setattr(org_api, "require_permission", _require_permission_noop)


@pytest.fixture(scope="session", autouse=True)
def ensure_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


