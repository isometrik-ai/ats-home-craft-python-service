"""Unit tests for UserService key methods with fake repos."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import OrganizationMemberStatus
from apps.user_service.app.schemas.users import (
    PatchUserRequest,
    PermissionInfo,
    UpdateUserProfileRequest,
)
from apps.user_service.app.services.user_service import UserService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ADMIN_ID = "660e8400-e29b-41d4-a716-446655440001"
MEMBER_ID = "770e8400-e29b-41d4-a716-446655440002"
ROLE_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ctx(*, user_id: str = ADMIN_ID) -> UserContext:
    """Build user context for user service tests."""
    return UserContext(
        user_id=user_id,
        email="admin@example.com",
        organization_id=ORG_ID,
    )


class _FakeOrgMemberRepo:
    """Fake OrganizationMemberRepository."""

    def __init__(
        self,
        *,
        profile: dict[str, Any] | None = None,
        users: list[dict[str, Any]] | None = None,
        total: int = 0,
        user_exists: bool = False,
        suspend_ok: bool = True,
        revoke_ok: bool = True,
    ) -> None:
        self.profile = profile
        self.users = users or []
        self.total = total
        self.user_exists = user_exists
        self.suspend_ok = suspend_ok
        self.revoke_ok = revoke_ok
        self.db_connection = MagicMock()
        self.last_suspend: tuple[str, str] | None = None
        self.last_email_update: dict[str, Any] | None = None

    async def get_user_profile_by_id(self, user_id: str, organization_id: str):
        """Return configured profile."""
        del user_id, organization_id
        return self.profile

    async def check_user_exists(self, email: str, organization_id: str) -> bool:
        """Return existence flag."""
        del email, organization_id
        return self.user_exists

    async def check_phone_exists_for_other_user(self, **kwargs) -> bool:
        """Return False by default."""
        del kwargs
        return False

    async def get_users_details_list(self, **kwargs):
        """Return user rows."""
        del kwargs
        return self.users

    async def get_users_total_count(self, **kwargs) -> int:
        """Return total count."""
        del kwargs
        return self.total

    async def suspend_user(self, user_id: str, organization_id: str) -> bool:
        """Record suspend call."""
        self.last_suspend = (user_id, organization_id)
        return self.suspend_ok

    async def revoke_suspended_user(self, user_id: str, organization_id: str) -> bool:
        """Record revoke call."""
        del user_id, organization_id
        return self.revoke_ok

    async def update_user_email(self, user_id: str, organization_id: str, new_email: str):
        """Record email update."""
        self.last_email_update = {
            "user_id": user_id,
            "organization_id": organization_id,
            "new_email": new_email,
        }
        return {"user_id": user_id}

    async def get_organization_member_status_by_email(self, email: str):
        """Return member status."""
        del email
        return OrganizationMemberStatus.ACTIVE.value

    async def update_user_info(self, user_id: str, organization_id: str, update_data: dict):
        """Record member info update."""
        del user_id, organization_id, update_data
        return {"ok": True}

    async def get_user_role_id(self, user_id: str, organization_id: str | None):
        """Return configured role id."""
        del user_id, organization_id
        return getattr(self, "role_id", ROLE_ID)

    async def get_role_permissions(self, role_id: str, organization_id: str):
        """Return configured permissions."""
        del role_id, organization_id
        return getattr(self, "permissions", [])

    async def add_member(self, organization_id: str, member_data: dict):
        """Record add_member call."""
        self.last_add_member = {"organization_id": organization_id, **member_data}
        return member_data

    async def update_user_activity(self, user_id: str, organization_id: str) -> None:
        """Record activity update."""
        self.last_activity = (user_id, organization_id)


class _FakeOrgRepo:
    """Fake OrganizationRepository."""

    def __init__(
        self,
        *,
        org: dict[str, Any] | None = None,
        organizations: list[dict[str, Any]] | None = None,
    ) -> None:
        self.org = org or {"id": ORG_ID, "name": "Acme Legal"}
        self.organizations = organizations or []

    async def get_organization_by_id(self, organization_id: str):
        """Return org row."""
        del organization_id
        return self.org

    async def get_organization_details(self, organization_id: str):
        """Return org details row."""
        del organization_id
        return self.org

    async def get_user_active_organizations(self, user_id: str):
        """Return active organizations for user."""
        del user_id
        return self.organizations


class _FakeRoleRepo:
    """Fake RoleRepository."""

    def __init__(self, *, role: dict[str, Any] | None = None) -> None:
        self.role = role or {"id": ROLE_ID, "name": "member", "description": ""}
        self.permission_counts = {ROLE_ID: 5}
        self.permissions = [{"id": "p1", "name": "Read", "code": "users.read", "category": "users"}]

    async def get_role_by_id(self, role_id: str, organization_id: str):
        """Return role row."""
        del role_id, organization_id
        return self.role

    async def get_permission_counts_for_roles(self, role_ids, organization_id):
        """Return permission counts map."""
        del organization_id
        return {rid: self.permission_counts.get(str(rid), 0) for rid in role_ids}

    async def get_role_permissions(self, role_id: str, organization_id: str):
        """Return permissions for role."""
        del role_id, organization_id
        return self.permissions


def _profile(**overrides) -> dict[str, Any]:
    """Build a member profile row."""
    row = {
        "user_id": MEMBER_ID,
        "email": "member@example.com",
        "first_name": "Member",
        "last_name": "One",
        "organization_id": ORG_ID,
        "role_id": ROLE_ID,
        "status": OrganizationMemberStatus.ACTIVE.value,
        "joined_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "last_active_at": None,
        "avatar_url": None,
        "timezone": "UTC",
    }
    row.update(overrides)
    return row


def _service(
    *,
    member_repo: _FakeOrgMemberRepo | None = None,
    org_repo: _FakeOrgRepo | None = None,
    role_repo: _FakeRoleRepo | None = None,
    user_id: str = ADMIN_ID,
) -> UserService:
    """Build UserService with fake repositories."""
    svc = UserService(user_context=_ctx(user_id=user_id), db_connection=MagicMock())
    svc.organization_member_repository = member_repo or _FakeOrgMemberRepo()
    svc.organization_repository = org_repo or _FakeOrgRepo()
    svc.role_repository = role_repo or _FakeRoleRepo()
    return svc


@pytest.mark.asyncio
async def test_check_user_exists_delegates():
    """check_user_exists returns repo flag."""
    repo = _FakeOrgMemberRepo(user_exists=True)
    svc = _service(member_repo=repo)
    assert await svc.check_user_exists("x@y.com", ORG_ID) is True


@pytest.mark.asyncio
async def test_get_users_list_transforms_rows():
    """get_users_list maps rows to UserListItem with permission counts."""
    users = [
        {
            "user_id": MEMBER_ID,
            "email": "member@example.com",
            "role_id": ROLE_ID,
            "role": "member",
            "member_role": "member",
            "status": "active",
            "joined_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    ]
    repo = _FakeOrgMemberRepo(users=users, total=1)
    svc = _service(member_repo=repo)

    result = await svc.get_users_list(ORG_ID, search="mem", limit=10, offset=0)

    assert result["total_count"] == 1
    assert result["users"][0].email == "member@example.com"
    assert result["users"][0].permissions_count == 5


@pytest.mark.asyncio
async def test_get_users_total_count():
    """get_users_total_count delegates to repository."""
    repo = _FakeOrgMemberRepo(total=42)
    svc = _service(member_repo=repo)
    assert await svc.get_users_total_count(ORG_ID) == 42


@pytest.mark.asyncio
async def test_suspend_and_revoke_user():
    """suspend_user and revoke_suspended_user delegate to repo."""
    repo = _FakeOrgMemberRepo()
    svc = _service(member_repo=repo)

    assert await svc.suspend_user(MEMBER_ID, ORG_ID) is True
    assert repo.last_suspend == (MEMBER_ID, ORG_ID)
    assert await svc.revoke_suspended_user(MEMBER_ID, ORG_ID) is True


@pytest.mark.asyncio
async def test_ban_user_success(monkeypatch):
    """ban_user suspends member and returns audit payload."""
    repo = _FakeOrgMemberRepo(profile=_profile(), suspend_ok=True)
    svc = _service(member_repo=repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.revoke_org_member_sessions_everywhere",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.send_org_member_banned_email",
        lambda **kwargs: None,
    )

    result = await svc.ban_user(MEMBER_ID, ORG_ID)

    assert result["audit_data"]["status"] == OrganizationMemberStatus.SUSPENDED.value
    assert result["current_user_data"]["email"] == "member@example.com"


@pytest.mark.asyncio
async def test_ban_self_raises():
    """Users cannot ban themselves."""
    svc = _service(user_id=ADMIN_ID)
    with pytest.raises(BadRequestException):
        await svc.ban_user(ADMIN_ID, ORG_ID)


@pytest.mark.asyncio
async def test_ban_missing_user_raises():
    """ban_user raises when profile is missing."""
    repo = _FakeOrgMemberRepo(profile=None)
    svc = _service(member_repo=repo)
    with pytest.raises(NotFoundException):
        await svc.ban_user(MEMBER_ID, ORG_ID)


@pytest.mark.asyncio
async def test_unban_user_success(monkeypatch):
    """unban_user reactivates member and returns audit payload."""
    repo = _FakeOrgMemberRepo(profile=_profile(), revoke_ok=True)
    svc = _service(member_repo=repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.send_org_member_unbanned_email",
        lambda **kwargs: None,
    )

    result = await svc.unban_user(MEMBER_ID, ORG_ID)

    assert result["audit_data"]["status"] == OrganizationMemberStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_update_user_email_success(monkeypatch):
    """update_user_email updates org member and Supabase."""
    repo = _FakeOrgMemberRepo(profile=_profile())
    svc = _service(member_repo=repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.update_supabase_user_email",
        AsyncMock(),
    )

    result = await svc.update_user_email(MEMBER_ID, ORG_ID, "new@example.com")

    assert result["current_user_data"]["email"] == "member@example.com"
    assert repo.last_email_update["new_email"] == "new@example.com"


@pytest.mark.asyncio
async def test_update_user_email_not_found():
    """update_user_email raises when member missing."""
    svc = _service(member_repo=_FakeOrgMemberRepo(profile=None))
    with pytest.raises(NotFoundException):
        await svc.update_user_email(MEMBER_ID, ORG_ID, "new@example.com")


@pytest.mark.asyncio
async def test_get_member_status_by_email():
    """get_organization_member_status_by_email delegates."""
    svc = _service()
    status = await svc.get_organization_member_status_by_email("a@b.com")
    assert status == OrganizationMemberStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_get_user_profile_by_id_enriches_role():
    """get_user_profile_by_id attaches role info."""
    repo = _FakeOrgMemberRepo(profile=_profile())
    svc = _service(member_repo=repo)

    profile = await svc.get_user_profile_by_id(MEMBER_ID, ORG_ID)

    assert profile is not None
    assert profile["roles"]["name"] == "member"


@pytest.mark.asyncio
async def test_get_user_profile_missing_org():
    """get_user_profile_by_id returns None without organization_id."""
    svc = _service()
    assert await svc.get_user_profile_by_id(MEMBER_ID, None) is None


def test_build_update_payload_first_name():
    """_build_update_payload maps profile fields to DB and metadata."""
    svc = _service()
    body = UpdateUserProfileRequest(first_name="Jane", last_name="Doe")
    update_data, metadata = svc._build_update_payload(body)
    assert update_data["first_name"] == "Jane"
    assert metadata["last_name"] == "Doe"


def test_build_audit_data_counts_permissions():
    """_build_audit_data includes permission count."""
    svc = _service()
    perms = [
        PermissionInfo(
            permission_id="p1",
            permission_name="Read Users",
            permission_code="users.read",
        )
    ]
    audit = svc._build_audit_data(_profile(), perms)
    assert audit["permission_count"] == 1
    assert audit["user_id"] == MEMBER_ID


@pytest.mark.asyncio
async def test_create_new_user_success():
    """create_new_user delegates member insert to repository."""
    repo = _FakeOrgMemberRepo()
    svc = _service(member_repo=repo)
    user_data = {
        "user_id": MEMBER_ID,
        "email": "new@example.com",
        "organization_id": ORG_ID,
        "first_name": "New",
    }

    result = await svc.create_new_user(user_data)

    assert result["email"] == "new@example.com"
    assert repo.last_add_member["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_create_new_user_missing_org_raises():
    """create_new_user requires organization_id."""
    svc = _service()
    with pytest.raises(BadRequestException):
        await svc.create_new_user({"user_id": MEMBER_ID, "email": "x@y.com"})


@pytest.mark.asyncio
async def test_get_user_permissions_empty_user_id():
    """get_user_permissions returns empty list for blank user id."""
    svc = _service()
    assert await svc.get_user_permissions("", ORG_ID) == []


@pytest.mark.asyncio
async def test_get_user_permissions_without_org():
    """get_user_permissions returns empty when organization_id missing."""
    repo = _FakeOrgMemberRepo()
    repo.role_id = ROLE_ID
    svc = _service(member_repo=repo)
    assert await svc.get_user_permissions(MEMBER_ID, None) == []


@pytest.mark.asyncio
async def test_get_user_permissions_success():
    """get_user_permissions returns role permissions from repository."""
    repo = _FakeOrgMemberRepo()
    repo.permissions = [{"id": "p1", "name": "Read", "code": "users.read", "category": "users"}]
    svc = _service(member_repo=repo)

    perms = await svc.get_user_permissions(MEMBER_ID, ORG_ID)

    assert len(perms) == 1
    assert perms[0]["code"] == "users.read"


@pytest.mark.asyncio
async def test_update_user_info_delegates():
    """update_user_info forwards payload to repository."""
    repo = _FakeOrgMemberRepo()
    svc = _service(member_repo=repo)
    result = await svc.update_user_info(MEMBER_ID, ORG_ID, {"first_name": "Jane"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user():
    """check_phone_exists_for_other_user delegates to repository."""
    repo = _FakeOrgMemberRepo()
    repo.check_phone_exists_for_other_user = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc = _service(member_repo=repo)
    assert await svc.check_phone_exists_for_other_user("+1", "555", ORG_ID, MEMBER_ID) is True


@pytest.mark.asyncio
async def test_update_user_activity():
    """update_user_activity records activity via repository."""
    repo = _FakeOrgMemberRepo()
    svc = _service(member_repo=repo)
    await svc.update_user_activity(MEMBER_ID, ORG_ID)
    assert repo.last_activity == (MEMBER_ID, ORG_ID)


@pytest.mark.asyncio
async def test_transform_users_empty():
    """transform_users returns empty list for no rows."""
    svc = _service()
    assert await svc.transform_users([], ORG_ID) == []


@pytest.mark.asyncio
async def test_patch_organization_member_no_fields():
    """patch_organization_member rejects patch without supported fields."""
    svc = _service()
    patch = PatchUserRequest.model_construct(role_id=None)
    with pytest.raises(ValidationException):
        await svc.patch_organization_member(MEMBER_ID, patch)


def test_extract_auth_user_contact_from_dict():
    """_extract_auth_user_contact reads email and metadata from auth payload."""
    user_data = {
        "email": "auth@example.com",
        "user_metadata": {
            "phone_number": "9876543210",
            "phone_isd_code": "+91",
        },
    }
    email, metadata, phone, isd = UserService._extract_auth_user_contact(
        user_data, fallback_email="fallback@example.com"
    )
    assert email == "auth@example.com"
    assert phone == "9876543210"
    assert isd == "+91"
    assert metadata["phone_number"] == "9876543210"


def test_build_or_update_profile_from_metadata():
    """_build_or_update_profile creates profile when org member row missing."""
    svc = _service()
    profile = svc._build_or_update_profile(
        base_profile=None,
        user_metadata={"first_name": "Auth", "last_name": "User", "timezone": "Asia/Kolkata"},
        user_id=MEMBER_ID,
        current_email="auth@example.com",
        phone_number="123",
        phone_isd_code="+1",
    )
    assert profile["first_name"] == "Auth"
    assert profile["email"] == "auth@example.com"
    assert profile["has_password"] is False


def test_build_identities_email_provider():
    """_build_identities maps Supabase identity rows."""
    identities = UserService._build_identities(
        {
            "identities": [
                {
                    "provider": "email",
                    "identity_data": {"email": "id@example.com"},
                    "email": "id@example.com",
                    "created_at": "2026-01-01",
                    "updated_at": "2026-01-02",
                }
            ]
        }
    )
    assert identities[0]["provider"] == "email"
    assert identities[0]["provider_id"] == "id@example.com"


def test_build_role_info_with_role():
    """_build_role_info maps role fields from profile."""
    role = UserService._build_role_info(
        {"role_id": ROLE_ID, "role": "member", "role_description": "Member role"}
    )
    assert role.role_name == "member"
    assert role.description == "Member role"


def test_format_permissions_maps_fields():
    """_format_permissions converts repository rows to PermissionInfo."""
    perms = UserService._format_permissions(
        [{"id": "p1", "name": "Read Users", "code": "users.read", "category": "users"}]
    )
    assert perms[0].permission_code == "users.read"


@pytest.mark.asyncio
async def test_get_user_organizations():
    """get_user_organizations maps repository rows to schema objects."""
    org_repo = _FakeOrgRepo(organizations=[{"id": ORG_ID, "name": "Acme", "domain": "acme.test"}])
    svc = _service(org_repo=org_repo)
    orgs = await svc.get_user_organizations(ADMIN_ID)
    assert orgs[0].name == "Acme"


@pytest.mark.asyncio
async def test_ban_user_suspend_failure():
    """ban_user raises when suspend_user returns False."""
    repo = _FakeOrgMemberRepo(profile=_profile(), suspend_ok=False)
    svc = _service(member_repo=repo)
    with pytest.raises(NotFoundException):
        await svc.ban_user(MEMBER_ID, ORG_ID)


@pytest.mark.asyncio
async def test_ban_user_email_failure_still_succeeds(monkeypatch):
    """ban_user continues when notification email raises."""
    repo = _FakeOrgMemberRepo(profile=_profile())
    svc = _service(member_repo=repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.revoke_org_member_sessions_everywhere",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.send_org_member_banned_email",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("smtp down")),
    )

    result = await svc.ban_user(MEMBER_ID, ORG_ID)

    assert result["audit_data"]["status"] == OrganizationMemberStatus.SUSPENDED.value


@pytest.mark.asyncio
async def test_unban_self_raises():
    """Users cannot unban themselves."""
    svc = _service(user_id=ADMIN_ID)
    with pytest.raises(BadRequestException):
        await svc.unban_user(ADMIN_ID, ORG_ID)


@pytest.mark.asyncio
async def test_unban_missing_user_raises():
    """unban_user raises when profile is missing."""
    svc = _service(member_repo=_FakeOrgMemberRepo(profile=None))
    with pytest.raises(NotFoundException):
        await svc.unban_user(MEMBER_ID, ORG_ID)


@pytest.mark.asyncio
async def test_unban_revoke_failure_raises():
    """unban_user raises when revoke_suspended_user returns False."""
    repo = _FakeOrgMemberRepo(profile=_profile(), revoke_ok=False)
    svc = _service(member_repo=repo)
    with pytest.raises(NotFoundException):
        await svc.unban_user(MEMBER_ID, ORG_ID)


@pytest.mark.asyncio
async def test_get_user_profile_with_metadata(monkeypatch):
    """get_user_profile_with_metadata merges auth metadata and permissions."""
    member_repo = _FakeOrgMemberRepo(profile=_profile())
    member_repo.permissions = [
        {"id": "p1", "name": "Read", "code": "users.read", "category": "users"}
    ]
    org_repo = _FakeOrgRepo()
    svc = _service(member_repo=member_repo, org_repo=org_repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_user_by_id",
        AsyncMock(
            return_value={
                "email": "member@example.com",
                "user_metadata": {"first_name": "Member", "last_name": "One"},
                "identities": [],
            }
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.get_isometrik_details",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.is_system_super_admin",
        AsyncMock(return_value=False),
    )

    result = await svc.get_user_profile_with_metadata(MEMBER_ID, ORG_ID)

    assert result["profile_data"]["email"] == "member@example.com"
    assert result["audit_data"]["permission_count"] == 1


@pytest.mark.asyncio
async def test_update_user_profile_success(monkeypatch):
    """update_user_profile updates org member and auth metadata."""
    repo = _FakeOrgMemberRepo(profile=_profile())
    svc = _service(member_repo=repo)
    updated_profile = _profile(first_name="Updated")
    monkeypatch.setattr(
        "apps.user_service.app.services.user_service.update_metadata",
        AsyncMock(),
    )
    monkeypatch.setattr(svc, "_update_isometrik_user_if_needed", AsyncMock())
    monkeypatch.setattr(svc, "get_user_profile_by_id", AsyncMock(return_value=updated_profile))

    result = await svc.update_user_profile(
        MEMBER_ID,
        ORG_ID,
        UpdateUserProfileRequest(first_name="Updated", timezone="UTC"),
    )

    assert result["updated_profile"]["first_name"] == "Updated"
    assert result["audit_data"]["first_name"] == "Updated"


@pytest.mark.asyncio
async def test_update_user_profile_no_fields_raises():
    """update_user_profile rejects empty patch."""
    svc = _service(member_repo=_FakeOrgMemberRepo(profile=_profile()))
    with pytest.raises(BadRequestException):
        await svc.update_user_profile(MEMBER_ID, ORG_ID, UpdateUserProfileRequest())


def test_build_verification_metadata_invalid_method():
    """_build_verification_metadata rejects unsupported verification methods."""
    svc = _service()
    body = UpdateUserProfileRequest(two_fa_enabled=True, verification_method="SMS")
    with pytest.raises(BadRequestException):
        svc._build_verification_metadata(body)


def test_build_verification_metadata_enabled():
    """_build_verification_metadata stores 2FA preference in metadata."""
    svc = _service()
    body = UpdateUserProfileRequest(two_fa_enabled=True, verification_method="PHONE")
    metadata = svc._build_verification_metadata(body)
    assert metadata["verification_preference"]["type"] == "PHONE"
