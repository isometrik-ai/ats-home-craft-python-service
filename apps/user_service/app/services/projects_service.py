"""Projects service: CRUD, media, and members for project setup."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.schemas.project_setup import (
    CreateProjectRequest,
    ProjectMediaRequest,
    UpdateProjectRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


def _to_float(value: Any) -> float | None:
    """Coerce Decimal/None to float for API responses."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


class ProjectsService:
    """Business logic for projects and their media/members."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.projects_repo = ProjectsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @staticmethod
    def _normalize_details(row: dict[str, Any]) -> dict[str, Any]:
        """Serialize a full project row for the API response."""
        out = dict(row)
        for key in ("id", "organization_id", "community_admin_user_id"):
            if out.get(key) is not None:
                out[key] = str(out[key])
        out["property_types"] = [str(pt) for pt in (out.get("property_types") or [])]
        out["primary_measurement_unit"] = str(out.get("primary_measurement_unit"))
        out["status"] = str(out.get("status"))
        out["setup_current_step"] = str(out.get("setup_current_step"))
        out["latitude"] = _to_float(out.get("latitude"))
        out["longitude"] = _to_float(out.get("longitude"))
        if isinstance(out.get("possession_date"), date):
            out["possession_date"] = out["possession_date"].isoformat()
        for ts_key in ("created_at", "updated_at"):
            out[ts_key] = format_iso_datetime(out.get(ts_key))
        return out

    @staticmethod
    def _summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
        """Serialize a project list row."""
        return {
            "id": str(row["id"]),
            "organization_id": str(row["organization_id"]),
            "code": row.get("code"),
            "name": row.get("name"),
            "developer_name": row.get("developer_name"),
            "city": row.get("city"),
            "state": row.get("state"),
            "status": str(row.get("status")),
            "property_types": [str(pt) for pt in (row.get("property_types") or [])],
            "primary_measurement_unit": str(row.get("primary_measurement_unit")),
            "units_count": int(row.get("units_count") or 0),
            "setup_current_step": str(row.get("setup_current_step")),
            "created_at": format_iso_datetime(row.get("created_at")),
            "updated_at": format_iso_datetime(row.get("updated_at")),
        }

    @staticmethod
    def _normalize_media(row: dict[str, Any]) -> dict[str, Any]:
        """Serialize a project_media row."""
        return {
            "id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "kind": str(row.get("kind")),
            "path": row.get("path"),
            "mime": row.get("mime"),
            "size_bytes": int(row.get("size_bytes") or 0),
            "original_name": row.get("original_name"),
            "sort_order": int(row.get("sort_order") or 0),
            "created_at": format_iso_datetime(row.get("created_at")),
        }

    @staticmethod
    def _my_project_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
        """Serialize an assigned-project list row."""
        summary = ProjectsService._summary_from_row(row)
        summary["role"] = str(row.get("role") or "")
        summary["community_admin_email"] = row.get("community_admin_email")
        summary["community_admin_phone_number"] = row.get("community_admin_phone_number")
        summary["community_admin_phone_isd_code"] = row.get("community_admin_phone_isd_code")
        return summary

    async def _ensure_community_admin_is_org_member(self, *, user_id: str) -> None:
        """Require the selected community admin to be an active org member."""
        org_id = self.user_context.organization_id
        assert org_id
        is_member = await OrganizationMemberRepository(
            db_connection=self.db_connection
        ).check_user_membership_by_user_id(
            user_id=user_id,
            organization_id=org_id,
            disallow_suspended=True,
        )
        if not is_member:
            raise ValidationException(
                message_key="project_setup.errors.community_admin_not_org_member",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def create_project(self, body: CreateProjectRequest) -> dict[str, Any]:
        """Create a project, seed setup steps, and register the creator."""
        org_id = self.user_context.organization_id
        community_admin_user_id = body.community_admin_user_id
        await self._ensure_community_admin_is_org_member(user_id=community_admin_user_id)
        project_id = str(uuid.uuid4())
        property_types = [pt.value for pt in body.property_types]
        row_data = {
            "id": project_id,
            "organization_id": org_id,
            "code": body.code,
            "name": body.name,
            "developer_name": body.developer_name,
            "community_admin_user_id": community_admin_user_id,
            "gstin": body.gstin,
            "possession_date": body.possession_date,
            "address_line_1": body.address_line_1,
            "address_line_2": body.address_line_2,
            "pin_code": body.pin_code,
            "city": body.city,
            "state": body.state,
            "country": body.country,
            "latitude": body.latitude,
            "longitude": body.longitude,
            "property_types": property_types,
            "primary_measurement_unit": body.primary_measurement_unit.value,
            "units_count": body.units_count or 0,
            "created_by": self.user_context.user_id,
            "updated_by": self.user_context.user_id,
        }
        try:
            inserted = await self.projects_repo.insert_project(row_data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc

        await self.setup_service.sync_steps_for_property_types(
            project_id=str(inserted["id"]),
            property_types=property_types,
        )
        if self.user_context.user_id:
            await self.projects_repo.upsert_member(
                organization_id=org_id,
                project_id=str(inserted["id"]),
                user_id=self.user_context.user_id,
            )
        await self.projects_repo.upsert_member(
            organization_id=org_id,
            project_id=str(inserted["id"]),
            user_id=community_admin_user_id,
            role="community_admin",
        )
        refreshed = await self.projects_repo.get_project(
            organization_id=org_id, project_id=str(inserted["id"])
        )
        details = self._normalize_details(refreshed or inserted)
        return {
            "project_id": str(inserted["id"]),
            "old_data": None,
            "new_data": details,
        }

    async def update_project(
        self, *, project_id: str, body: UpdateProjectRequest
    ) -> dict[str, Any]:
        """Patch a project; re-seed steps if property_types changed."""
        org_id = self.user_context.organization_id
        current = await self.projects_repo.get_project(
            organization_id=org_id, project_id=project_id
        )
        if not current:
            raise NotFoundException(
                message_key="project_setup.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        property_types_changed = False
        if "property_types" in patch and body.property_types is not None:
            patch["property_types"] = [pt.value for pt in body.property_types]
            property_types_changed = True
        if "primary_measurement_unit" in patch and body.primary_measurement_unit:
            patch["primary_measurement_unit"] = body.primary_measurement_unit.value
        if "status" in patch and body.status:
            patch["status"] = body.status.value

        if "community_admin_user_id" in patch and patch["community_admin_user_id"]:
            await self._ensure_community_admin_is_org_member(
                user_id=str(patch["community_admin_user_id"])
            )

        try:
            updated = await self.projects_repo.update_project(
                organization_id=org_id,
                project_id=project_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc

        if property_types_changed:
            await self.setup_service.sync_steps_for_property_types(
                project_id=project_id,
                property_types=patch["property_types"],
            )
            updated = await self.projects_repo.get_project(
                organization_id=org_id, project_id=project_id
            )
        if "community_admin_user_id" in patch and patch["community_admin_user_id"]:
            await self.projects_repo.upsert_member(
                organization_id=org_id,
                project_id=project_id,
                user_id=str(patch["community_admin_user_id"]),
                role="community_admin",
            )
        return {
            "old_data": current,
            "new_data": self._normalize_details(updated or current),
        }

    async def get_project_details(self, *, project_id: str) -> dict[str, Any]:
        """Fetch a single project."""
        row = await self.projects_repo.get_project(
            organization_id=self.user_context.organization_id,
            project_id=project_id,
        )
        if not row:
            raise NotFoundException(
                message_key="project_setup.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_details(row)

    async def list_projects(
        self,
        *,
        search: str | None,
        status: str | None,
        property_type: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """List projects with pagination."""
        rows, total = await self.projects_repo.list_projects(
            organization_id=self.user_context.organization_id,
            search=search,
            status=status,
            property_type=property_type,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._summary_from_row(row) for row in rows],
            "total": total,
        }

    async def list_my_projects(
        self,
        *,
        search: str | None,
        status: str | None,
        property_type: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """List projects assigned to the current user in the active organization."""
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        if not org_id or not user_id:
            raise ValidationException(
                message_key="auth.errors.session_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        rows, total = await self.projects_repo.list_projects_for_member(
            organization_id=org_id,
            user_id=user_id,
            search=search,
            status=status,
            property_type=property_type,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._my_project_summary_from_row(row) for row in rows],
            "total": total,
        }

    async def delete_project(self, *, project_id: str) -> dict[str, Any]:
        """Hard-delete a project."""
        org_id = self.user_context.organization_id
        current = await self.projects_repo.get_project(
            organization_id=org_id, project_id=project_id
        )
        if not current:
            raise NotFoundException(
                message_key="project_setup.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.projects_repo.delete_project(organization_id=org_id, project_id=project_id)
        return {"old_data": current, "new_data": None}

    # -- media --------------------------------------------------------------

    async def add_media(self, *, project_id: str, body: ProjectMediaRequest) -> dict[str, Any]:
        """Attach media metadata to a project (stored as-is)."""
        await self.setup_service.ensure_project(project_id=project_id)
        inserted = await self.projects_repo.insert_media(
            {
                "organization_id": self.user_context.organization_id,
                "project_id": project_id,
                "kind": body.kind.value,
                "path": body.path,
                "mime": body.mime,
                "size_bytes": body.size_bytes,
                "original_name": body.original_name,
                "sort_order": body.sort_order,
                "uploaded_by": self.user_context.user_id,
            }
        )
        return self._normalize_media(inserted)

    async def list_media(self, *, project_id: str) -> list[dict[str, Any]]:
        """List a project's media."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.projects_repo.list_media(
            organization_id=self.user_context.organization_id,
            project_id=project_id,
        )
        return [self._normalize_media(row) for row in rows]

    async def remove_media(self, *, project_id: str, media_id: str) -> dict[str, Any]:
        """Delete a media row."""
        org_id = self.user_context.organization_id
        existing = await self.projects_repo.get_media(
            organization_id=org_id, project_id=project_id, media_id=media_id
        )
        if not existing:
            raise NotFoundException(
                message_key="project_setup.errors.media_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.projects_repo.delete_media(
            organization_id=org_id, project_id=project_id, media_id=media_id
        )
        return {"old_data": existing, "new_data": None}
