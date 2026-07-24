"""Unit tests for ProjectsService helpers and CRUD."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.enums import (
    MeasurementUnit,
    ProjectMediaKind,
    PropertyProjectStatus,
    PropertyType,
)
from apps.user_service.app.schemas.project_setup import (
    CreateProjectRequest,
    ProjectMediaRequest,
    UpdateProjectRequest,
)
from apps.user_service.app.services.projects_service import ProjectsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "660e8400-e29b-41d4-a716-446655440001"
ADMIN_ID = "770e8400-e29b-41d4-a716-446655440002"
PROJECT_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ctx() -> UserContext:
    """Build user context for project tests."""
    return UserContext(user_id=USER_ID, email="admin@example.com", organization_id=ORG_ID)


class _FakeProjectsRepo:
    """Minimal fake ProjectsRepository."""

    def __init__(
        self,
        *,
        existing_codes: set[str] | None = None,
        project: dict[str, Any] | None = None,
        projects: list[dict[str, Any]] | None = None,
        media: list[dict[str, Any]] | None = None,
    ):
        self.existing_codes = existing_codes or set()
        self.project = project
        self.projects = projects or []
        self.media = media or []
        self.inserted_project: dict[str, Any] | None = None
        self.deleted_project_id: str | None = None
        self.upsert_calls: list[dict[str, Any]] = []

    async def project_code_exists(self, *, organization_id: str, code: str) -> bool:
        """Return whether the code is already taken in the org."""
        del organization_id
        return code in self.existing_codes

    async def insert_project(self, data: dict[str, Any]) -> dict[str, Any]:
        """Record inserted project."""
        self.inserted_project = data
        return data

    async def get_project(self, *, organization_id: str, project_id: str) -> dict[str, Any] | None:
        """Return configured project row."""
        del organization_id, project_id
        return self.project

    async def update_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply patch to configured project."""
        del organization_id, project_id
        if self.project:
            self.project = {**self.project, **update_data}
        return self.project or {}

    async def list_projects(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        """Return paginated projects."""
        del kwargs
        return self.projects, len(self.projects)

    async def list_projects_for_member(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        """Return paginated member projects."""
        del kwargs
        return self.projects, len(self.projects)

    async def delete_project(self, *, organization_id: str, project_id: str) -> None:
        """Record deletion."""
        del organization_id
        self.deleted_project_id = project_id

    async def upsert_member(self, **kwargs) -> None:
        """Record member upsert."""
        self.upsert_calls.append(kwargs)

    async def insert_media(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert media row."""
        row = {"id": "media-1", **data}
        self.media.append(row)
        return row

    async def list_media(self, **kwargs) -> list[dict[str, Any]]:
        """Return media rows."""
        del kwargs
        return self.media

    async def get_media(self, **kwargs) -> dict[str, Any] | None:
        """Return one media row."""
        del kwargs
        return self.media[0] if self.media else None

    async def delete_media(self, **kwargs) -> None:
        """Clear media list."""
        del kwargs
        self.media = []


def _service(
    *,
    projects_repo: _FakeProjectsRepo | None = None,
    setup_service: AsyncMock | None = None,
) -> ProjectsService:
    """Build ProjectsService with fake repos."""
    service = ProjectsService.__new__(ProjectsService)
    service.db_connection = MagicMock()
    service.user_context = _ctx()
    service.projects_repo = projects_repo or _FakeProjectsRepo()
    service.setup_service = setup_service or AsyncMock()
    return service


def _project_row(**overrides) -> dict[str, Any]:
    """Build a full project row."""
    row = {
        "id": PROJECT_ID,
        "organization_id": ORG_ID,
        "code": "sunrise-towers",
        "name": "Sunrise Towers",
        "developer_name": "Dev Co",
        "city": "Mumbai",
        "state": "MH",
        "status": PropertyProjectStatus.ACTIVE.value,
        "property_types": [PropertyType.RESIDENTIAL.value],
        "primary_measurement_unit": MeasurementUnit.SQ_FT.value,
        "units_count": 10,
        "setup_current_step": "project_basics",
        "latitude": Decimal("19.0760"),
        "longitude": Decimal("72.8777"),
        "possession_date": date(2026, 12, 1),
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _create_body(**overrides) -> CreateProjectRequest:
    """Build CreateProjectRequest with defaults."""
    defaults = {
        "name": "Sunrise Towers",
        "developer_name": "Dev Co",
        "community_admin_user_id": ADMIN_ID,
        "gstin": "22AAAAA0000A1Z5",
        "address_line_1": "123 Main St",
        "pin_code": "400001",
        "city": "Mumbai",
        "state": "MH",
        "country": "IN",
        "property_types": [PropertyType.RESIDENTIAL],
        "primary_measurement_unit": MeasurementUnit.SQ_FT,
    }
    defaults.update(overrides)
    return CreateProjectRequest(**defaults)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Green Valley Phase 1", "green-valley-phase-1"),
        ("  Sunrise Towers!!!  ", "sunrise-towers"),
        ("---", ""),
        ("Project #42", "project-42"),
    ],
)
def test_slugify_project_name(name: str, expected: str):
    """Slugify normalizes names into URL-friendly project codes."""
    assert ProjectsService._slugify_project_name(name) == expected


def test_normalize_details_serializes_row():
    """Details serializer converts ids, dates, and decimals."""
    payload = ProjectsService._normalize_details(_project_row())
    assert payload["id"] == PROJECT_ID
    assert payload["latitude"] == 19.0760
    assert payload["possession_date"] == "2026-12-01"


def test_summary_from_row_includes_counts():
    """Summary serializer exposes list fields."""
    summary = ProjectsService._summary_from_row(_project_row())
    assert summary["code"] == "sunrise-towers"
    assert summary["units_count"] == 10


def test_normalize_media_row():
    """Media serializer stringifies ids and timestamps."""
    media = ProjectsService._normalize_media(
        {
            "id": "media-1",
            "project_id": PROJECT_ID,
            "kind": ProjectMediaKind.COVER_IMAGE.value,
            "path": "/files/cover.jpg",
            "mime": "image/jpeg",
            "size_bytes": 1024,
            "original_name": "cover.jpg",
            "sort_order": 0,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )
    assert media["kind"] == ProjectMediaKind.COVER_IMAGE.value
    assert media["size_bytes"] == 1024


@pytest.mark.asyncio
async def test_resolve_code_explicit():
    """Provided code is returned unchanged."""
    service = _service(projects_repo=_FakeProjectsRepo(existing_codes={"custom-code"}))
    resolved = await service._resolve_project_code(
        organization_id=ORG_ID,
        name="Ignored Name",
        code="custom-code",
    )
    assert resolved == "custom-code"


@pytest.mark.asyncio
async def test_resolve_code_from_name():
    """Missing code is generated from the project name."""
    service = _service()
    resolved = await service._resolve_project_code(
        organization_id=ORG_ID,
        name="Sunrise Towers",
        code=None,
    )
    assert resolved == "sunrise-towers"


@pytest.mark.asyncio
async def test_resolve_code_suffix_on_conflict():
    """Generated code gets a numeric suffix when the base slug exists."""
    service = _service(projects_repo=_FakeProjectsRepo(existing_codes={"sunrise-towers"}))
    resolved = await service._resolve_project_code(
        organization_id=ORG_ID,
        name="Sunrise Towers",
        code=None,
    )
    assert resolved == "sunrise-towers-2"


@pytest.mark.asyncio
async def test_get_project_details_found():
    """Get project returns normalized details."""
    service = _service(
        projects_repo=_FakeProjectsRepo(
            project=_project_row(
                community_admin_user_id=ADMIN_ID,
                community_admin_email="admin@example.com",
                community_admin_phone_number="9876543210",
                community_admin_phone_isd_code="+91",
                community_admin_first_name="Jane",
                community_admin_last_name="Admin",
                community_admin_salutation="Ms.",
            )
        )
    )
    result = await service.get_project_details(project_id=PROJECT_ID)
    assert result["name"] == "Sunrise Towers"
    assert result["community_admin"]["user_id"] == ADMIN_ID
    assert result["community_admin"]["email"] == "admin@example.com"
    assert result["community_admin"]["display_name"] == "Ms. Jane Admin"
    assert "community_admin_email" not in result


@pytest.mark.asyncio
async def test_get_project_details_not_found():
    """Missing project raises not found."""
    service = _service()
    with pytest.raises(NotFoundException):
        await service.get_project_details(project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_list_projects_returns_summaries():
    """List projects serializes summary rows."""
    service = _service(projects_repo=_FakeProjectsRepo(projects=[_project_row()]))
    result = await service.list_projects(
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    )
    assert result["total"] == 1
    assert result["items"][0]["code"] == "sunrise-towers"


@pytest.mark.asyncio
async def test_list_my_projects_requires_session():
    """My projects rejects missing org or user."""
    service = _service()
    service.user_context = UserContext(user_id=None, email="x@y.com", organization_id=None)
    with pytest.raises(ValidationException):
        await service.list_my_projects(
            search=None,
            status=None,
            property_type=None,
            page=1,
            page_size=20,
        )


@pytest.mark.asyncio
async def test_list_my_projects_includes_role():
    """My projects adds role from member row."""
    row = _project_row(role="community_admin")
    service = _service(projects_repo=_FakeProjectsRepo(projects=[row]))
    result = await service.list_my_projects(
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    )
    assert result["items"][0]["role"] == "community_admin"


@pytest.mark.asyncio
async def test_delete_project_success():
    """Delete returns old_data snapshot."""
    repo = _FakeProjectsRepo(project=_project_row())
    service = _service(projects_repo=repo)
    result = await service.delete_project(project_id=PROJECT_ID)
    assert result["old_data"]["id"] == PROJECT_ID
    assert repo.deleted_project_id == PROJECT_ID


@pytest.mark.asyncio
async def test_create_project_success(monkeypatch):
    """Create inserts project, syncs steps, and registers members."""
    repo = _FakeProjectsRepo(project=_project_row())
    service = _service(projects_repo=repo)
    org_member_repo = MagicMock()
    org_member_repo.check_user_membership_by_user_id = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.OrganizationMemberRepository",
        lambda db_connection: org_member_repo,
    )

    result = await service.create_project(_create_body())

    assert result["project_id"]
    assert repo.inserted_project is not None
    assert len(repo.upsert_calls) == 2
    service.setup_service.sync_steps_for_property_types.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_project_not_found():
    """Update raises when project is missing."""
    service = _service()
    body = UpdateProjectRequest(name="Renamed")
    with pytest.raises(NotFoundException):
        await service.update_project(project_id=PROJECT_ID, body=body)


@pytest.mark.asyncio
async def test_update_project_patches_row(monkeypatch):
    """Update applies patch and returns old/new snapshots."""
    repo = _FakeProjectsRepo(project=_project_row())
    service = _service(projects_repo=repo)
    org_member_repo = MagicMock()
    org_member_repo.check_user_membership_by_user_id = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.OrganizationMemberRepository",
        lambda db_connection: org_member_repo,
    )

    body = UpdateProjectRequest(
        name="Renamed Towers",
        community_admin_user_id=ADMIN_ID,
    )
    result = await service.update_project(project_id=PROJECT_ID, body=body)

    assert result["old_data"]["name"] == "Sunrise Towers"
    assert result["new_data"]["name"] == "Renamed Towers"


@pytest.mark.asyncio
async def test_add_and_list_media():
    """Media CRUD stores and lists normalized rows."""
    service = _service()
    body = ProjectMediaRequest(
        kind=ProjectMediaKind.LOGO,
        path="/logo.png",
        mime="image/png",
        size_bytes=512,
        original_name="logo.png",
        sort_order=1,
    )
    added = await service.add_media(project_id=PROJECT_ID, body=body)
    listed = await service.list_media(project_id=PROJECT_ID)

    assert added["path"] == "/logo.png"
    assert len(listed) == 1
    service.setup_service.ensure_project.assert_awaited()


@pytest.mark.asyncio
async def test_remove_media_not_found():
    """Remove media raises when row is missing."""
    service = _service()
    with pytest.raises(NotFoundException):
        await service.remove_media(project_id=PROJECT_ID, media_id="missing")


@pytest.mark.asyncio
async def test_create_project_duplicate_code(monkeypatch):
    """Unique violation on insert raises conflict."""
    repo = _FakeProjectsRepo()

    async def _raise_unique(data: dict[str, Any]) -> dict[str, Any]:
        del data
        raise UniqueViolationError("duplicate")

    repo.insert_project = _raise_unique  # type: ignore[method-assign]
    service = _service(projects_repo=repo)
    org_member_repo = MagicMock()
    org_member_repo.check_user_membership_by_user_id = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "apps.user_service.app.services.projects_service.OrganizationMemberRepository",
        lambda db_connection: org_member_repo,
    )

    from libs.shared_utils.http_exceptions import ConflictException

    with pytest.raises(ConflictException):
        await service.create_project(_create_body())
