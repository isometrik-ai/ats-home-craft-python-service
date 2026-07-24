"""Unit tests for SuperadminOrganizationService."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.auth import CompanyData
from apps.user_service.app.schemas.enums import (
    OrganizationStatus,
    PlanType,
    SuperadminOrganizationListSortField,
    SuperadminOrganizationListSortOrder,
    SuperadminOrganizationListStatus,
)
from apps.user_service.app.schemas.organizations import NewOrganizationBody
from apps.user_service.app.services.superadmin_organization_service import (
    SuperadminOrganizationService,
)
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
OWNER_ID = "660e8400-e29b-41d4-a716-446655440001"


def _list_row(**overrides) -> dict[str, Any]:
    """Build superadmin org list DB row."""
    row = {
        "id": ORG_ID,
        "name": "Acme Corp",
        "owner_user_id": OWNER_ID,
        "owner_email": "owner@example.com",
        "owner_first_name": "Jane",
        "owner_last_name": "Doe",
        "member_count": 5,
        "plan_type": "trial",
        "list_status": SuperadminOrganizationListStatus.ACTIVE.value,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _org_row(**overrides) -> dict[str, Any]:
    """Build organization detail DB row."""
    row = {
        "id": ORG_ID,
        "name": "Acme Corp",
        "slug": "acme",
        "domain": "acme.example.com",
        "logo_url": None,
        "status": OrganizationStatus.ACTIVE.value,
        "timezone": "UTC",
        "settings": "{}",
        "subscription": '{"plan_type":"trial"}',
        "description": "Test",
        "company_size": "11-50",
        "member_count": 3,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class _FakeOrgRepo:
    """Configurable fake OrganizationRepository."""

    def __init__(
        self,
        *,
        list_rows: list | None = None,
        list_total: int = 0,
        organization: dict | None = None,
        impersonation_row: dict | None = None,
        update_result: dict | None = None,
    ) -> None:
        self.db_connection = MagicMock()
        self.list_rows = list_rows or []
        self.list_total = list_total
        self.organization = organization
        self.impersonation_row = impersonation_row
        self.update_result = update_result if update_result is not None else {"id": ORG_ID}
        self.last_list_kwargs: dict | None = None
        self.last_update: tuple[str, dict] | None = None

    async def get_superadmin_organizations_list(self, **kwargs):
        """Return configured list rows and total."""
        self.last_list_kwargs = kwargs
        return self.list_rows, self.list_total

    async def get_organization_by_id(self, organization_id: str):
        """Return configured organization row."""
        del organization_id
        return self.organization

    async def update_organization(self, organization_id: str, update_data: dict):
        """Return configured update result."""
        self.last_update = (organization_id, update_data)
        return self.update_result

    async def get_organization_with_owner_for_impersonation(self, organization_id: str):
        """Return impersonation row."""
        del organization_id
        return self.impersonation_row


def _new_org_body() -> NewOrganizationBody:
    """Build minimal NewOrganizationBody for tests."""
    return NewOrganizationBody(
        company_data=CompanyData(
            company_name="New Org",
            primary_practice_areas=["General"],
        ),
    )


def _service(*, repo: _FakeOrgRepo | None = None) -> SuperadminOrganizationService:
    """Build service with fake org repo."""
    svc = SuperadminOrganizationService(db_connection=MagicMock())
    svc._org_repo = repo or _FakeOrgRepo()  # pylint: disable=protected-access
    return svc


def test_owner_full_name():
    """Owner name joins first/last; empty returns None."""
    assert (
        SuperadminOrganizationService._owner_full_name(  # pylint: disable=protected-access
            {"owner_first_name": "Jane", "owner_last_name": "Doe"}
        )
        == "Jane Doe"
    )
    assert SuperadminOrganizationService._owner_full_name({}) is None  # pylint: disable=protected-access


def test_map_list_row():
    """List row maps to SuperadminOrganizationListItem."""
    item = SuperadminOrganizationService._map_list_row(_list_row())  # pylint: disable=protected-access

    assert item.organization_id == ORG_ID
    assert item.admin.email == "owner@example.com"
    assert item.admin.full_name == "Jane Doe"
    assert item.status == SuperadminOrganizationListStatus.ACTIVE


@pytest.mark.asyncio
async def test_list_organizations_success():
    """list_organizations maps rows and pagination."""
    repo = _FakeOrgRepo(list_rows=[_list_row()], list_total=1)
    svc = _service(repo=repo)

    result = await svc.list_organizations(
        page=1,
        page_size=20,
        search="acme",
        plan=PlanType.TRIAL,
        status=SuperadminOrganizationListStatus.ACTIVE,
        sort=SuperadminOrganizationListSortField.NAME,
        order=SuperadminOrganizationListSortOrder.ASC,
    )

    assert result.total_count == 1
    assert result.items[0].name == "Acme Corp"
    assert result.message == "success.retrieved"
    assert repo.last_list_kwargs["search"] == "acme"


@pytest.mark.asyncio
async def test_list_organizations_empty_message():
    """Empty list returns no_data message."""
    repo = _FakeOrgRepo(list_rows=[], list_total=0)
    svc = _service(repo=repo)

    result = await svc.list_organizations(
        page=1,
        page_size=20,
        search=None,
        plan=None,
        status=None,
        sort=SuperadminOrganizationListSortField.CREATED_AT,
        order=SuperadminOrganizationListSortOrder.DESC,
    )

    assert result.message == "success.no_data"
    assert result.total_pages == 0


@pytest.mark.asyncio
async def test_create_organization_owner_not_found():
    """Missing owner raises NotFoundException."""
    svc = _service()
    mock_user_repo = MagicMock()
    mock_user_repo.get_user_details_by_id = AsyncMock(return_value=None)

    with patch(
        "apps.user_service.app.services.superadmin_organization_service.UserRepository",
        return_value=mock_user_repo,
    ):
        with pytest.raises(NotFoundException):
            await svc.create_organization(
                owner_user_id=OWNER_ID,
                body=_new_org_body(),
            )


@pytest.mark.asyncio
async def test_create_organization_delegates_to_org_service():
    """Valid owner delegates to OrganizationService.create_organization_for_owner."""
    svc = _service()
    mock_user_repo = MagicMock()
    mock_user_repo.get_user_details_by_id = AsyncMock(
        return_value={"id": OWNER_ID, "email": "owner@example.com"}
    )
    mock_org_service = MagicMock()
    mock_org_service.create_organization_for_owner = AsyncMock(return_value={"id": ORG_ID})

    with (
        patch(
            "apps.user_service.app.services.superadmin_organization_service.UserRepository",
            return_value=mock_user_repo,
        ),
        patch(
            "apps.user_service.app.services.superadmin_organization_service.OrganizationService",
            return_value=mock_org_service,
        ),
    ):
        result = await svc.create_organization(
            owner_user_id=OWNER_ID,
            body=_new_org_body(),
        )

    assert result["id"] == ORG_ID
    mock_org_service.create_organization_for_owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_suspend_organization_success():
    """suspend_organization updates status to suspended."""
    repo = _FakeOrgRepo(update_result={"id": ORG_ID})
    svc = _service(repo=repo)

    await svc.suspend_organization(ORG_ID)

    assert repo.last_update[1]["status"] == OrganizationStatus.SUSPENDED.value


@pytest.mark.asyncio
async def test_suspend_organization_not_found():
    """Missing org on suspend raises NotFoundException."""
    repo = _FakeOrgRepo(update_result={})
    svc = _service(repo=repo)

    with pytest.raises(NotFoundException):
        await svc.suspend_organization(ORG_ID)


@pytest.mark.asyncio
async def test_reactivate_organization_success():
    """reactivate_organization sets active when currently suspended."""
    repo = _FakeOrgRepo(
        organization={"id": ORG_ID, "status": OrganizationStatus.SUSPENDED.value},
        update_result={"id": ORG_ID},
    )
    svc = _service(repo=repo)

    await svc.reactivate_organization(ORG_ID)

    assert repo.last_update[1]["status"] == OrganizationStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_reactivate_organization_not_suspended():
    """Non-suspended org raises NotFoundException."""
    repo = _FakeOrgRepo(organization={"id": ORG_ID, "status": OrganizationStatus.ACTIVE.value})
    svc = _service(repo=repo)

    with pytest.raises(NotFoundException):
        await svc.reactivate_organization(ORG_ID)


@pytest.mark.asyncio
async def test_get_organization_detail_found():
    """get_organization_detail maps org row."""
    repo = _FakeOrgRepo(organization=_org_row())
    svc = _service(repo=repo)

    info = await svc.get_organization_detail(ORG_ID)

    assert info.name == "Acme Corp"


@pytest.mark.asyncio
async def test_get_organization_detail_not_found():
    """Missing org raises NotFoundException."""
    svc = _service(repo=_FakeOrgRepo(organization=None))

    with pytest.raises(NotFoundException):
        await svc.get_organization_detail(ORG_ID)


@pytest.mark.asyncio
async def test_impersonate_organization_owner_success():
    """Impersonation exchanges magic link and builds response."""
    repo = _FakeOrgRepo(
        impersonation_row={
            "id": ORG_ID,
            "name": "Acme",
            "owner_user_id": OWNER_ID,
            "owner_email": "owner@example.com",
        }
    )
    svc = _service(repo=repo)

    mock_session = MagicMock(
        access_token="tok",
        refresh_token="ref",
        expires_in=3600,
        token_type="bearer",
    )
    mock_verify = MagicMock(session=mock_session, user=MagicMock(id=OWNER_ID))
    mock_session_mgr = MagicMock()
    mock_session_mgr._extract_session_id = AsyncMock(return_value="sess-1")  # pylint: disable=protected-access
    mock_session_mgr.update_session_organization_context = AsyncMock()

    with (
        patch(
            "apps.user_service.app.services.superadmin_organization_service.generate_magiclink_and_exchange_for_session",
            new=AsyncMock(return_value=mock_verify),
        ),
        patch(
            "apps.user_service.app.services.superadmin_organization_service.SessionManagementService",
            return_value=mock_session_mgr,
        ),
        patch.object(
            svc,
            "_build_select_organization_response",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await svc.impersonate_organization_owner(
            organization_id=ORG_ID,
            supabase_admin_client=MagicMock(),
        )

    assert result.access_token == "tok"
    assert result.organization_id == ORG_ID


@pytest.mark.asyncio
async def test_impersonate_no_owner_email():
    """Missing owner email raises BadRequestException."""
    repo = _FakeOrgRepo(impersonation_row={"id": ORG_ID, "owner_email": ""})
    svc = _service(repo=repo)

    with pytest.raises(BadRequestException):
        await svc.impersonate_organization_owner(
            organization_id=ORG_ID,
            supabase_admin_client=MagicMock(),
        )


@pytest.mark.asyncio
async def test_exit_impersonation_superadmin_token_rejected():
    """Superadmin token cannot exit impersonation."""
    svc = _service()

    with patch(
        "apps.user_service.app.services.superadmin_organization_service.is_system_super_admin",
        new=AsyncMock(return_value=True),
    ):
        with pytest.raises(BadRequestException):
            await svc.exit_impersonation_session(current_user={"sub": OWNER_ID, "session_id": "s1"})


@pytest.mark.asyncio
async def test_exit_impersonation_success():
    """Valid owner token revokes session and clears cache."""
    svc = _service()
    mock_session_repo = MagicMock()
    mock_session_repo.delete_auth_session = AsyncMock(return_value={"id": "s1"})

    with (
        patch(
            "apps.user_service.app.services.superadmin_organization_service.is_system_super_admin",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "apps.user_service.app.services.superadmin_organization_service.SessionRepository",
            return_value=mock_session_repo,
        ),
        patch(
            "apps.user_service.app.services.superadmin_organization_service.invalidate_session_context_cache",
            new=AsyncMock(),
        ) as mock_invalidate,
    ):
        result = await svc.exit_impersonation_session(
            current_user={"sub": OWNER_ID, "session_id": "s1"}
        )

    assert result["id"] == "s1"
    mock_invalidate.assert_awaited_once_with("s1")
