# pylint: disable=all

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from fastapi import FastAPI
    from apps.user_service.app.api.admin_management.organisation import router as org_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth
    from apps.user_service.app.dependencies.common_utils import check_user_access_async

    app = FastAPI()
    app.include_router(org_router, prefix="/v1/admin")
    app.dependency_overrides[get_user_from_auth] = lambda: {"user_id": "u", "organization_id": "o", "email": "e@e.com"}
    app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_organisations_list_success(client):
    with patch("apps.user_service.app.api.admin_management.organisation.get_list_of_organisations", AsyncMock(return_value=[
        {"organization_id": "o", "name": "Org", "slug": "org", "domain": "example.com", "logo_url": None, "plan_type": "free", "status": "active", "max_users": 10, "timezone": "UTC", "created_at": "", "updated_at": "", "member_count": 0}
    ])), patch("apps.user_service.app.api.admin_management.organisation.get_organisations_count", AsyncMock(return_value=1)):
        res = client.get("/v1/admin/organisation/list")
        assert res.status_code == 200
        assert res.json()["total_count"] == 1


def test_organisation_details_success(client):
    valid_id = "00000000-0000-0000-0000-000000000000"
    with patch("apps.user_service.app.api.admin_management.organisation.get_organisation_details_by_id", AsyncMock(return_value={
    "organization_id": valid_id, "name": "Org", "slug": "org", "domain": "example.com", "logo_url": None, "plan_type": "free", "status": "active", "max_users": 10, "timezone": "UTC", "created_at": "", "updated_at": "", "member_count": 0
    })):  
        res = client.get(f"/v1/admin/organisation/{valid_id}")
        assert res.status_code == 200
        assert res.json()["data"]["organization_id"] == valid_id


