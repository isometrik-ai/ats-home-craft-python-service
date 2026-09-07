"""Form templates service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.form_templates_repository import (
    FormTemplatesRepository,
)


class FormTemplatesService:
    """Business logic for form templates."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = FormTemplatesRepository(conn)

    async def list(self, *, project_id: str, page: int = 1, page_size: int = 50):
        """List."""
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
        )

    async def get(self, *, project_id: str, entity_id: str):
        """Get."""
        return await self.repo.get_by_id(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )

    async def create(self, *, project_id: str, data: dict[str, Any]):
        """Create."""
        payload = {
            "organization_id": self.ctx.organization_id,
            "project_id": project_id,
            **data,
        }
        return await self.repo.create(payload)

    async def update(self, *, project_id: str, entity_id: str, data: dict[str, Any]):
        """Update."""
        payload = {
            "organization_id": self.ctx.organization_id,
            "project_id": project_id,
            **data,
        }
        return await self.repo.update(entity_id, payload)

    async def delete(self, *, project_id: str, entity_id: str) -> bool:
        """Delete."""
        return await self.repo.soft_delete(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )
