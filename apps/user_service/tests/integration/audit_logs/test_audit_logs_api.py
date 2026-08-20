"""Integration tests for audit logs endpoints."""

from datetime import date

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_get_audit_logs_with_filters(monkeypatch, client):
    """Should pass list filters through to the service layer."""

    captured: dict = {}

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_check_user_access_async(*args, **kwargs):
        del args, kwargs
        return True

    async def fake_get_audit_logs(self, filter_params):
        del self
        captured["filter_params"] = filter_params
        return {"audit_logs": [{"id": "log-1", "description": "did something"}], "total_count": 1}

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_user_access_async",
        fake_check_user_access_async,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.audit_log_service.AuditLogService.get_audit_logs",
        fake_get_audit_logs,
    )

    res = await client.get(
        "/v1/audit-logs"
        "?page=1&page_size=10"
        "&action_type=UPDATE"
        "&category=CONTACT"
        "&risk_level=medium"
        "&start_date=2026-01-01"
        "&end_date=2026-01-31"
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "log-1"
    assert body["total"] == 1

    filters = captured["filter_params"]
    assert filters.action_type.value == "UPDATE"
    assert filters.category == "CONTACT"
    assert filters.risk_level.value == "medium"
    assert filters.start_date == date(2026, 1, 1)
    assert filters.end_date == date(2026, 1, 31)


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", ["CREATE", "UPDATE", "DELETE"])
async def test_get_audit_logs_accepts_each_action_type(monkeypatch, client, action_type):
    """Each supported action_type query value is forwarded to the service."""
    captured: dict = {}

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_check_user_access_async(*args, **kwargs):
        del args, kwargs
        return True

    async def fake_get_audit_logs(self, filter_params):
        del self
        captured["action_type"] = filter_params.action_type
        return {"audit_logs": [], "total_count": 0}

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_user_access_async",
        fake_check_user_access_async,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.audit_log_service.AuditLogService.get_audit_logs",
        fake_get_audit_logs,
    )

    res = await client.get(f"/v1/audit-logs?action_type={action_type}")
    body = assert_success(res, 200)

    assert body["total"] == 0
    assert captured["action_type"].value == action_type


@pytest.mark.asyncio
async def test_get_audit_logs_rejects_invalid_action_type(client):
    """Unknown action_type returns 422 validation error."""
    res = await client.get("/v1/audit-logs?action_type=READ")

    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_audit_logs_rejects_invalid_risk_level(client):
    """Unknown risk_level returns 422 validation error."""
    res = await client.get("/v1/audit-logs?risk_level=critical")

    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_audit_logs_rejects_inverted_date_range(monkeypatch, client):
    """end_date before start_date returns 422 validation error."""

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_check_user_access_async(*args, **kwargs):
        del args, kwargs
        return True

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_user_access_async",
        fake_check_user_access_async,
    )

    res = await client.get("/v1/audit-logs?start_date=2026-02-01&end_date=2026-01-01")

    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_audit_logs_scopes_user_without_system_permission(monkeypatch, client):
    """Users without system visibility always filter to their own user_id."""
    captured: dict = {}

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="member"
        )

    async def fake_check_user_access_async(*args, **kwargs):
        del args, kwargs
        return False

    async def fake_get_audit_logs(self, filter_params):
        del self
        captured["user_id"] = filter_params.user_id
        return {"audit_logs": [], "total_count": 0}

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_user_access_async",
        fake_check_user_access_async,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.audit_log_service.AuditLogService.get_audit_logs",
        fake_get_audit_logs,
    )

    res = await client.get("/v1/audit-logs?user_id=other-user&action_type=DELETE")

    assert_success(res, 200)
    assert captured["user_id"] == "u1"


@pytest.mark.asyncio
async def test_get_audit_logs(monkeypatch, client):
    """Should return paginated audit logs."""

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        # Minimal JWT claims shape expected by extract_user_context
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_check_user_access_async(*args, **kwargs):
        del args, kwargs
        return True

    async def fake_get_audit_logs(self, filter_params):
        del self, filter_params
        return {"audit_logs": [{"id": "log-1", "description": "did something"}], "total_count": 1}

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_user_access_async",
        fake_check_user_access_async,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.audit_log_service.AuditLogService.get_audit_logs",
        fake_get_audit_logs,
    )

    res = await client.get("/v1/audit-logs?page=1&page_size=10")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "log-1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_audit_log_by_id(monkeypatch, client):
    """Should return audit log detail by id."""

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_get_by_id(self, audit_log_id):
        del self
        assert audit_log_id == "log-1"
        return {"id": "log-1", "description": "detail"}

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.audit_log_service.AuditLogService.get_audit_log_by_id",
        fake_get_by_id,
    )

    res = await client.get("/v1/audit-logs/log-1")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "log-1"


@pytest.mark.asyncio
async def test_delete_all_audit_logs(monkeypatch, client):
    """Should delete all audit logs."""

    async def fake_get_user_from_auth(*args, **kwargs):
        del args, kwargs
        return {"sub": "u1", "email": "u1@example.com", "session_id": "sess-1"}

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_delete_all(self):
        del self
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.get_user_from_auth",
        fake_get_user_from_auth,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.audit_logs.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.audit_log_service.AuditLogService.delete_all_audit_logs",
        fake_delete_all,
    )

    res = await client.delete("/v1/audit-logs")
    body = assert_success(res, 200)
    assert body["code"]  # ensure custom code present
