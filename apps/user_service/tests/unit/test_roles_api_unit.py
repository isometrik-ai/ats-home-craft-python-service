"""Unit tests for roles API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from starlette.requests import Request

from apps.user_service.app.api.roles import (
    create_new_role,
    delete_role_data,
    get_role_from_id,
    get_roles,
    update_role_data,
)
from apps.user_service.app.schemas.admin_access_management import (
    CreateRoleRequest,
    PermissionItem,
    RoleDetailItem,
    UpdateRoleRequest,
)
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ROLE_ID = UUID("990e8400-e29b-41d4-a716-446655440004")
PERM_ID = "aa0e8400-e29b-41d4-a716-446655440005"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/roles",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
        }
    )


def _user_ctx() -> UserContext:
    return UserContext(user_id="u1", email="admin@example.com", organization_id=ORG_ID)


def _role_detail(**overrides) -> RoleDetailItem:
    data = {
        "id": str(ROLE_ID),
        "name": "Admin",
        "description": "Admin role",
        "is_default": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "permissions": [
            PermissionItem(
                id=PERM_ID,
                name="View",
                code="perm.view",
                category="settings",
                description="View",
                created_at="2026-01-01T00:00:00Z",
            )
        ],
    }
    data.update(overrides)
    return RoleDetailItem(**data)


@pytest.mark.asyncio
async def test_get_roles_empty_returns_204() -> None:
    """Empty role list returns 204."""
    with (
        patch(
            "apps.user_service.app.api.roles.check_permissions", AsyncMock(return_value=_user_ctx())
        ),
        patch("apps.user_service.app.api.roles.RoleService") as svc_cls,
    ):
        svc_cls.return_value.list_roles = AsyncMock(return_value=([], 0))
        response = await get_roles(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
            search=None,
            page=1,
            page_size=20,
        )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_get_roles_returns_list() -> None:
    """Role list returns 200 with items."""
    with (
        patch(
            "apps.user_service.app.api.roles.check_permissions", AsyncMock(return_value=_user_ctx())
        ),
        patch("apps.user_service.app.api.roles.RoleService") as svc_cls,
    ):
        svc_cls.return_value.list_roles = AsyncMock(return_value=([{"id": str(ROLE_ID)}], 1))
        response = await get_roles(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
            search="admin",
            page=1,
            page_size=20,
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_role_from_id_route() -> None:
    """get_role_from_id returns role detail payload."""
    with (
        patch(
            "apps.user_service.app.api.roles.check_permissions", AsyncMock(return_value=_user_ctx())
        ),
        patch("apps.user_service.app.api.roles.RoleService") as svc_cls,
    ):
        svc_cls.return_value.get_role_details = AsyncMock(return_value=_role_detail())
        response = await get_role_from_id(
            request=_request(),
            db_connection=MagicMock(),
            role_id=ROLE_ID,
            current_user={"sub": "u1"},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_new_role_route() -> None:
    """create_new_role sets audit context and returns 201."""
    request = _request()
    with (
        patch(
            "apps.user_service.app.api.roles.check_permissions", AsyncMock(return_value=_user_ctx())
        ),
        patch("apps.user_service.app.api.roles.RoleService") as svc_cls,
    ):
        svc_cls.return_value.create_role = AsyncMock(
            return_value={"id": str(ROLE_ID), "created_at": "2026-01-01T00:00:00Z"}
        )
        response = await create_new_role(
            request=request,
            role_data=CreateRoleRequest(
                name="Reviewer",
                description="Review only",
                permission_ids=[PERM_ID],
            ),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
        )
    assert response.status_code == 201
    assert request.state.audit_table == "roles"


@pytest.mark.asyncio
async def test_update_role_data_route() -> None:
    """update_role_data captures audit snapshots."""
    request = _request()
    detail = _role_detail()
    with (
        patch(
            "apps.user_service.app.api.roles.check_permissions", AsyncMock(return_value=_user_ctx())
        ),
        patch("apps.user_service.app.api.roles.RoleService") as svc_cls,
    ):
        svc_cls.return_value.get_role_details = AsyncMock(side_effect=[detail, detail])
        svc_cls.return_value.update_role = AsyncMock(return_value=None)
        response = await update_role_data(
            request=request,
            role_data=UpdateRoleRequest(name="Updated Admin"),
            db_connection=MagicMock(),
            role_id=ROLE_ID,
            current_user={"sub": "u1"},
        )
    assert response.status_code == 200
    assert request.state.raw_audit_old_data["role_name"] == "Admin"


@pytest.mark.asyncio
async def test_delete_role_data_route() -> None:
    """delete_role_data returns success."""
    request = _request()
    with (
        patch(
            "apps.user_service.app.api.roles.check_permissions", AsyncMock(return_value=_user_ctx())
        ),
        patch("apps.user_service.app.api.roles.RoleService") as svc_cls,
    ):
        svc_cls.return_value.get_role_details = AsyncMock(return_value=_role_detail())
        svc_cls.return_value.delete_role = AsyncMock(return_value=None)
        response = await delete_role_data(
            request=request,
            db_connection=MagicMock(),
            role_id=ROLE_ID,
            current_user={"sub": "u1"},
        )
    assert response.status_code == 200
