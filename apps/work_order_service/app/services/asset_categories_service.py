"""Asset categories service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.asset_categories_repository import (
    AssetCategoryRepository,
)


class AssetCategoriesService:
    """Business logic for asset categories."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = AssetCategoryRepository(conn)

    def _scope(self, project_id: str) -> dict[str, str]:
        """Scope."""
        assert self.ctx.organization_id
        return {"organization_id": self.ctx.organization_id, "project_id": project_id}

    async def list(
        self, *, project_id: str, page: int = 1, page_size: int = 50, search: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
            search=search,
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
        payload = {**self._scope(project_id), **data}
        record = await self.repo.create(payload)
        return record

    async def update(
        self, *, project_id: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update."""
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
