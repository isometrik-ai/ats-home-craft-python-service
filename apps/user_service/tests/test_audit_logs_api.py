# pylint: disable=all

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.audit_logs.audit_logs import router as audit_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(audit_router, prefix="/v1/admin")
    app.dependency_overrides[get_user_from_auth] = lambda: {"user_id": "u", "organization_id": "o", "email": "e@e.com"}
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_audit_logs_list_success(client):
    with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list", AsyncMock(return_value=[
        {
            "id": "a1",
            "organization_id": "o",
            "user_id": "u",
            "user_email": "e@e.com",
            "user_role": "Admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "r1",
            "old_values": None,
            "new_values": None,
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": 3232235777,
            "description": "created",
            "timestamp": "",
            "status_code": 200,
            "category": "audit_management"
        }
    ])), patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count", AsyncMock(return_value=1)):
        res = client.get("/v1/admin/audit-logs")
        assert res.status_code == 200
        assert res.json()["total_count"] == 1


