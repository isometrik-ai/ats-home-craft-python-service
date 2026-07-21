"""Site map service: project location, overlays, and step completion."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.db.repositories.site_map_repository import SiteMapRepository
from apps.user_service.app.schemas.enums import ProjectSetupStep
from apps.user_service.app.schemas.project_inventory import (
    CreateSiteMapOverlaysRequest,
    UpdateProjectLocationRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class SiteMapService:
    """Business logic for the site map step."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.site_map_repo = SiteMapRepository(db_connection)
        self.projects_repo = ProjectsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def update_location(
        self, *, project_id: str, body: UpdateProjectLocationRequest
    ) -> dict[str, Any]:
        """Patch the project's latitude/longitude."""
        await self.setup_service.ensure_project(project_id=project_id)
        updated = await self.projects_repo.update_project(
            organization_id=self._org_id,
            project_id=project_id,
            update_data={"latitude": body.latitude, "longitude": body.longitude},
        )
        return serialize_row(updated or {})

    async def create_overlays(
        self, *, project_id: str, body: CreateSiteMapOverlaysRequest
    ) -> list[dict[str, Any]]:
        """Create site map overlay markers in bulk."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = [
            {
                **item.model_dump(),
                "organization_id": self._org_id,
                "project_id": project_id,
            }
            for item in body.items
        ]
        inserted = await self.site_map_repo.insert_overlays(rows)
        return [serialize_row(row) for row in inserted]

    async def list_overlays(self, *, project_id: str) -> list[dict[str, Any]]:
        """List overlays for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.site_map_repo.list_overlays(
            organization_id=self._org_id, project_id=project_id
        )
        return [serialize_row(row) for row in rows]

    async def delete_overlay(self, *, project_id: str, overlay_id: str) -> dict[str, Any]:
        """Delete an overlay."""
        await self.setup_service.ensure_project(project_id=project_id)
        deleted = await self.site_map_repo.delete_overlay(
            organization_id=self._org_id, project_id=project_id, overlay_id=overlay_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.overlay_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": overlay_id}, "new_data": None}

    async def complete_site_map(self, *, project_id: str) -> dict[str, Any]:
        """Mark the site_map step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.SITE_MAP.value,
        )
