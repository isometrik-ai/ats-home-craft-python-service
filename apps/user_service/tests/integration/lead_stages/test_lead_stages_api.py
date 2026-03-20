"""Integration tests for lead stages endpoints."""

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


def _ctx():
    """Return reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_create_lead_stage(monkeypatch, client):
    """Create a new lead stage."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_create_lead_stage(self, body):
        """Fake service create call."""
        del self
        assert body.stage_name == "Qualified"
        assert body.sort_order == 2
        assert body.is_initial is False
        assert body.is_final is False
        assert body.color.value == "green"
        return {"id": "stage-1", "stage_name": "Qualified"}

    monkeypatch.setattr(
        "apps.user_service.app.api.lead_stages.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.LeadStageService.create_lead_stage",
        fake_create_lead_stage,
    )

    response = await client.post(
        "/v1/lead-stages",
        json={
            "stage_name": "Qualified",
            "description": "Warm leads that passed intro",
            "color": "green",
            "sort_order": 2,
            "is_initial": False,
            "is_final": False,
        },
    )
    assert_success(response, 201)


@pytest.mark.asyncio
async def test_list_lead_stages(monkeypatch, client):
    """List lead stages."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_list_lead_stages(self):
        """Fake service list call."""
        del self
        return (
            [
                {
                    "id": "stage-1",
                    "stage_name": "New",
                    "stage_key": "new",
                    "description": None,
                    "color": "blue",
                    "sort_order": 1,
                    "is_initial": True,
                    "is_final": False,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            1,
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.lead_stages.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.LeadStageService.list_lead_stages",
        fake_list_lead_stages,
    )

    response = await client.get("/v1/lead-stages")
    body = assert_success(response, 200)
    assert body["data"][0]["id"] == "stage-1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_lead_stages_empty(monkeypatch, client):
    """List lead stages returns empty collection."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_list_lead_stages(self):
        """Fake service list call for empty case."""
        del self
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.api.lead_stages.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.LeadStageService.list_lead_stages",
        fake_list_lead_stages,
    )

    response = await client.get("/v1/lead-stages")
    body = assert_success(response, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_lead_stage(monkeypatch, client):
    """Get lead stage details by ID."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_get_lead_stage(self, stage_id):
        """Fake service get call."""
        del self
        assert stage_id == "550e8400-e29b-41d4-a716-446655440000"
        return {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "stage_name": "Qualified",
            "stage_key": "qualified",
            "description": "Warm lead",
            "color": "green",
            "sort_order": 2,
            "is_initial": False,
            "is_final": False,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.lead_stages.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.LeadStageService.get_lead_stage",
        fake_get_lead_stage,
    )

    response = await client.get("/v1/lead-stages/550e8400-e29b-41d4-a716-446655440000")
    body = assert_success(response, 200)
    assert body["data"]["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert body["data"]["stage_key"] == "qualified"
