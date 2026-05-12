"""Unit tests for UserService.update_organization_member_role."""

from __future__ import annotations

from typing import Any

import pytest

from apps.user_service.app.schemas.users import PatchUserRequest
from apps.user_service.app.services.user_service import UserService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)

ORG = "11111111-1111-1111-1111-111111111111"
CREATOR = "22222222-2222-2222-2222-222222222222"
ADMIN_USER = "33333333-3333-3333-3333-333333333333"
MEMBER = "44444444-4444-4444-4444-444444444444"
OTHER = "55555555-5555-5555-5555-555555555555"
ROLE_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE_MEMBER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ROLE_NEW = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _base_ctx_row(
    *,
    created_by_id: str = CREATOR,
    requester_role_name: str = "admin",
    target_role_name: str = "member",
    new_role_name: str = "custom",
) -> dict[str, Any]:
    """Return a default row as returned by fetch_context_for_member_role_change."""
    return {
        "created_by_id": created_by_id,
        "requester_user_id": ADMIN_USER,
        "requester_role_id": ROLE_ADMIN,
        "requester_status": "active",
        "target_user_id": MEMBER,
        "target_role_id": ROLE_MEMBER,
        "target_status": "active",
        "target_email": "m@example.com",
        "target_first_name": "M",
        "target_last_name": "1",
        "target_avatar_url": None,
        "target_phone_number": None,
        "target_phone_isd_code": None,
        "target_timezone": "UTC",
        "target_joined_at": None,
        "target_last_active_at": None,
        "target_organization_id": ORG,
        "requester_role_name": requester_role_name,
        "target_role_name": target_role_name,
        "new_role_id": ROLE_NEW,
        "new_role_name": new_role_name,
    }


class _FakeOrgMemberRepo:
    """Minimal fake for role-change paths."""

    def __init__(self) -> None:
        """Initialize fake fetch/update results."""
        self.fetch_result: dict[str, Any] | None = _base_ctx_row()
        self.update_result: dict[str, Any] | None = {"user_id": MEMBER}

    async def fetch_context_for_member_role_change(self, **_kwargs: Any) -> dict[str, Any] | None:
        """Return the configured fetch_context_for_member_role_change result."""
        return self.fetch_result

    async def update_user_info(
        self, _user_id: str, _organization_id: str, _update_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the configured update_user_info result."""
        return self.update_result


def _service(user_id: str) -> UserService:
    """Build a UserService with a fake org member repository for the given user id."""
    ctx = UserContext(
        user_id=user_id,
        email="u@example.com",
        organization_id=ORG,
        user_type=None,
    )
    svc = UserService(user_context=ctx, db_connection=None)
    svc.organization_member_repository = _FakeOrgMemberRepo()
    return svc


@pytest.mark.asyncio
async def test_creator_can_change_member_role() -> None:
    """Creator may change a member role when requester context matches creator."""
    svc = _service(CREATOR)
    fake = svc.organization_member_repository
    assert isinstance(fake, _FakeOrgMemberRepo)
    fake.fetch_result = _base_ctx_row(
        requester_role_name="member",
        target_role_name="member",
    )
    fake.fetch_result["requester_user_id"] = CREATOR
    fake.fetch_result["requester_role_id"] = ROLE_MEMBER
    fake.fetch_result["requester_role_name"] = "member"

    out = await svc.update_organization_member_role(MEMBER, ROLE_NEW)
    assert out["audit_data"]["role_id"] == ROLE_NEW
    assert out["current_user_data"]["user_id"] == MEMBER


@pytest.mark.asyncio
async def test_self_change_forbidden() -> None:
    """Users cannot change their own organization role."""
    svc = _service(CREATOR)
    with pytest.raises(ForbiddenException) as exc:
        await svc.update_organization_member_role(CREATOR, ROLE_NEW)
    assert exc.value.message_key == "users.errors.self_action"


@pytest.mark.asyncio
async def test_missing_organization_context() -> None:
    """Role change fails when the requester has no organization id."""
    ctx = UserContext(user_id=CREATOR, email="u@example.com", organization_id=None, user_type=None)
    svc = UserService(user_context=ctx, db_connection=None)
    svc.organization_member_repository = _FakeOrgMemberRepo()
    with pytest.raises(ValidationException):
        await svc.update_organization_member_role(MEMBER, ROLE_NEW)


@pytest.mark.asyncio
async def test_admin_can_change_non_admin() -> None:
    """Admin may change a non-admin member's role."""
    svc = _service(ADMIN_USER)
    out = await svc.update_organization_member_role(MEMBER, ROLE_NEW)
    assert out["audit_data"]["previous_role_name"] == "member"


@pytest.mark.asyncio
async def test_patch_organization_member_applies_role() -> None:
    """patch_organization_member delegates a role_id patch to update_organization_member_role."""
    svc = _service(ADMIN_USER)
    out = await svc.patch_organization_member(MEMBER, PatchUserRequest(role_id=ROLE_NEW))
    assert out["audit_data"]["role_id"] == ROLE_NEW


@pytest.mark.asyncio
async def test_admin_cannot_change_other_admin() -> None:
    """Admin cannot change another admin's role."""
    svc = _service(ADMIN_USER)
    fake = svc.organization_member_repository
    assert isinstance(fake, _FakeOrgMemberRepo)
    fake.fetch_result = _base_ctx_row(target_role_name="admin")
    with pytest.raises(ForbiddenException) as exc:
        await svc.update_organization_member_role(MEMBER, ROLE_NEW)
    assert exc.value.message_key == "users.errors.cannot_change_admin_user_role"


@pytest.mark.asyncio
async def test_admin_cannot_change_org_creator_role() -> None:
    """Non-creator admin cannot change the organization creator's role."""
    svc = _service(ADMIN_USER)
    fake = svc.organization_member_repository
    assert isinstance(fake, _FakeOrgMemberRepo)
    fake.fetch_result = _base_ctx_row(
        target_role_name="member",
    )
    fake.fetch_result["target_user_id"] = CREATOR
    fake.fetch_result["target_role_id"] = ROLE_MEMBER
    fake.fetch_result["target_email"] = "c@example.com"

    with pytest.raises(ForbiddenException) as exc:
        await svc.update_organization_member_role(CREATOR, ROLE_NEW)
    assert exc.value.message_key == "users.errors.cannot_change_organization_creator_role"


@pytest.mark.asyncio
async def test_non_admin_non_creator_forbidden() -> None:
    """Plain members cannot change other members' roles."""
    svc = _service(OTHER)
    fake = svc.organization_member_repository
    assert isinstance(fake, _FakeOrgMemberRepo)
    fake.fetch_result = _base_ctx_row(
        requester_role_name="member",
    )
    fake.fetch_result["requester_user_id"] = OTHER
    fake.fetch_result["requester_role_id"] = ROLE_MEMBER

    with pytest.raises(ForbiddenException) as exc:
        await svc.update_organization_member_role(MEMBER, ROLE_NEW)
    assert exc.value.message_key == "users.errors.cannot_change_member_role"


@pytest.mark.asyncio
async def test_org_not_found() -> None:
    """NotFound when repository returns no context row (e.g. org missing)."""
    svc = _service(CREATOR)
    fake = svc.organization_member_repository
    assert isinstance(fake, _FakeOrgMemberRepo)
    fake.fetch_result = None
    with pytest.raises(NotFoundException) as exc:
        await svc.update_organization_member_role(MEMBER, ROLE_NEW)
    assert exc.value.message_key == "organizations.errors.not_found"


@pytest.mark.asyncio
async def test_invalid_new_role_not_found() -> None:
    """NotFound when the target new role id is missing from organization roles."""
    svc = _service(CREATOR)
    fake = svc.organization_member_repository
    assert isinstance(fake, _FakeOrgMemberRepo)
    row = _base_ctx_row()
    row["requester_user_id"] = CREATOR
    row["requester_role_id"] = ROLE_ADMIN
    row["new_role_id"] = None
    row["new_role_name"] = None
    fake.fetch_result = row

    with pytest.raises(NotFoundException) as exc:
        await svc.update_organization_member_role(MEMBER, ROLE_NEW)
    assert exc.value.message_key == "users.errors.role_not_found"
