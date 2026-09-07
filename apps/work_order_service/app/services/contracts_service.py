"""Maintenance contracts service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.contracts_repository import (
    ContractsRepository,
)
from apps.work_order_service.app.services.events_service import (
    EventsService,
    diff_records,
)
from apps.work_order_service.app.services.scheduler_service import SchedulerService


class ContractsService:
    """Business logic for contracts."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = ContractsRepository(conn)
        self.events = EventsService(conn)
        self.scheduler = SchedulerService(conn)

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
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
            status=status,
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
            entity="contract",
            action="created",
            record=record,
            actor_name=self.ctx.email,
            actor_user_id=self.ctx.user_id,
        )
        await self.scheduler.recompute_contract(record)
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
                entity="contract",
                action="updated",
                record=record,
                changes=diff_records(before, record),
                actor_name=self.ctx.email,
                actor_user_id=self.ctx.user_id,
            )
            terms_changed = any(
                before.get(k) != record.get(k)
                for k in (
                    "start_date",
                    "end_date",
                    "visit_frequency",
                    "last_serviced_date",
                    "auto_generate_lead_days",
                    "asset_ids",
                )
            )
            status_changed = before.get("status") != record.get("status")
            await self.scheduler.recompute_contract(
                record,
                terms_changed=terms_changed,
                status_changed=status_changed,
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
            before["record_status"] = "deleted"
            await self.scheduler.recompute_contract(before, status_changed=True)
            await self.events.record_and_dispatch(
                entity="contract",
                action="deleted",
                record=before,
                actor_name=self.ctx.email,
                actor_user_id=self.ctx.user_id,
            )
        return ok

    async def list_active_for_scheduler(self) -> list[dict[str, Any]]:
        """List active for scheduler."""
        return await self.repo.list_active_for_scheduler(self.ctx.organization_id)
