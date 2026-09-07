"""Vendor invoices service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.invoices_repository import (
    InvoicesRepository,
)
from apps.work_order_service.app.services.events_service import (
    EventsService,
    diff_records,
)


class InvoicesService:
    """Business logic for invoices."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext | None = None) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = InvoicesRepository(conn)
        self.events = EventsService(conn)

    def _scope(self, project_id: str) -> dict[str, str]:
        """Scope."""
        assert self.ctx and self.ctx.organization_id
        return {"organization_id": self.ctx.organization_id, "project_id": project_id}

    async def list(
        self,
        *,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        work_order_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        assert self.ctx and self.ctx.organization_id
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
            work_order_id=work_order_id,
            status=status,
        )

    async def get(self, *, project_id: str, entity_id: str) -> dict[str, Any] | None:
        """Get."""
        assert self.ctx and self.ctx.organization_id
        return await self.repo.get_by_id(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )

    async def create(
        self, *, project_id: str, data: dict[str, Any], source: str = "fm"
    ) -> dict[str, Any]:
        """Create."""
        if self.ctx and self.ctx.organization_id:
            payload = {**self._scope(project_id), **data}
        else:
            payload = data
        record = await self.repo.create(payload)
        await self.events.record_and_dispatch(
            entity="invoice",
            action="created",
            record=record,
            actor_name=self.ctx.email if self.ctx else None,
            actor_user_id=self.ctx.user_id if self.ctx else None,
            source=source,
        )
        from apps.work_order_service.app.adapters.notifications import (
            notify_invoice_event,
        )

        await notify_invoice_event(
            event="submitted" if source == "vendor_portal" else "created",
            organization_id=record["organization_id"],
            project_id=record["project_id"],
            invoice_id=record["id"],
            work_order_id=record.get("work_order_id"),
            user_id=self.ctx.user_id if self.ctx else None,
        )
        return record

    async def update(
        self,
        *,
        project_id: str,
        entity_id: str,
        data: dict[str, Any],
        source: str = "fm",
    ) -> dict[str, Any] | None:
        """Update."""
        before = await self.get(project_id=project_id, entity_id=entity_id)
        payload = {**self._scope(project_id), **data}
        record = await self.repo.update(entity_id, payload)
        if record and before:
            await self.events.record_and_dispatch(
                entity="invoice",
                action="updated",
                record=record,
                changes=diff_records(before, record),
                actor_name=self.ctx.email if self.ctx else None,
                actor_user_id=self.ctx.user_id if self.ctx else None,
                source=source,
            )
        return record

    async def delete(self, *, project_id: str, entity_id: str) -> bool:
        """Delete."""
        before = await self.get(project_id=project_id, entity_id=entity_id)
        assert self.ctx and self.ctx.organization_id
        ok = await self.repo.soft_delete(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )
        if ok and before:
            await self.events.record_and_dispatch(
                entity="invoice",
                action="deleted",
                record=before,
                actor_name=self.ctx.email,
                actor_user_id=self.ctx.user_id,
            )
        return ok

    async def append_timeline(
        self, *, project_id: str, entity_id: str, event: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Append timeline."""
        assert self.ctx and self.ctx.organization_id
        timeline = await self.repo.append_timeline(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            event={
                **event,
                "by": event.get("by") or self.ctx.email,
            },
        )
        return timeline
