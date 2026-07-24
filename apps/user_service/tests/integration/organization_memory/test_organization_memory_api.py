"""Integration tests for organization memory query endpoint."""

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


def _user_context() -> UserContext:
    """Build user context for org memory tests."""
    return UserContext(
        user_id="test-user-id",
        email="test@example.com",
        organization_id="org-123",
        user_type="admin",
    )


def _patch_org_memory_access(monkeypatch) -> None:
    """Bypass auth gates and enable Graphiti for tests."""

    async def fake_extract_user_context(current_user, db_connection):
        del current_user, db_connection
        return _user_context()

    async def fake_require_access(*, db_connection, user_context):
        del db_connection, user_context

    async def fake_memory_enabled(_db, _org_id):
        return True

    monkeypatch.setattr(
        "apps.user_service.app.api.organization_memory.extract_user_context",
        fake_extract_user_context,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.organization_memory.require_org_memory_query_access",
        fake_require_access,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.organization_memory.is_graphiti_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.organization_memory.is_organization_memory_enabled",
        fake_memory_enabled,
    )


@pytest.mark.asyncio
async def test_org_memory_query(monkeypatch, client):
    """POST /organization/memory/query returns an answer."""

    _patch_org_memory_access(monkeypatch)

    async def fake_run(
        _self,
        *,
        user_message: str,
        organization_id: str,
        entity_id=None,
        entity_type=None,
        db_connection=None,
    ):
        del _self, entity_id, entity_type, db_connection
        assert user_message == "Who are our top leads?"
        assert organization_id == "org-123"
        return "You have 3 active leads."

    monkeypatch.setattr(
        "apps.user_service.app.services.org_memory_query_service.OrgMemoryQueryService.run",
        fake_run,
    )

    res = await client.post(
        "/v1/organization/memory/query",
        json={"query": "Who are our top leads?"},
    )
    body = assert_success(res, 200)
    assert body["data"]["answer"] == "You have 3 active leads."


@pytest.mark.asyncio
async def test_org_memory_query_scoped(monkeypatch, client):
    """POST /organization/memory/query accepts entity scope."""

    _patch_org_memory_access(monkeypatch)

    async def fake_run(
        _self,
        *,
        user_message: str,
        organization_id: str,
        entity_id=None,
        entity_type=None,
        db_connection=None,
    ):
        del _self, user_message, organization_id, db_connection
        assert entity_id == "lead-1"
        assert entity_type == "lead"
        return "Lead summary for lead-1."

    monkeypatch.setattr(
        "apps.user_service.app.services.org_memory_query_service.OrgMemoryQueryService.run",
        fake_run,
    )

    res = await client.post(
        "/v1/organization/memory/query",
        json={
            "query": "Summarize this lead",
            "entity_id": "lead-1",
            "entity_type": "lead",
        },
    )
    body = assert_success(res, 200)
    assert "Lead summary" in body["data"]["answer"]
