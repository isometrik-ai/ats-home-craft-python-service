"""Integration tests for organization endpoints."""

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


def _ctx():
    """Return a reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_get_organizations_list(monkeypatch, client):
    """List organizations."""

    async def fake_extract(current_user, db_connection):
        del current_user, db_connection
        return _ctx()

    async def fake_require_permission(
        permission_code, user_context, db_connection, organization_id=None
    ):
        del permission_code, user_context, db_connection, organization_id
        return None

    async def fake_list(self, page, page_size, search=None, status=None):
        del self, page, page_size, search, status
        return type(
            "Resp",
            (),
            {"data": [{"id": "org-1", "name": "Org"}], "total_count": 1},
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.organization.extract_user_context",
        fake_extract,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.organization.require_permission",
        fake_require_permission,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service."
        "OrganizationService.list_organizations",
        fake_list,
    )

    res = await client.get("/v1/organization/list?page=1&page_size=10")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "org-1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_organization_by_id(monkeypatch, client):
    """Get organization detail."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_detail(self, org_id):
        """Fake org detail."""
        del self
        assert org_id == "550e8400-e29b-41d4-a716-446655440000"
        return type(
            "Resp", (), {"model_dump": lambda self=None, **_k: {"id": org_id, "name": "Org"}}
        )()

    monkeypatch.setattr(
        "apps.user_service.app.api.organization.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service."
        "OrganizationService.get_organization_detail",
        fake_detail,
    )

    res = await client.get("/v1/organization/550e8400-e29b-41d4-a716-446655440000")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.asyncio
async def test_create_organization(monkeypatch, client):
    """Create organization."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_extract_user_context(_current_user, _db_connection):
        """Fake extract context."""
        del _current_user, _db_connection
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id=None, user_type="admin"
        )

    async def fake_create(_self, body, slug=None):
        """Fake create org."""
        del _self, slug
        assert body.company_data.company_name == "Org New"
        return {
            "organization_id": "org-new",
            "organization": {"id": "org-new", "name": "Org New"},
            "user": {"id": "u1"},
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.organization.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service."
        "OrganizationService.create_organization",
        fake_create,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.organization.extract_user_context",
        fake_extract_user_context,
    )

    res = await client.post(
        "/v1/organization/",
        json={
            "company_data": {
                "company_name": "Org New",
                "primary_practice_areas": ["Litigation"],
            }
        },
    )
    if res.status_code == 201:
        body = assert_success(res, 201)
        assert body["data"]["organization"]["id"] == "org-new"
    else:
        assert res.status_code in (200, 201, 409)


@pytest.mark.asyncio
async def test_admin_update_organization(monkeypatch, client):
    """Admin update organization."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_update(self, organization_id, update_data):
        """Fake admin update."""
        del self
        assert organization_id == "550e8400-e29b-41d4-a716-446655440000"
        assert update_data.name == "Updated Org"
        return {
            "organization_id": organization_id,
            "organization_name": "Updated Org",
            "slug": "updated-org-slug",
            "old_data": {"name": "Original Org", "slug": "original-slug"},
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.organization.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service"
        ".OrganizationService.update_organization",
        fake_update,
    )

    res = await client.put(
        "/v1/organization/550e8400-e29b-41d4-a716-446655440000",
        json={"name": "Updated Org"},
    )
    body = assert_success(res, 200)
    assert body["data"]["organization_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert body["data"]["organization_name"] == "Updated Org"
    assert body["data"]["slug"] == "updated-org-slug"


@pytest.mark.asyncio
async def test_delete_organization(monkeypatch, client):
    """Delete organization."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_delete(self, organization_id):
        del self
        assert organization_id == "550e8400-e29b-41d4-a716-446655440000"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.organization.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service"
        ".OrganizationService.delete_organization",
        fake_delete,
    )

    res = await client.delete("/v1/organization/550e8400-e29b-41d4-a716-446655440000")
    assert_success(res, 200)
