# Minimal Mocked Test Suite

This test suite uses fully mocked operations to avoid network calls and aligns with the current API routers and signatures.

## Run

```
cd apps/user_service/tests
PYTHONPATH=.. python -m pytest -v --tb=short
```

## Files

- conftest.py: global patches for operations to use an in-memory Supabase mock and ensure a running event loop.
- test_roles_api.py: roles list/get/create/update.
- test_users_api.py: users list/get/invite/update.
- test_permissions_api.py: permissions list.
- test_organisations_api.py: organisations list and details.
- test_sessions_api.py: sessions list.
- test_audit_logs_api.py: audit logs list.
