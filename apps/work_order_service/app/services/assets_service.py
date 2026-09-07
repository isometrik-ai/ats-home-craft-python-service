"""Assets service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import EntityType
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.assets_repository import (
    AssetsRepository,
)


class AssetsService:
    """Business logic for assets."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = AssetsRepository(conn)

    def _scope(self, project_id: str) -> dict[str, str]:
        """Scope."""
        assert self.ctx.organization_id
        return {"organization_id": self.ctx.organization_id, "project_id": project_id}

    async def _validate_custom_fields(self, custom_fields: list | None) -> list:
        """Validate custom fields."""
        if not custom_fields:
            return []
        cfs = CustomFieldService(db_connection=self.conn, user_context=self.ctx)
        return await cfs.validate_for_create(custom_fields, EntityType.ASSET)

    async def list(
        self,
        *,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        category_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
            search=search,
            category_id=category_id,
        )

    async def get(self, *, project_id: str, entity_id: str) -> dict[str, Any] | None:
        """Get."""
        return await self.repo.get_by_id(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )

    async def create(self, *, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        if "custom_fields" in data:
            data = {
                **data,
                "custom_fields": await self._validate_custom_fields(data.get("custom_fields")),
            }
        payload = {**self._scope(project_id), **data}
        record = await self.repo.create(payload)
        return record

    async def update(
        self, *, project_id: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update."""
        if "custom_fields" in data and data["custom_fields"] is not None:
            cfs = CustomFieldService(db_connection=self.conn, user_context=self.ctx)
            existing = await self.get(project_id=project_id, entity_id=entity_id)
            merged = await cfs.merge_for_update(
                data.get("custom_fields"),
                existing.get("custom_fields") if existing else [],
                EntityType.ASSET,
            )
            data = {**data, "custom_fields": merged}
        payload = {**self._scope(project_id), **data}
        record = await self.repo.update(entity_id, payload)
        return record

    async def delete(self, *, project_id: str, entity_id: str) -> bool:
        """Delete."""
        ok = await self.repo.soft_delete(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )
        return ok
