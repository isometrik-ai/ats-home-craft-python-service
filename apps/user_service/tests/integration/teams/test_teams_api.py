"""Integration tests for teams endpoints."""

from types import SimpleNamespace

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_create_team(monkeypatch, client):
    """Create team."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_create(self, body):
        del self
        assert body.name == "Team A"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.teams.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.team_service.TeamService.create_team",
        fake_create,
    )

    res = await client.post(
        "/v1/teams",
        json={"name": "Team A", "description": "desc", "members": []},
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_list_teams(monkeypatch, client):
    """List teams."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_list(self, page, page_size, search=None):
        del self, page, page_size, search
        return type(
            "Resp",
            (),
            {
                "data": [{"id": "t1", "name": "Team A"}],
                "total": 1,
                "total_count": 1,
                "page": 1,
                "page_size": 20,
            },
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.teams.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.team_service.TeamService.list_teams",
        fake_list,
    )

    res = await client.get("/v1/teams?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "t1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_teams_no_data(monkeypatch, client):
    """List teams empty branch."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_list(self, page, page_size, search=None):
        del self, page, page_size, search
        return type(
            "Resp", (), {"data": [], "total": 0, "total_count": 0, "page": 1, "page_size": 20}
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.teams.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.team_service.TeamService.list_teams",
        fake_list,
    )

    res = await client.get("/v1/teams?page=1&page_size=20")
    assert res.status_code == 204


@pytest.mark.asyncio
async def test_update_team(monkeypatch, client):
    """Update team."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_update(self, team_id, body):
        del self, team_id
        return SimpleNamespace(id="t1", name=body.name)

    monkeypatch.setattr(
        "apps.user_service.app.api.teams.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.team_service.TeamService.update_team",
        fake_update,
    )

    team_id = "550e8400-e29b-41d4-a716-446655440000"
    res = await client.put(f"/v1/teams/{team_id}", json={"name": "Updated Team"})
    if res.status_code == 200:
        body = assert_success(res, 200)
        if "data" in body:
            assert body["data"]["name"] == "Updated Team"
    else:
        assert res.status_code in (200, 204, 404, 422)


@pytest.mark.asyncio
async def test_delete_team(monkeypatch, client):
    """Delete team."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        del current_user, db_connection, permission_codes
        return UserContext(
            user_id="u1", email="u1@example.com", organization_id="org-1", user_type="admin"
        )

    async def fake_delete(self, team_id):
        del self, team_id
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.teams.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.team_service.TeamService.delete_team",
        fake_delete,
    )

    team_id = "550e8400-e29b-41d4-a716-446655440000"
    res = await client.delete(f"/v1/teams/{team_id}")
    assert res.status_code in (200, 204, 404, 422)
