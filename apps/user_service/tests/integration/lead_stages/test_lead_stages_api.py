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
