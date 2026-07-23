"""Unit tests for OrganizationService key methods."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.auth import CompanyData
from apps.user_service.app.schemas.enums import (
    AccountType,
    DeleteRequestStatus,
    OrganizationStatus,
    PlanType,
)
from apps.user_service.app.schemas.organizations import (
    NewOrganizationBody,
    OrganizationAdminUpdate,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
MEMBER_ID = "660e8400-e29b-41d4-a716-446655440001"
REQUEST_ID = "770e8400-e29b-41d4-a716-446655440002"


def _ctx() -> UserContext:
    """Build user context for organization tests."""
    return UserContext(user_id="admin-1", email="admin@example.com", organization_id=ORG_ID)


class _FakeOrgRepo:
    """Configurable fake OrganizationRepository."""

    def __init__(
        self,
        *,
        organizations: list[dict[str, Any]] | None = None,
        total: int | None = None,
        organization: dict[str, Any] | None = None,
        slug_unique: bool = True,
        is_owner: bool = False,
    ) -> None:
        self.organizations = organizations or []
        self.total = total if total is not None else len(self.organizations)
        self.organization = organization
        self.slug_unique = slug_unique
        self.is_owner = is_owner
        self.last_list_kwargs: dict[str, Any] | None = None
        self.last_count_kwargs: dict[str, Any] | None = None
        self.last_update: dict[str, Any] | None = None
        self.deleted_id: str | None = None

    async def get_organizations_list(self, **kwargs):
        """Return paginated organizations."""
        self.last_list_kwargs = kwargs
        return self.organizations

    async def get_organizations_count(self, **kwargs):
        """Return total organization count."""
        self.last_count_kwargs = kwargs
        return self.total

    async def get_organization_by_id(self, organization_id: str):
        """Return one organization row."""
        del organization_id
        return self.organization

    async def check_slug_unique(self, slug: str, exclude_id: str | None = None) -> bool:
        """Return slug uniqueness flag."""
        del slug, exclude_id
        return self.slug_unique

    async def update_organization(self, organization_id: str, update_data: dict[str, Any]):
        """Apply update and return merged row."""
        del organization_id
        self.last_update = update_data
        merged = dict(self.organization or {})
        merged.update({k: v for k, v in update_data.items() if k != "settings"})
        if "settings" in update_data:
            merged["settings"] = update_data["settings"]
        self.organization = merged
        return merged

    async def delete_organization(self, organization_id: str) -> None:
        """Record soft delete."""
        self.deleted_id = organization_id

    async def is_user_organization_owner(self, organization_id: str, user_id: str) -> bool:
        """Return owner flag."""
        del organization_id, user_id
        return self.is_owner


def _org_row(**overrides) -> dict[str, Any]:
    """Build an organization DB row."""
    row = {
        "id": ORG_ID,
        "name": "Acme Legal",
        "slug": "business-acme-legal",
        "domain": "acme.example.com",
        "logo_url": None,
        "status": OrganizationStatus.ACTIVE.value,
        "timezone": "UTC",
        "description": "Test org",
        "company_size": "11-50",
        "industry": "Legal",
        "referral_source": None,
        "member_count": 3,
        "settings": '{"website_url":"https://acme.example.com"}',
        "subscription": '{"plan_type":"trial"}',
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _service(*, org_repo: _FakeOrgRepo | None = None) -> OrganizationService:
    """Build OrganizationService with fake organization repo."""
    svc = OrganizationService(user_context=_ctx(), db_connection=MagicMock())
    svc.organization_repository = org_repo or _FakeOrgRepo()
    svc.organization_member_repository = MagicMock()
    svc.delete_request_repository = MagicMock()
    svc.team_repository = MagicMock()
    svc.role_repository = MagicMock()
    svc.permissions_repository = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_list_organizations_returns_page():
    """List organizations maps rows and pagination metadata."""
    repo = _FakeOrgRepo(organizations=[_org_row()], total=1)
    svc = _service(org_repo=repo)

    result = await svc.list_organizations(page=1, page_size=20, search="acme")

    assert result.total_count == 1
    assert result.data[0].name == "Acme Legal"
    assert result.page == 1
    assert repo.last_list_kwargs["search"] == "acme"


@pytest.mark.asyncio
async def test_list_organizations_empty_message():
    """Empty list uses no-data success message."""
    repo = _FakeOrgRepo(organizations=[], total=0)
    svc = _service(org_repo=repo)

    result = await svc.list_organizations()

    assert result.total_count == 0
    assert result.message == "success.no_data"


@pytest.mark.asyncio
async def test_get_organization_detail_found():
    """Get organization detail maps DB row to schema."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)

    result = await svc.get_organization_detail(ORG_ID)

    assert result.organization_id == ORG_ID
    assert result.name == "Acme Legal"
    assert result.website_url == "https://acme.example.com"


@pytest.mark.asyncio
async def test_get_organization_detail_not_found():
    """Missing organization raises NotFoundException."""
    svc = _service(org_repo=_FakeOrgRepo(organization=None))
    with pytest.raises(NotFoundException):
        await svc.get_organization_detail(ORG_ID)


@pytest.mark.asyncio
async def test_get_organization_detail_bad_uuid():
    """Invalid organization id raises ValidationException."""
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.get_organization_detail("not-a-uuid")


@pytest.mark.asyncio
async def test_get_ai_overview_settings_found():
    """AI overview settings resolve from organization settings."""
    settings = {
        "ai_overview_settings": {
            "business_overview": "Legal practice",
            "overview_prompts": {
                "lead": "Lead prompt",
                "contact": "Contact prompt",
                "company": "Company prompt",
            },
        }
    }
    repo = _FakeOrgRepo(organization=_org_row(settings=json.dumps(settings)))
    svc = _service(org_repo=repo)

    result = await svc.get_ai_overview_settings(ORG_ID)

    assert result.business_overview == "Legal practice"


@pytest.mark.asyncio
async def test_get_ai_overview_settings_not_found():
    """Missing org for AI settings raises NotFoundException."""
    svc = _service(org_repo=_FakeOrgRepo(organization=None))
    with pytest.raises(NotFoundException):
        await svc.get_ai_overview_settings(ORG_ID)


def test_generate_slug_business():
    """Slug generator prefixes business account slugs."""
    slug = OrganizationService._generate_slug("Acme Legal", AccountType.BUSINESS.value)
    assert slug == "business-acme-legal"


def test_generate_slug_personal():
    """Slug generator prefixes personal account slugs."""
    slug = OrganizationService._generate_slug("My Org", AccountType.PERSONAL.value)
    assert slug == "personal-my-org"


def test_map_to_organization_basic_details():
    """Basic details mapper exposes id, name, and settings fields."""
    mapped = OrganizationService._map_to_organization_basic_details(_org_row())
    assert mapped.id == ORG_ID
    assert mapped.name == "Acme Legal"


def test_format_organization_for_audit():
    """Audit formatter flattens organization settings."""
    audit = OrganizationService._format_organization_for_audit(_org_row())
    assert audit["organization_id"] == ORG_ID
    assert audit["website_url"] == "https://acme.example.com"


def test_deep_merge_dict_skips_none():
    """_deep_merge_dict preserves base values when update has None."""
    svc = _service()
    merged = svc._deep_merge_dict({"a": 1, "nested": {"x": 1}}, {"a": None, "nested": {"y": 2}})
    assert merged["a"] == 1
    assert merged["nested"] == {"x": 1, "y": 2}


def test_categorize_update_fields():
    """_categorize_update_fields splits direct and settings columns."""
    svc = _service()
    direct, nested, simple, practice, ai = svc._categorize_update_fields(
        {
            "name": "New",
            "website_url": "https://new.example.com",
            "address": {"city": "Mumbai"},
            "primary_practice_areas": ["Litigation"],
            "ai_overview_settings": {"business_overview": "Updated"},
        }
    )
    assert direct["name"] == "New"
    assert simple["website_url"] == "https://new.example.com"
    assert nested["address"]["city"] == "Mumbai"
    assert practice["primary_practice_areas"] == ["Litigation"]
    assert ai["business_overview"] == "Updated"


def test_build_update_payload_merges_settings():
    """_build_update_payload merges nested settings into JSON payload."""
    svc = _service()
    existing = {"website_url": "https://old.example.com", "address": {"city": "Delhi"}}
    payload = svc._build_update_payload(
        existing,
        {"website_url": "https://new.example.com", "address": {"state": "MH"}},
    )
    settings = payload["settings"]
    assert settings["website_url"] == "https://new.example.com"
    assert settings["address"]["city"] == "Delhi"
    assert settings["address"]["state"] == "MH"


def test_build_admin_update_payload_repopulate():
    """_build_admin_update_payload resets overview prompts when requested."""
    body = OrganizationAdminUpdate(
        repopulate_ai_overview_prompts=["lead", "contact"],
    )
    payload = OrganizationService._build_admin_update_payload(body)
    prompts = payload["ai_overview_settings"]["overview_prompts"]
    assert prompts["lead"] is None
    assert prompts["contact"] is None


@pytest.mark.asyncio
async def test_update_organization_success():
    """update_organization patches name and returns audit snapshot."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    body = OrganizationAdminUpdate(name="Renamed Legal")

    result = await svc.update_organization(ORG_ID, body)

    assert result["organization_name"] == "Renamed Legal"
    assert result["old_data"]["name"] == "Acme Legal"
    assert repo.last_update is not None


@pytest.mark.asyncio
async def test_update_organization_not_found():
    """update_organization raises when org is missing."""
    svc = _service(org_repo=_FakeOrgRepo(organization=None))
    with pytest.raises(NotFoundException):
        await svc.update_organization(ORG_ID, OrganizationAdminUpdate(name="X"))


@pytest.mark.asyncio
async def test_update_organization_slug_conflict():
    """update_organization rejects duplicate slug."""
    repo = _FakeOrgRepo(organization=_org_row(), slug_unique=False)
    svc = _service(org_repo=repo)
    with pytest.raises(ConflictException):
        await svc.update_organization(
            ORG_ID,
            OrganizationAdminUpdate(slug="business-taken"),
        )


@pytest.mark.asyncio
async def test_update_organization_status_suspended():
    """update_organization can set status to suspended."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    body = OrganizationAdminUpdate(status=OrganizationStatus.SUSPENDED)

    result = await svc.update_organization(ORG_ID, body)

    assert repo.last_update["status"] == OrganizationStatus.SUSPENDED.value
    assert result["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_delete_organization():
    """delete_organization soft-deletes by id."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    await svc.delete_organization(ORG_ID)
    assert repo.deleted_id == ORG_ID


@pytest.mark.asyncio
async def test_create_delete_request_success(monkeypatch):
    """create_delete_request inserts pending request."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    svc.delete_request_repository.get_pending_request_by_organization_and_requester = AsyncMock(
        return_value=None
    )
    svc.delete_request_repository.create_delete_request = AsyncMock(
        return_value={"id": REQUEST_ID, "status": DeleteRequestStatus.PENDING.value}
    )
    monkeypatch.setattr(
        OrganizationService,
        "_notify_super_admins",
        AsyncMock(),
    )

    result = await svc.create_delete_request(ORG_ID)

    assert result["id"] == REQUEST_ID


@pytest.mark.asyncio
async def test_create_delete_request_duplicate():
    """create_delete_request rejects duplicate pending request."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    svc.delete_request_repository.get_pending_request_by_organization_and_requester = AsyncMock(
        return_value={"id": REQUEST_ID}
    )
    with pytest.raises(ConflictException):
        await svc.create_delete_request(ORG_ID)


@pytest.mark.asyncio
async def test_list_delete_requests():
    """list_delete_requests returns paginated DeleteRequestInfo rows."""
    repo = _FakeOrgRepo()
    svc = _service(org_repo=repo)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    svc.delete_request_repository.get_delete_requests_list = AsyncMock(
        return_value=[
            {
                "id": REQUEST_ID,
                "organization_id": ORG_ID,
                "requester_id": "admin-1",
                "status": DeleteRequestStatus.PENDING.value,
                "requested_at": now,
                "created_at": now,
                "updated_at": now,
            }
        ]
    )
    svc.delete_request_repository.get_delete_requests_count = AsyncMock(return_value=1)

    result = await svc.list_delete_requests(page=1, page_size=20)

    assert result["total_count"] == 1
    assert result["data"][0].request_id == REQUEST_ID


@pytest.mark.asyncio
async def test_process_delete_request_reject(monkeypatch):
    """process_delete_request rejects pending request."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    svc.delete_request_repository.get_delete_request_by_id = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "requester_id": "admin-1",
            "status": DeleteRequestStatus.PENDING.value,
        }
    )
    svc.delete_request_repository.reject_delete_request = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "status": DeleteRequestStatus.REJECTED.value,
            "review_reason": "No",
            "reviewed_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
    )
    svc.organization_member_repository.get_user_profile_by_id = AsyncMock(
        return_value={"email": "admin@example.com"}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.send_organization_deletion_rejected_email",
        lambda **kwargs: True,
    )

    result = await svc.process_delete_request(REQUEST_ID, is_accepted=False, reason="No")

    assert result["status"] == DeleteRequestStatus.REJECTED.value


@pytest.mark.asyncio
async def test_process_delete_request_already_processed():
    """process_delete_request raises when request is not pending."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    svc.delete_request_repository.get_delete_request_by_id = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "status": DeleteRequestStatus.APPROVED.value,
        }
    )
    with pytest.raises(ForbiddenException):
        await svc.process_delete_request(REQUEST_ID, is_accepted=True, reason="Yes")


@pytest.mark.asyncio
async def test_delete_organization_member_success(monkeypatch):
    """delete_organization_member soft-deletes non-owner member."""
    repo = _FakeOrgRepo(is_owner=False)
    svc = _service(org_repo=repo)
    svc.organization_member_repository.get_user_profile_by_id = AsyncMock(
        return_value={
            "user_id": MEMBER_ID,
            "email": "m@example.com",
            "organization_id": ORG_ID,
            "role_id": "role-1",
        }
    )
    svc.organization_member_repository.delete_member_by_user_id = AsyncMock()
    svc.team_repository.delete_user_from_all_teams = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.revoke_org_member_sessions_everywhere",
        AsyncMock(),
    )

    result = await svc.delete_organization_member(MEMBER_ID)

    assert result["current_user_data"]["email"] == "m@example.com"
    assert result["audit_new"]["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_organization_member_owner():
    """delete_organization_member rejects organization owner."""
    repo = _FakeOrgRepo(is_owner=True)
    svc = _service(org_repo=repo)
    svc.organization_member_repository.get_user_profile_by_id = AsyncMock(
        return_value={"user_id": MEMBER_ID, "email": "o@example.com", "organization_id": ORG_ID}
    )
    with pytest.raises(BadRequestException):
        await svc.delete_organization_member(MEMBER_ID)


def test_parse_subscription_valid_json():
    """_parse_subscription parses JSON subscription payloads."""
    parsed = OrganizationService._parse_subscription('{"plan_type":"trial","status":"active"}')
    assert parsed is not None
    assert parsed.plan_type == "trial"


def test_parse_subscription_invalid_returns_none():
    """_parse_subscription returns None for invalid payloads."""
    assert OrganizationService._parse_subscription("not-json") is None
    assert OrganizationService._parse_subscription(None) is None


def test_build_subscription_and_settings():
    """_build_subscription and _build_settings derive org defaults from body."""
    body = NewOrganizationBody(
        company_data=CompanyData(
            company_name="Acme Legal",
            website_url="https://acme.example.com",
            industry="Legal",
            primary_practice_areas=["Litigation"],
        )
    )
    svc = _service()
    subscription = svc._build_subscription(body)
    settings = svc._build_settings(body)
    assert subscription["plan_type"] == PlanType.TRIAL
    assert settings["website_url"] == "https://acme.example.com"


def test_default_ai_overview_settings():
    """default_ai_overview_settings returns platform defaults."""
    defaults = OrganizationService.default_ai_overview_settings()
    assert defaults.overview_prompts.lead is not None


def test_merge_nested_settings_and_practice_areas():
    """_merge_nested_settings and _update_practice_areas merge nested JSON."""
    svc = _service()
    merged: dict[str, Any] = {"practice_areas": {"primary": ["Litigation"]}}
    svc._merge_nested_settings(merged, {"address": {"city": "Mumbai"}})
    assert merged["address"]["city"] == "Mumbai"
    svc._update_practice_areas(merged, {"primary_practice_areas": ["Corporate"]})
    assert merged["practice_areas"]["primary"] == ["Corporate"]


@pytest.mark.asyncio
async def test_refetch_ai_overview_settings_no_org():
    """refetch_ai_overview_settings requires session organization."""
    ctx = UserContext(user_id="admin-1", email="admin@example.com", organization_id=None)
    svc = OrganizationService(user_context=ctx, db_connection=MagicMock())
    with pytest.raises(BadRequestException):
        await svc.refetch_ai_overview_settings(["lead"])


@pytest.mark.asyncio
async def test_refetch_ai_overview_settings_enrichment_disabled(monkeypatch):
    """refetch_ai_overview_settings fails when enrichment is not configured."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.strands_enrichment_enabled",
        lambda: False,
    )
    with pytest.raises(ServiceUnavailableException):
        await svc.refetch_ai_overview_settings(["lead"])


@pytest.mark.asyncio
async def test_create_delete_request_org_not_found():
    """create_delete_request raises when organization missing."""
    svc = _service(org_repo=_FakeOrgRepo(organization=None))
    with pytest.raises(NotFoundException):
        await svc.create_delete_request(ORG_ID)


@pytest.mark.asyncio
async def test_validate_slug_unique_conflict():
    """_validate_slug_unique raises ConflictException for duplicate slug."""
    svc = _service(org_repo=_FakeOrgRepo(slug_unique=False))
    with pytest.raises(ConflictException):
        await svc._validate_slug_unique("business-taken")


@pytest.mark.asyncio
async def test_delete_organization_member_not_found():
    """delete_organization_member raises when member profile missing."""
    repo = _FakeOrgRepo(is_owner=False)
    svc = _service(org_repo=repo)
    svc.organization_member_repository.get_user_profile_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.delete_organization_member(MEMBER_ID)


@pytest.mark.asyncio
async def test_process_delete_request_approve(monkeypatch):
    """process_delete_request approve path delegates to _approve_delete_request."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    svc.delete_request_repository.get_delete_request_by_id = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "requester_id": "admin-1",
            "status": DeleteRequestStatus.PENDING.value,
        }
    )
    approve_mock = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "status": DeleteRequestStatus.APPROVED.value,
        }
    )
    monkeypatch.setattr(svc, "_approve_delete_request", approve_mock)

    result = await svc.process_delete_request(REQUEST_ID, is_accepted=True, reason="Yes")

    assert result["status"] == DeleteRequestStatus.APPROVED.value
    approve_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_permanently_delete_organization(monkeypatch):
    """permanently_delete_organization removes org data and notifies members."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(org_repo=repo)
    monkeypatch.setattr(
        svc,
        "_permanently_delete_organization_data",
        AsyncMock(return_value=["member@example.com"]),
    )
    monkeypatch.setattr(
        OrganizationService,
        "_notify_members_of_organization_deletion",
        lambda *args, **kwargs: None,
    )

    result = await svc.permanently_delete_organization(ORG_ID)

    assert result["organization_id"] == ORG_ID
    assert result["organization_name"] == "Acme Legal"


def _new_org_body() -> NewOrganizationBody:
    """Build minimal organization create body."""
    return NewOrganizationBody(
        company_data=CompanyData(
            company_name="Acme Legal",
            website_url="https://acme.example.com",
            primary_practice_areas=["Litigation"],
        )
    )


@pytest.mark.asyncio
async def test_create_organization_success(monkeypatch):
    """create_organization provisions org, roles, member, and session context."""
    repo = _FakeOrgRepo(slug_unique=True)
    svc = _service(org_repo=repo)
    svc.organization_repository.create_organization = AsyncMock(
        return_value={"name": "Acme Legal", "slug": "business-acme-legal"}
    )
    svc.lead_stage_repository = MagicMock()
    svc.lead_stage_repository.bulk_insert_default_stages_for_organization = AsyncMock()
    svc.email_template_repository = MagicMock()
    svc.email_template_repository.insert_default_layout = AsyncMock()
    svc.permissions_repository.create_default_permissions = AsyncMock(return_value=["perm-1"])
    svc.role_repository.create_role = AsyncMock(return_value={"id": "role-1"})
    svc.role_repository.assign_permissions_to_role = AsyncMock()
    svc.organization_member_repository.add_member = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.SessionManagementService",
        lambda **kwargs: MagicMock(update_session_organization_context=AsyncMock()),
    )
    monkeypatch.setattr(
        svc, "_create_isometrik_application_if_enabled", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(svc, "_enqueue_business_overview_enrichment", AsyncMock())
    monkeypatch.setattr(svc, "_create_isometrik_ai_agent_best_effort", AsyncMock())

    result = await svc.create_organization(_new_org_body(), slug=None, session_id="sess-1")

    assert result["organization_name"] == "Acme Legal"
    assert result["role_name"] == "admin"
    svc.organization_member_repository.add_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_organization_forbidden_without_user():
    """create_organization rejects missing user context."""
    ctx = UserContext(user_id=None, email=None, organization_id=ORG_ID)
    svc = OrganizationService(user_context=ctx, db_connection=MagicMock())
    with pytest.raises(ForbiddenException):
        await svc.create_organization(_new_org_body(), slug=None, session_id="sess-1")


@pytest.mark.asyncio
async def test_create_organization_for_owner_success(monkeypatch):
    """create_organization_for_owner skips session update but provisions org."""
    repo = _FakeOrgRepo(slug_unique=True)
    svc = _service(org_repo=repo)
    svc.organization_repository.create_organization = AsyncMock(
        return_value={"name": "Acme Legal", "slug": "business-acme-legal"}
    )
    svc.lead_stage_repository = MagicMock()
    svc.lead_stage_repository.bulk_insert_default_stages_for_organization = AsyncMock()
    svc.email_template_repository = MagicMock()
    svc.email_template_repository.insert_default_layout = AsyncMock()
    svc.permissions_repository.create_default_permissions = AsyncMock(return_value=["perm-1"])
    svc.role_repository.create_role = AsyncMock(return_value={"id": "role-1"})
    svc.role_repository.assign_permissions_to_role = AsyncMock()
    svc.organization_member_repository.add_member = AsyncMock()
    monkeypatch.setattr(
        svc, "_create_isometrik_application_if_enabled", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(svc, "_enqueue_business_overview_enrichment", AsyncMock())
    monkeypatch.setattr(svc, "_create_isometrik_ai_agent_best_effort", AsyncMock())

    result = await svc.create_organization_for_owner(_new_org_body())

    assert result["slug"] == "business-acme-legal"
    svc.lead_stage_repository.bulk_insert_default_stages_for_organization.assert_awaited_once()


@pytest.mark.asyncio
async def test_refetch_ai_overview_settings_success(monkeypatch):
    """refetch_ai_overview_settings delegates to enrichment service."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.strands_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "OrgBusinessOverviewEnrichmentService.refetch_ai_overview_fields",
        AsyncMock(return_value={"lead": "prompt"}),
    )

    result = await svc.refetch_ai_overview_settings(["lead"])

    assert result["lead"] == "prompt"


@pytest.mark.asyncio
async def test_create_isometrik_application_if_enabled(monkeypatch):
    """_create_isometrik_application_if_enabled returns application data."""
    svc = _service()
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.create_isometrik_application",
        AsyncMock(return_value={"data": {"projectId": "proj-1"}}),
    )

    result = await svc._create_isometrik_application_if_enabled(_new_org_body())

    assert result == {"projectId": "proj-1"}


@pytest.mark.asyncio
async def test_add_requesting_user_as_member_with_isometrik(monkeypatch):
    """_add_requesting_user_as_member stores isometrik user id when create succeeds."""
    svc = _service()
    svc.organization_member_repository.add_member = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.create_isometrik_user",
        AsyncMock(return_value={"userId": "iso-admin"}),
    )

    await svc._add_requesting_user_as_member(
        organization_id=ORG_ID,
        role_id="role-1",
        body=_new_org_body(),
        isometrik_creds={"userSecret": "secret"},
    )

    member_data = svc.organization_member_repository.add_member.await_args.kwargs["member_data"]
    assert member_data["isometrik_user_id"] == "iso-admin"


@pytest.mark.asyncio
async def test_enqueue_business_overview_enrichment(monkeypatch):
    """_enqueue_business_overview_enrichment delegates to enrichment service."""
    svc = _service()
    enqueue_mock = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "OrgBusinessOverviewEnrichmentService.enqueue_enrichment_requested",
        enqueue_mock,
    )

    await svc._enqueue_business_overview_enrichment(
        organization_id=ORG_ID,
        organization_name="Acme Legal",
        organization_website="https://acme.example.com",
        settings={"website_url": "https://acme.example.com"},
    )

    enqueue_mock.assert_awaited_once()
