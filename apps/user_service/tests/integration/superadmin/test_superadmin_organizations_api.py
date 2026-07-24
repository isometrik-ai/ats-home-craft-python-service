"""Integration tests for superadmin organization endpoints."""

import pytest

from apps.user_service.app.schemas.superadmin_organizations import (
    SuperadminImpersonationResponse,
)
from apps.user_service.tests.utils.assertions import assert_success

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"

_FAKE_ORG_LIST_ITEM = {
    "organization_id": ORG_ID,
    "name": "Acme Org",
    "admin": {"user_id": "owner-1", "full_name": "Owner", "email": "owner@example.com"},
    "member_count": 5,
    "plan_type": "trial",
    "status": "active",
    "created_at": "2026-01-01T00:00:00Z",
}

_FAKE_ORG_DETAIL = {
    "organization_id": ORG_ID,
    "name": "Acme Org",
    "status": "active",
    "plan_type": "trial",
}


def _patch_superadmin(monkeypatch) -> None:
    """Bypass superadmin role check."""

    async def fake_require_super_admin(current_user):
        del current_user

    monkeypatch.setattr(
        "apps.user_service.app.api.superadmin_organizations.require_super_admin",
        fake_require_super_admin,
    )


@pytest.mark.asyncio
async def test_superadmin_list_orgs(monkeypatch, client):
    """GET /superadmin/organizations lists orgs."""

    _patch_superadmin(monkeypatch)

    async def fake_list(
        _self,
        *,
        page,
        page_size,
        search=None,
        plan=None,
        status=None,
        sort=None,
        order=None,
    ):
        del _self, search, plan, status, sort, order
        assert page == 1
        assert page_size == 20
        return type(
            "Result",
            (),
            {"items": [_FAKE_ORG_LIST_ITEM], "total_count": 1},
        )()

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.list_organizations",
        fake_list,
    )

    res = await client.get("/v1/superadmin/organizations?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["organization_id"] == ORG_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_superadmin_list_orgs_empty(monkeypatch, client):
    """GET /superadmin/organizations returns empty list."""

    _patch_superadmin(monkeypatch)

    async def fake_list(_self, **kwargs):
        del _self, kwargs
        return type("Result", (), {"items": [], "total_count": 0})()

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.list_organizations",
        fake_list,
    )

    res = await client.get("/v1/superadmin/organizations?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_superadmin_get_org(monkeypatch, client):
    """GET /superadmin/organizations/{id} returns detail."""

    _patch_superadmin(monkeypatch)

    async def fake_get_detail(_self, organization_id: str):
        del _self
        assert organization_id == ORG_ID
        return type(
            "Detail",
            (),
            {"model_dump": lambda self=None, **_k: _FAKE_ORG_DETAIL},
        )()

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.get_organization_detail",
        fake_get_detail,
    )

    res = await client.get(f"/v1/superadmin/organizations/{ORG_ID}")
    body = assert_success(res, 200)
    assert body["data"]["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_superadmin_create_org(monkeypatch, client):
    """POST /superadmin/organizations creates an org."""

    _patch_superadmin(monkeypatch)

    async def fake_create(_self, *, owner_user_id: str, body):
        del _self, body
        assert owner_user_id == "test-user-id"
        return {
            "organization_id": ORG_ID,
            "organization_name": "New Org",
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.create_organization",
        fake_create,
    )

    res = await client.post(
        "/v1/superadmin/organizations",
        json={
            "user_data": {
                "first_name": "Jane",
                "last_name": "Doe",
            },
            "company_data": {
                "company_name": "New Org",
                "primary_practice_areas": ["Web Development"],
            },
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_superadmin_delete_org(monkeypatch, client):
    """DELETE /superadmin/organizations/{id} removes org."""

    _patch_superadmin(monkeypatch)

    async def fake_delete(_self, organization_id: str, *, actor_user_id, actor_email):
        del _self, actor_user_id, actor_email
        assert organization_id == ORG_ID
        return {"organization_id": ORG_ID, "organization_name": "Acme Org"}

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.permanently_delete_organization",
        fake_delete,
    )

    res = await client.delete(f"/v1/superadmin/organizations/{ORG_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_superadmin_suspend_org(monkeypatch, client):
    """POST /superadmin/organizations/{id}/suspend suspends org."""

    _patch_superadmin(monkeypatch)

    async def fake_suspend(_self, organization_id: str):
        del _self
        assert organization_id == ORG_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.suspend_organization",
        fake_suspend,
    )

    res = await client.post(f"/v1/superadmin/organizations/{ORG_ID}/suspend")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_superadmin_reactivate_org(monkeypatch, client):
    """POST /superadmin/organizations/{id}/reactivate restores org."""

    _patch_superadmin(monkeypatch)

    async def fake_reactivate(_self, organization_id: str):
        del _self
        assert organization_id == ORG_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.reactivate_organization",
        fake_reactivate,
    )

    res = await client.post(f"/v1/superadmin/organizations/{ORG_ID}/reactivate")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_superadmin_impersonate_org(monkeypatch, client):
    """POST /superadmin/organizations/{id}/impersonate issues session."""

    _patch_superadmin(monkeypatch)

    fake_data = SuperadminImpersonationResponse(
        access_token="imp-token",
        organization_id=ORG_ID,
        organization_name="Acme Org",
        impersonated_user_id="owner-1",
    )

    async def fake_impersonate(_self, *, organization_id: str, supabase_admin_client):
        del _self, supabase_admin_client
        assert organization_id == ORG_ID
        return fake_data

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.impersonate_organization_owner",
        fake_impersonate,
    )

    res = await client.post(f"/v1/superadmin/organizations/{ORG_ID}/impersonate")
    body = assert_success(res, 200)
    assert body["data"]["access_token"] == "imp-token"
    assert body["data"]["impersonated_user_id"] == "owner-1"


@pytest.mark.asyncio
async def test_superadmin_exit_impersonation(monkeypatch, client):
    """POST /superadmin/organizations/impersonate/exit revokes session."""

    async def fake_exit(_self, *, current_user):
        del _self
        assert current_user["sub"] == "test-user-id"
        return {"organization_id": ORG_ID, "revoked": True}

    monkeypatch.setattr(
        "apps.user_service.app.services.superadmin_organization_service."
        "SuperadminOrganizationService.exit_impersonation_session",
        fake_exit,
    )

    res = await client.post("/v1/superadmin/organizations/impersonate/exit")
    body = assert_success(res, 200)
    assert body["data"]["organization_id"] == ORG_ID
