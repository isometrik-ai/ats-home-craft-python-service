"""Payments service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.payments_repository import (
    PaymentsRepository,
)
from apps.work_order_service.app.services.events_service import (
    EventsService,
    diff_records,
)


class PaymentsService:
    """Business logic for payments."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = PaymentsRepository(conn)
        self.events = EventsService(conn)

    def _scope(self, project_id: str) -> dict[str, str]:
        """Scope."""
        assert self.ctx.organization_id
        return {"organization_id": self.ctx.organization_id, "project_id": project_id}

    async def list(
        self,
        *,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        invoice_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
            invoice_id=invoice_id,
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
        await self.events.record_and_dispatch(
            entity="payment",
            action="created",
            record=record,
            actor_name=self.ctx.email,
            actor_user_id=self.ctx.user_id,
        )
        return record

    async def update(
        self, *, project_id: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update."""
        before = await self.get(project_id=project_id, entity_id=entity_id)
        payload = {**self._scope(project_id), **data}
        record = await self.repo.update(entity_id, payload)
        if record and before:
            await self.events.record_and_dispatch(
                entity="payment",
                action="updated",
                record=record,
                changes=diff_records(before, record),
                actor_name=self.ctx.email,
                actor_user_id=self.ctx.user_id,
            )
        return record

    async def delete(self, *, project_id: str, entity_id: str) -> bool:
        """Delete."""
        before = await self.get(project_id=project_id, entity_id=entity_id)
        ok = await self.repo.soft_delete(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )
        if ok and before:
            await self.events.record_and_dispatch(
                entity="payment",
                action="deleted",
                record=before,
                actor_name=self.ctx.email,
                actor_user_id=self.ctx.user_id,
            )
        return ok
