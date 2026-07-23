"""Integration tests for /v1/projects endpoints.

Covers project CRUD, setup wizard, media, towers, inventory, facilities,
units, site map, and vehicle review routes with mocked service methods.
"""

from __future__ import annotations

import pytest

from apps.user_service.tests.integration.helpers import (
    admin_context,
    patch_check_permissions,
)
from apps.user_service.tests.utils.assertions import assert_success

PROJECT_ID = "880e8400-e29b-41d4-a716-446655440003"
ORG = "org-123"
TOWER_ID = "990e8400-e29b-41d4-a716-446655440001"
WING_ID = "aa0e8400-e29b-41d4-a716-446655440001"
GATE_ID = "bb0e8400-e29b-41d4-a716-446655440001"
LIFT_ID = "cc0e8400-e29b-41d4-a716-446655440001"
FLOOR_ID = "dd0e8400-e29b-41d4-a716-446655440001"
CONFIG_ID = "ee0e8400-e29b-41d4-a716-446655440001"
MEDIA_ID = "ff0e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "110e8400-e29b-41d4-a716-446655440001"
FACILITY_ID = "220e8400-e29b-41d4-a716-446655440001"
ZONE_ID = "330e8400-e29b-41d4-a716-446655440001"
OVERLAY_ID = "440e8400-e29b-41d4-a716-446655440001"
PLOT_ITEM_ID = "550e8400-e29b-41d4-a716-446655440001"
VEHICLE_ID = "660e8400-e29b-41d4-a716-446655440001"
SLOT_ID = "770e8400-e29b-41d4-a716-446655440001"
ADMIN_USER_ID = "550e8400-e29b-41d4-a716-446655440000"

_API = "apps.user_service.app.api.projects"

_FAKE_PROJECT_SUMMARY = {
    "id": PROJECT_ID,
    "organization_id": ORG,
    "code": "sunrise-heights",
    "name": "Sunrise Heights",
    "developer_name": "Acme Developers",
    "city": "Mumbai",
    "state": "Maharashtra",
    "status": "draft",
    "property_types": ["residential"],
    "primary_measurement_unit": "sq_ft",
    "units_count": 0,
    "setup_current_step": "project_basics",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_PROJECT_DETAILS = {
    **_FAKE_PROJECT_SUMMARY,
    "community_admin_user_id": ADMIN_USER_ID,
    "gstin": "27AABCU9603R1ZM",
    "address_line_1": "123 Main St",
    "pin_code": "400001",
    "country": "India",
}

_FAKE_MY_PROJECT = {**_FAKE_PROJECT_SUMMARY, "role": "community_admin"}

_FAKE_PROJECT_STATUS = {
    "project_id": PROJECT_ID,
    "status": "draft",
    "setup_current_step": "towers",
    "is_completed": False,
    "steps": [
        {"step_key": "project_basics", "status": "completed"},
        {"step_key": "towers", "status": "in_progress"},
    ],
}

_FAKE_MEDIA = {
    "id": MEDIA_ID,
    "project_id": PROJECT_ID,
    "kind": "cover_image",
    "path": "/media/cover.jpg",
    "mime": "image/jpeg",
    "size_bytes": 1024,
    "original_name": "cover.jpg",
    "sort_order": 0,
    "created_at": "2026-01-01T00:00:00Z",
}

_FAKE_TOWER = {
    "id": TOWER_ID,
    "project_id": PROJECT_ID,
    "name": "Tower A",
    "code": "TA",
    "tower_type": "residential",
    "basement_count": 0,
    "upper_floor_count": 10,
    "active": True,
}

_FAKE_WING = {"id": WING_ID, "tower_id": TOWER_ID, "name": "Wing A", "code": "WA"}

_FAKE_GATE = {"id": GATE_ID, "tower_id": TOWER_ID, "name": "Main Gate", "gate_type": "both"}

_FAKE_LIFT = {"id": LIFT_ID, "tower_id": TOWER_ID, "name": "Lift 1", "lift_type": "passenger"}

_FAKE_FLOOR = {
    "id": FLOOR_ID,
    "tower_id": TOWER_ID,
    "level_number": 1,
    "display_name": "Floor 1",
    "sort_order": 1,
    "is_parking": False,
}

_FAKE_CONFIG = {
    "id": CONFIG_ID,
    "project_id": PROJECT_ID,
    "config_kind": "apartment",
    "name": "2 BHK",
    "code": "2BHK",
    "active": True,
}

_FAKE_PLOT_ITEM = {
    "id": PLOT_ITEM_ID,
    "config_id": CONFIG_ID,
    "plot_no": "P-101",
    "size_sqft": 1200.0,
    "status": "empty",
    "sort_order": 0,
}

_FAKE_FACILITY = {
    "id": FACILITY_ID,
    "project_id": PROJECT_ID,
    "name": "Clubhouse",
    "facility_type": "clubhouse",
    "location_type": "indoor_clubhouse",
    "status": "active",
    "active": True,
}

_FAKE_UNIT = {
    "id": UNIT_ID,
    "project_id": PROJECT_ID,
    "code": "A-101",
    "status": "vacant",
    "sort_order": 0,
    "is_parking": False,
}

_FAKE_UNIT_DETAIL = {
    "id": UNIT_ID,
    "project_id": PROJECT_ID,
    "code": "A-101",
    "status": "vacant",
    "occupancy_label": "Vacant",
    "is_sold": False,
    "is_parking": False,
    "sort_order": 0,
    "vehicles_count": 0,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_PARKING_ZONE = {
    "id": ZONE_ID,
    "project_id": PROJECT_ID,
    "tower_id": TOWER_ID,
    "floor_id": FLOOR_ID,
    "name": "Basement P1",
}

_FAKE_OVERLAY = {
    "id": OVERLAY_ID,
    "project_id": PROJECT_ID,
    "entity_type": "tower",
    "entity_id": TOWER_ID,
    "latitude": 19.076,
    "longitude": 72.8777,
}

_FAKE_INVENTORY_SUMMARY = {
    "project_id": PROJECT_ID,
    "header": {
        "buildings": 1,
        "apartments": 10,
        "commercial": 0,
        "plots": 0,
        "sold_count": 2,
        "unsold_count": 8,
        "sold_percent": 20,
    },
    "buildings": [
        {
            "id": TOWER_ID,
            "name": "Tower A",
            "code": "TA",
            "tower_type": "residential",
            "upper_floor_count": 10,
            "basement_count": 0,
            "unit_count": 10,
            "sold_count": 2,
            "unsold_count": 8,
            "active": True,
        }
    ],
    "units": [
        {
            "id": UNIT_ID,
            "code": "A-101",
            "tower_id": TOWER_ID,
            "floor_id": FLOOR_ID,
            "config_id": CONFIG_ID,
            "config_kind": "apartment",
            "status": "vacant",
            "sort_order": 0,
            "is_parking": False,
        }
    ],
    "floors": {
        TOWER_ID: [
            {
                "id": FLOOR_ID,
                "level_number": 1,
                "display_name": "Floor 1",
                "sort_order": 1,
                "is_parking": False,
            }
        ]
    },
    "plot_configs": [],
}

_FAKE_VEHICLE = {
    "id": VEHICLE_ID,
    "project_id": PROJECT_ID,
    "registration_number": "MH01AB1234",
    "status": "pending",
}

_CREATE_PROJECT_BODY = {
    "name": "Sunrise Heights",
    "developer_name": "Acme Developers",
    "community_admin_user_id": ADMIN_USER_ID,
    "gstin": "27AABCU9603R1ZM",
    "address_line_1": "123 Main St",
    "pin_code": "400001",
    "city": "Mumbai",
    "state": "Maharashtra",
    "country": "India",
    "property_types": ["residential"],
    "primary_measurement_unit": "sq_ft",
}


def _patch_projects_access(monkeypatch) -> None:
    """Bypass RBAC for projects routes."""
    patch_check_permissions(monkeypatch, _API, org_id=ORG)


def _patch_extract_user_context(monkeypatch) -> None:
    """Bypass extract_user_context for /projects/mine."""

    async def fake_extract_user_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id=ORG)

    monkeypatch.setattr(f"{_API}.extract_user_context", fake_extract_user_context)


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project(monkeypatch, client):
    """POST /projects creates a project."""

    _patch_projects_access(monkeypatch)

    async def fake_create_project(_self, body):
        del _self
        assert body.name == "Sunrise Heights"
        return {
            "project_id": PROJECT_ID,
            "old_data": None,
            "new_data": _FAKE_PROJECT_DETAILS,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.create_project",
        fake_create_project,
    )

    res = await client.post("/v1/projects", json=_CREATE_PROJECT_BODY)
    body = assert_success(res, 201)
    assert body["data"]["id"] == PROJECT_ID


@pytest.mark.asyncio
async def test_list_projects(monkeypatch, client):
    """GET /projects returns paginated projects."""

    _patch_projects_access(monkeypatch)

    async def fake_list_projects(
        _self,
        *,
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    ):
        del _self, search, status, property_type
        return {"items": [_FAKE_PROJECT_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.list_projects",
        fake_list_projects,
    )

    res = await client.get("/v1/projects?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == PROJECT_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_projects_empty(monkeypatch, client):
    """GET /projects returns empty collection."""

    _patch_projects_access(monkeypatch)

    async def fake_list_projects(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.list_projects",
        fake_list_projects,
    )

    res = await client.get("/v1/projects")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_my_projects(monkeypatch, client):
    """GET /projects/mine returns assigned projects."""

    _patch_extract_user_context(monkeypatch)

    async def fake_list_my_projects(_self, **kwargs):
        del _self, kwargs
        return {"items": [_FAKE_MY_PROJECT], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.list_my_projects",
        fake_list_my_projects,
    )

    res = await client.get("/v1/projects/mine")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == PROJECT_ID
    assert body["data"][0]["role"] == "community_admin"


@pytest.mark.asyncio
async def test_list_my_projects_empty(monkeypatch, client):
    """GET /projects/mine returns no-content pagination when empty."""

    _patch_extract_user_context(monkeypatch)

    async def fake_list_my_projects(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.list_my_projects",
        fake_list_my_projects,
    )

    res = await client.get("/v1/projects/mine")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_project_status(monkeypatch, client):
    """GET /projects/{id}/status returns wizard steps."""

    _patch_projects_access(monkeypatch)

    async def fake_get_status(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return _FAKE_PROJECT_STATUS

    monkeypatch.setattr(
        "apps.user_service.app.services.project_setup_service.ProjectSetupService.get_status",
        fake_get_status,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/status")
    body = assert_success(res, 200)
    assert body["data"]["project_id"] == PROJECT_ID
    assert len(body["data"]["steps"]) == 2


@pytest.mark.asyncio
async def test_get_project_details(monkeypatch, client):
    """GET /projects/{id} returns project details."""

    _patch_projects_access(monkeypatch)

    async def fake_get_details(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return _FAKE_PROJECT_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.get_project_details",
        fake_get_details,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}")
    body = assert_success(res, 200)
    assert body["data"]["name"] == "Sunrise Heights"


@pytest.mark.asyncio
async def test_update_project(monkeypatch, client):
    """PATCH /projects/{id} updates a project."""

    _patch_projects_access(monkeypatch)

    async def fake_update(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.name == "Sunrise Heights Updated"
        return {
            "old_data": _FAKE_PROJECT_DETAILS,
            "new_data": {**_FAKE_PROJECT_DETAILS, "name": "Sunrise Heights Updated"},
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.update_project",
        fake_update,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}",
        json={"name": "Sunrise Heights Updated"},
    )
    body = assert_success(res, 200)
    assert body["data"]["name"] == "Sunrise Heights Updated"


@pytest.mark.asyncio
async def test_delete_project(monkeypatch, client):
    """DELETE /projects/{id} deletes a project."""

    _patch_projects_access(monkeypatch)

    async def fake_delete(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return {"old_data": _FAKE_PROJECT_DETAILS, "new_data": None}

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.delete_project",
        fake_delete,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_setup_step(monkeypatch, client):
    """POST /projects/{id}/steps/{key}/complete marks step done."""

    _patch_projects_access(monkeypatch)

    async def fake_complete_step(_self, *, project_id: str, step_key: str, data=None):
        del _self, data
        assert project_id == PROJECT_ID
        assert step_key == "towers"
        return {"step_key": step_key, "status": "completed"}

    monkeypatch.setattr(
        "apps.user_service.app.services.project_setup_service.ProjectSetupService.complete_step",
        fake_complete_step,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/steps/towers/complete",
        json={"data": {"tower_count": 1}},
    )
    body = assert_success(res, 200)
    assert body["data"]["step_key"] == "towers"


@pytest.mark.asyncio
async def test_complete_project_setup(monkeypatch, client):
    """POST /projects/{id}/complete finalizes setup."""

    _patch_projects_access(monkeypatch)

    async def fake_complete_wizard(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return {"project_id": PROJECT_ID, "status": "active"}

    monkeypatch.setattr(
        "apps.user_service.app.services.project_setup_service.ProjectSetupService.complete_wizard",
        fake_complete_wizard,
    )

    res = await client.post(f"/v1/projects/{PROJECT_ID}/complete")
    body = assert_success(res, 200)
    assert body["data"]["status"] == "active"


# ---------------------------------------------------------------------------
# Project media
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_project_media(monkeypatch, client):
    """POST /projects/{id}/media attaches media."""

    _patch_projects_access(monkeypatch)

    async def fake_add_media(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.kind.value == "cover_image"
        return _FAKE_MEDIA

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.add_media",
        fake_add_media,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/media",
        json={
            "kind": "cover_image",
            "path": "/media/cover.jpg",
            "mime": "image/jpeg",
            "size_bytes": 1024,
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == MEDIA_ID


@pytest.mark.asyncio
async def test_list_project_media(monkeypatch, client):
    """GET /projects/{id}/media lists media rows."""

    _patch_projects_access(monkeypatch)

    async def fake_list_media(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return [_FAKE_MEDIA]

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.list_media",
        fake_list_media,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/media")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == MEDIA_ID


@pytest.mark.asyncio
async def test_delete_project_media(monkeypatch, client):
    """DELETE /projects/{id}/media/{media_id} removes media."""

    _patch_projects_access(monkeypatch)

    async def fake_remove_media(_self, *, project_id: str, media_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert media_id == MEDIA_ID
        return {"old_data": _FAKE_MEDIA}

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.ProjectsService.remove_media",
        fake_remove_media,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/media/{MEDIA_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Towers, wings, gates, lifts, floors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tower(monkeypatch, client):
    """POST /projects/{id}/towers creates a tower."""

    _patch_projects_access(monkeypatch)

    async def fake_create_tower(_self, *, project_id: str, body):
        del _self, body
        assert project_id == PROJECT_ID
        return _FAKE_TOWER

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.create_tower",
        fake_create_tower,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/towers",
        json={"name": "Tower A", "code": "TA", "tower_type": "residential"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == TOWER_ID


@pytest.mark.asyncio
async def test_list_towers(monkeypatch, client):
    """GET /projects/{id}/towers lists towers."""

    _patch_projects_access(monkeypatch)

    async def fake_list_towers(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return [_FAKE_TOWER]

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.list_towers",
        fake_list_towers,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/towers")
    body = assert_success(res, 200)
    assert body["data"][0]["code"] == "TA"


@pytest.mark.asyncio
async def test_update_tower(monkeypatch, client):
    """PATCH /projects/{id}/towers/{tower_id} updates tower."""

    _patch_projects_access(monkeypatch)

    async def fake_update_tower(_self, *, project_id: str, tower_id: str, body):
        del _self, body
        assert project_id == PROJECT_ID
        assert tower_id == TOWER_ID
        return {**_FAKE_TOWER, "name": "Tower Alpha"}

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.update_tower",
        fake_update_tower,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}",
        json={"name": "Tower Alpha"},
    )
    body = assert_success(res, 200)
    assert body["data"]["name"] == "Tower Alpha"


@pytest.mark.asyncio
async def test_delete_tower(monkeypatch, client):
    """DELETE /projects/{id}/towers/{tower_id} deletes tower."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_tower(_self, *, project_id: str, tower_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert tower_id == TOWER_ID
        return {"old_data": _FAKE_TOWER}

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.delete_tower",
        fake_delete_tower,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_tower_wing(monkeypatch, client):
    """POST tower wings creates a wing."""

    _patch_projects_access(monkeypatch)

    async def fake_create_wing(_self, *, project_id: str, tower_id: str, body):
        del _self, body
        assert project_id == PROJECT_ID
        assert tower_id == TOWER_ID
        return _FAKE_WING

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.create_wing",
        fake_create_wing,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/wings",
        json={"name": "Wing A"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == WING_ID


@pytest.mark.asyncio
async def test_list_tower_wings(monkeypatch, client):
    """GET tower wings lists wings."""

    _patch_projects_access(monkeypatch)

    async def fake_list_wings(_self, *, project_id: str, tower_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert tower_id == TOWER_ID
        return [_FAKE_WING]

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.list_wings",
        fake_list_wings,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/wings")
    body = assert_success(res, 200)
    assert body["data"][0]["name"] == "Wing A"


@pytest.mark.asyncio
async def test_delete_tower_wing(monkeypatch, client):
    """DELETE tower wing removes wing."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_wing(_self, *, project_id: str, tower_id: str, wing_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert tower_id == TOWER_ID
        assert wing_id == WING_ID
        return {"old_data": _FAKE_WING}

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.delete_wing",
        fake_delete_wing,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/wings/{WING_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_tower_gate(monkeypatch, client):
    """POST tower gates creates a gate."""

    _patch_projects_access(monkeypatch)

    async def fake_create_gate(_self, *, project_id: str, tower_id: str, body):
        del _self, body
        assert project_id == PROJECT_ID
        return _FAKE_GATE

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.create_gate",
        fake_create_gate,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/gates",
        json={"name": "Main Gate"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == GATE_ID


@pytest.mark.asyncio
async def test_list_tower_gates(monkeypatch, client):
    """GET tower gates lists gates."""

    _patch_projects_access(monkeypatch)

    async def fake_list_gates(_self, *, project_id: str, tower_id: str):
        del _self
        return [_FAKE_GATE]

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.list_gates",
        fake_list_gates,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/gates")
    body = assert_success(res, 200)
    assert body["data"][0]["name"] == "Main Gate"


@pytest.mark.asyncio
async def test_delete_tower_gate(monkeypatch, client):
    """DELETE tower gate removes gate."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_gate(_self, *, project_id: str, tower_id: str, gate_id: str):
        del _self
        assert gate_id == GATE_ID
        return {"old_data": _FAKE_GATE}

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.delete_gate",
        fake_delete_gate,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/gates/{GATE_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_tower_lift(monkeypatch, client):
    """POST tower lifts creates a lift."""

    _patch_projects_access(monkeypatch)

    async def fake_create_lift(_self, *, project_id: str, tower_id: str, body):
        del _self, body
        return _FAKE_LIFT

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.create_lift",
        fake_create_lift,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/lifts",
        json={"name": "Lift 1"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == LIFT_ID


@pytest.mark.asyncio
async def test_list_tower_lifts(monkeypatch, client):
    """GET tower lifts lists lifts."""

    _patch_projects_access(monkeypatch)

    async def fake_list_lifts(_self, *, project_id: str, tower_id: str):
        del _self
        return [_FAKE_LIFT]

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.list_lifts",
        fake_list_lifts,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/lifts")
    body = assert_success(res, 200)
    assert body["data"][0]["name"] == "Lift 1"


@pytest.mark.asyncio
async def test_delete_tower_lift(monkeypatch, client):
    """DELETE tower lift removes lift."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_lift(_self, *, project_id: str, tower_id: str, lift_id: str):
        del _self
        assert lift_id == LIFT_ID
        return {"old_data": _FAKE_LIFT}

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.delete_lift",
        fake_delete_lift,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/lifts/{LIFT_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_floor(monkeypatch, client):
    """POST tower floors creates a floor."""

    _patch_projects_access(monkeypatch)

    async def fake_create_floor(_self, *, project_id: str, tower_id: str, body):
        del _self, body
        return _FAKE_FLOOR

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.create_floor",
        fake_create_floor,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/floors",
        json={"level_number": 1, "display_name": "Floor 1"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == FLOOR_ID


@pytest.mark.asyncio
async def test_list_floors(monkeypatch, client):
    """GET tower floors lists floors."""

    _patch_projects_access(monkeypatch)

    async def fake_list_floors(_self, *, project_id: str, tower_id: str):
        del _self
        return [_FAKE_FLOOR]

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.list_floors",
        fake_list_floors,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/floors")
    body = assert_success(res, 200)
    assert body["data"][0]["display_name"] == "Floor 1"


@pytest.mark.asyncio
async def test_delete_floor(monkeypatch, client):
    """DELETE tower floor removes floor."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_floor(_self, *, project_id: str, tower_id: str, floor_id: str):
        del _self
        assert floor_id == FLOOR_ID
        return {"old_data": _FAKE_FLOOR}

    monkeypatch.setattr(
        "apps.user_service.app.services.towers_service.TowersService.delete_floor",
        fake_delete_floor,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/towers/{TOWER_ID}/floors/{FLOOR_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Unit configs, plot items, config media
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_unit_config(monkeypatch, client):
    """POST /projects/{id}/configs creates config."""

    _patch_projects_access(monkeypatch)

    async def fake_create_config(_self, *, project_id: str, body):
        del _self, body
        assert project_id == PROJECT_ID
        return _FAKE_CONFIG

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.create_config",
        fake_create_config,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/configs",
        json={
            "config_kind": "apartment",
            "name": "2 BHK",
            "code": "2BHK",
            "bedrooms": 2,
            "bathrooms": 2,
            "area_sqft": 950,
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == CONFIG_ID


@pytest.mark.asyncio
async def test_list_unit_configs(monkeypatch, client):
    """GET /projects/{id}/configs lists configs."""

    _patch_projects_access(monkeypatch)

    async def fake_list_configs(_self, *, project_id: str, config_kind=None):
        del _self, config_kind
        return [_FAKE_CONFIG]

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.list_configs",
        fake_list_configs,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/configs")
    body = assert_success(res, 200)
    assert body["data"][0]["code"] == "2BHK"


@pytest.mark.asyncio
async def test_update_unit_config(monkeypatch, client):
    """PATCH config updates unit configuration."""

    _patch_projects_access(monkeypatch)

    async def fake_update_config(_self, *, project_id: str, config_id: str, body):
        del _self, body
        assert config_id == CONFIG_ID
        return {**_FAKE_CONFIG, "name": "2 BHK Premium"}

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.update_config",
        fake_update_config,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}",
        json={"name": "2 BHK Premium"},
    )
    body = assert_success(res, 200)
    assert body["data"]["name"] == "2 BHK Premium"


@pytest.mark.asyncio
async def test_delete_unit_config(monkeypatch, client):
    """DELETE config removes unit configuration."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_config(_self, *, project_id: str, config_id: str):
        del _self
        assert config_id == CONFIG_ID
        return {"old_data": _FAKE_CONFIG}

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.delete_config",
        fake_delete_config,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_plot_item(monkeypatch, client):
    """POST plot-items creates plot config item."""

    _patch_projects_access(monkeypatch)

    async def fake_create_plot_item(_self, *, project_id: str, config_id: str, body):
        del _self, body
        assert config_id == CONFIG_ID
        return _FAKE_PLOT_ITEM

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.create_plot_item",
        fake_create_plot_item,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}/plot-items",
        json={"plot_no": "P-101", "size_sqft": 1200},
    )
    body = assert_success(res, 201)
    assert body["data"]["plot_no"] == "P-101"


@pytest.mark.asyncio
async def test_list_plot_items(monkeypatch, client):
    """GET plot-items lists plot config items."""

    _patch_projects_access(monkeypatch)

    async def fake_list_plot_items(_self, *, project_id: str, config_id: str):
        del _self
        assert config_id == CONFIG_ID
        return [_FAKE_PLOT_ITEM]

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.list_plot_items",
        fake_list_plot_items,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}/plot-items")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == PLOT_ITEM_ID


@pytest.mark.asyncio
async def test_delete_plot_item(monkeypatch, client):
    """DELETE plot-item removes plot config item."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_plot_item(_self, *, project_id: str, config_id: str, item_id: str):
        del _self
        assert item_id == PLOT_ITEM_ID
        return {"old_data": _FAKE_PLOT_ITEM}

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.delete_plot_item",
        fake_delete_plot_item,
    )

    res = await client.delete(
        f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}/plot-items/{PLOT_ITEM_ID}"
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_add_config_media(monkeypatch, client):
    """POST config media attaches media to config."""

    _patch_projects_access(monkeypatch)

    fake_config_media = {
        "id": MEDIA_ID,
        "config_id": CONFIG_ID,
        "kind": "floor_plan",
        "path": "/media/floor-plan.pdf",
        "mime": "application/pdf",
        "size_bytes": 2048,
    }

    async def fake_add_media(_self, *, project_id: str, config_id: str, body):
        del _self, body
        assert config_id == CONFIG_ID
        return fake_config_media

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.add_media",
        fake_add_media,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}/media",
        json={
            "kind": "floor_plan",
            "path": "/media/floor-plan.pdf",
            "mime": "application/pdf",
            "size_bytes": 2048,
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["kind"] == "floor_plan"


@pytest.mark.asyncio
async def test_list_config_media(monkeypatch, client):
    """GET config media lists config media rows."""

    _patch_projects_access(monkeypatch)

    async def fake_list_media(_self, *, project_id: str, config_id: str):
        del _self
        return [
            {
                "id": MEDIA_ID,
                "config_id": CONFIG_ID,
                "kind": "floor_plan",
                "path": "/x.pdf",
                "mime": "application/pdf",
                "size_bytes": 1,
            }
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.list_media",
        fake_list_media,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}/media")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == MEDIA_ID


@pytest.mark.asyncio
async def test_delete_config_media(monkeypatch, client):
    """DELETE config media removes config media row."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_media(_self, *, project_id: str, config_id: str, media_id: str):
        del _self
        assert media_id == MEDIA_ID
        return {
            "old_data": {
                "id": MEDIA_ID,
                "config_id": CONFIG_ID,
                "kind": "floor_plan",
                "path": "/media/floor-plan.pdf",
            }
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.unit_configs_service.UnitConfigsService.delete_media",
        fake_delete_media,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/configs/{CONFIG_ID}/media/{MEDIA_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Floor inventory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_floor_inventory(monkeypatch, client):
    """PUT /projects/{id}/inventory upserts matrix."""

    _patch_projects_access(monkeypatch)
    inventory_row = {
        "tower_id": TOWER_ID,
        "floor_id": FLOOR_ID,
        "config_id": CONFIG_ID,
        "quantity": 4,
    }

    async def fake_upsert(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert len(body.items) == 1
        return [inventory_row]

    monkeypatch.setattr(
        "apps.user_service.app.services.inventory_service.InventoryService.upsert_inventory",
        fake_upsert,
    )

    res = await client.put(
        f"/v1/projects/{PROJECT_ID}/inventory",
        json={
            "items": [
                {
                    "tower_id": TOWER_ID,
                    "floor_id": FLOOR_ID,
                    "config_id": CONFIG_ID,
                    "quantity": 4,
                }
            ]
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["items"][0]["quantity"] == 4


@pytest.mark.asyncio
async def test_get_inventory_summary(monkeypatch, client):
    """GET inventory summary returns aggregated data."""

    _patch_projects_access(monkeypatch)

    async def fake_summary(
        _self, *, project_id: str, tower_id=None, status=None, include_plot_items=True
    ):
        del _self, tower_id, status, include_plot_items
        assert project_id == PROJECT_ID
        return _FAKE_INVENTORY_SUMMARY

    monkeypatch.setattr(
        "apps.user_service.app.services.inventory_service.InventoryService.get_inventory_summary",
        fake_summary,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/inventory/summary")
    body = assert_success(res, 200)
    assert body["data"]["header"]["buildings"] == 1


@pytest.mark.asyncio
async def test_list_floor_inventory(monkeypatch, client):
    """GET /projects/{id}/inventory lists floor matrix."""

    _patch_projects_access(monkeypatch)

    async def fake_list_inventory(_self, *, project_id: str):
        del _self
        return [
            {
                "tower_id": TOWER_ID,
                "floor_id": FLOOR_ID,
                "config_id": CONFIG_ID,
                "quantity": 4,
            }
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.inventory_service.InventoryService.list_inventory",
        fake_list_inventory,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/inventory")
    body = assert_success(res, 200)
    assert body["data"][0]["quantity"] == 4


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_facility(monkeypatch, client):
    """POST /projects/{id}/facilities creates facility."""

    _patch_projects_access(monkeypatch)

    async def fake_create_facility(_self, *, project_id: str, body):
        del _self, body
        return _FAKE_FACILITY

    monkeypatch.setattr(
        "apps.user_service.app.services.facilities_service.FacilitiesService.create_facility",
        fake_create_facility,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/facilities",
        json={
            "name": "Clubhouse",
            "facility_type": "clubhouse",
            "location_type": "indoor_clubhouse",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == FACILITY_ID


@pytest.mark.asyncio
async def test_list_facilities(monkeypatch, client):
    """GET /projects/{id}/facilities lists facilities."""

    _patch_projects_access(monkeypatch)

    async def fake_list_facilities(_self, *, project_id: str):
        del _self
        return [_FAKE_FACILITY]

    monkeypatch.setattr(
        "apps.user_service.app.services.facilities_service.FacilitiesService.list_facilities",
        fake_list_facilities,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/facilities")
    body = assert_success(res, 200)
    assert body["data"][0]["name"] == "Clubhouse"


@pytest.mark.asyncio
async def test_list_facility_parking_slots(monkeypatch, client):
    """GET facility parking-slots lists slots."""

    _patch_projects_access(monkeypatch)

    async def fake_list_slots(_self, *, project_id: str, facility_id: str, status=None):
        del _self, status
        assert facility_id == FACILITY_ID
        return [{"id": SLOT_ID, "slot_number": "P-01", "status": "available"}]

    monkeypatch.setattr(
        "apps.user_service.app.services.facilities_service.FacilitiesService.list_parking_slots",
        fake_list_slots,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/facilities/{FACILITY_ID}/parking-slots")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == SLOT_ID


@pytest.mark.asyncio
async def test_update_facility(monkeypatch, client):
    """PATCH facility updates facility row."""

    _patch_projects_access(monkeypatch)

    async def fake_update_facility(_self, *, project_id: str, facility_id: str, body):
        del _self, body
        assert facility_id == FACILITY_ID
        return {**_FAKE_FACILITY, "name": "Grand Clubhouse"}

    monkeypatch.setattr(
        "apps.user_service.app.services.facilities_service.FacilitiesService.update_facility",
        fake_update_facility,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/facilities/{FACILITY_ID}",
        json={"name": "Grand Clubhouse"},
    )
    body = assert_success(res, 200)
    assert body["data"]["name"] == "Grand Clubhouse"


@pytest.mark.asyncio
async def test_delete_facility(monkeypatch, client):
    """DELETE facility removes facility row."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_facility(_self, *, project_id: str, facility_id: str):
        del _self
        assert facility_id == FACILITY_ID
        return {"old_data": _FAKE_FACILITY}

    monkeypatch.setattr(
        "apps.user_service.app.services.facilities_service.FacilitiesService.delete_facility",
        fake_delete_facility,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/facilities/{FACILITY_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Units and parking zones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_unit(monkeypatch, client):
    """POST /projects/{id}/units creates a unit."""

    _patch_projects_access(monkeypatch)

    async def fake_create_unit(_self, *, project_id: str, body):
        del _self, body
        return _FAKE_UNIT

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.create_unit",
        fake_create_unit,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/units",
        json={"code": "A-101", "tower_id": TOWER_ID, "floor_id": FLOOR_ID},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == UNIT_ID


@pytest.mark.asyncio
async def test_list_units(monkeypatch, client):
    """GET /projects/{id}/units lists units."""

    _patch_projects_access(monkeypatch)

    async def fake_list_units(_self, *, project_id: str):
        del _self
        return [_FAKE_UNIT]

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.list_units",
        fake_list_units,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/units")
    body = assert_success(res, 200)
    assert body["data"][0]["code"] == "A-101"


@pytest.mark.asyncio
async def test_get_unit_detail(monkeypatch, client):
    """GET unit detail returns full unit payload."""

    _patch_projects_access(monkeypatch)

    async def fake_get_unit_detail(_self, *, project_id: str, unit_id: str):
        del _self
        assert unit_id == UNIT_ID
        return _FAKE_UNIT_DETAIL

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.get_unit_detail",
        fake_get_unit_detail,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/units/{UNIT_ID}/detail")
    body = assert_success(res, 200)
    assert body["data"]["occupancy_label"] == "Vacant"


@pytest.mark.asyncio
async def test_update_unit(monkeypatch, client):
    """PATCH unit updates unit row."""

    _patch_projects_access(monkeypatch)

    async def fake_update_unit(_self, *, project_id: str, unit_id: str, body):
        del _self, body
        assert unit_id == UNIT_ID
        return {**_FAKE_UNIT, "status": "occupied"}

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.update_unit",
        fake_update_unit,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/units/{UNIT_ID}",
        json={"status": "occupied"},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "occupied"


@pytest.mark.asyncio
async def test_delete_unit(monkeypatch, client):
    """DELETE unit removes unit row."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_unit(_self, *, project_id: str, unit_id: str):
        del _self
        assert unit_id == UNIT_ID
        return {"old_data": _FAKE_UNIT}

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.delete_unit",
        fake_delete_unit,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/units/{UNIT_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_parking_zone(monkeypatch, client):
    """POST parking-zones creates parking zone."""

    _patch_projects_access(monkeypatch)

    async def fake_create_zone(_self, *, project_id: str, body):
        del _self, body
        return _FAKE_PARKING_ZONE

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.create_parking_zone",
        fake_create_zone,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/parking-zones",
        json={
            "tower_id": TOWER_ID,
            "floor_id": FLOOR_ID,
            "name": "Basement P1",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == ZONE_ID


@pytest.mark.asyncio
async def test_list_parking_zones(monkeypatch, client):
    """GET parking-zones lists parking zones."""

    _patch_projects_access(monkeypatch)

    async def fake_list_zones(_self, *, project_id: str):
        del _self
        return [_FAKE_PARKING_ZONE]

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.list_parking_zones",
        fake_list_zones,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/parking-zones")
    body = assert_success(res, 200)
    assert body["data"][0]["name"] == "Basement P1"


@pytest.mark.asyncio
async def test_delete_parking_zone(monkeypatch, client):
    """DELETE parking-zone removes parking zone."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_zone(_self, *, project_id: str, zone_id: str):
        del _self
        assert zone_id == ZONE_ID
        return {"old_data": _FAKE_PARKING_ZONE}

    monkeypatch.setattr(
        "apps.user_service.app.services.units_service.UnitsService.delete_parking_zone",
        fake_delete_zone,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/parking-zones/{ZONE_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Site map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_project_location(monkeypatch, client):
    """PATCH site-map location updates lat/lng."""

    _patch_projects_access(monkeypatch)

    async def fake_update_location(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        return {"latitude": body.latitude, "longitude": body.longitude}

    monkeypatch.setattr(
        "apps.user_service.app.services.site_map_service.SiteMapService.update_location",
        fake_update_location,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/site-map/location",
        json={"latitude": 19.076, "longitude": 72.8777},
    )
    body = assert_success(res, 200)
    assert body["data"]["latitude"] == 19.076


@pytest.mark.asyncio
async def test_create_site_map_overlays(monkeypatch, client):
    """POST site-map overlays creates markers."""

    _patch_projects_access(monkeypatch)

    async def fake_create_overlays(_self, *, project_id: str, body):
        del _self, body
        return [_FAKE_OVERLAY]

    monkeypatch.setattr(
        "apps.user_service.app.services.site_map_service.SiteMapService.create_overlays",
        fake_create_overlays,
    )

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/site-map/overlays",
        json={
            "items": [
                {
                    "entity_type": "tower",
                    "entity_id": TOWER_ID,
                    "latitude": 19.076,
                    "longitude": 72.8777,
                }
            ]
        },
    )
    body = assert_success(res, 201)
    assert body["data"][0]["id"] == OVERLAY_ID


@pytest.mark.asyncio
async def test_list_site_map_overlays(monkeypatch, client):
    """GET site-map overlays lists markers."""

    _patch_projects_access(monkeypatch)

    async def fake_list_overlays(_self, *, project_id: str):
        del _self
        return [_FAKE_OVERLAY]

    monkeypatch.setattr(
        "apps.user_service.app.services.site_map_service.SiteMapService.list_overlays",
        fake_list_overlays,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/site-map/overlays")
    body = assert_success(res, 200)
    assert body["data"][0]["entity_id"] == TOWER_ID


@pytest.mark.asyncio
async def test_delete_site_map_overlay(monkeypatch, client):
    """DELETE site-map overlay removes marker."""

    _patch_projects_access(monkeypatch)

    async def fake_delete_overlay(_self, *, project_id: str, overlay_id: str):
        del _self
        assert overlay_id == OVERLAY_ID
        return {"old_data": _FAKE_OVERLAY}

    monkeypatch.setattr(
        "apps.user_service.app.services.site_map_service.SiteMapService.delete_overlay",
        fake_delete_overlay,
    )

    res = await client.delete(f"/v1/projects/{PROJECT_ID}/site-map/overlays/{OVERLAY_ID}")
    assert_success(res, 200)


# ---------------------------------------------------------------------------
# Vehicle requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_vehicle_requests(monkeypatch, client):
    """GET vehicle-requests lists pending vehicles."""

    _patch_projects_access(monkeypatch)

    async def fake_list_vehicles(_self, *, project_id: str, status=None):
        del _self, status
        return [_FAKE_VEHICLE]

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.list_project_vehicles",
        fake_list_vehicles,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/vehicle-requests")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == VEHICLE_ID


@pytest.mark.asyncio
async def test_review_vehicle_request(monkeypatch, client):
    """PATCH vehicle-requests approves a vehicle."""

    _patch_projects_access(monkeypatch)

    async def fake_review_vehicle(_self, *, project_id: str, vehicle_id: str, body):
        del _self
        assert vehicle_id == VEHICLE_ID
        assert body.status.value == "approved"
        return {**_FAKE_VEHICLE, "status": "approved", "parking_slot_id": SLOT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.review_vehicle",
        fake_review_vehicle,
    )

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/vehicle-requests/{VEHICLE_ID}",
        json={"status": "approved", "parking_slot_id": SLOT_ID},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "approved"
