"""Work orders service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import UserContext
from apps.work_order_service.app.db.repositories.work_orders_repository import (
    WorkOrderRepository,
)
from apps.work_order_service.app.schemas.enums import WorkOrderState
from apps.work_order_service.app.services.events_service import (
    EventsService,
    diff_records,
)
from apps.work_order_service.app.utils.tokens import generate_vendor_token

RECURRING_FIELDS = (
    "is_recurring",
    "recurring_frequency",
    "recurring_days",
    "scheduled_date",
    "recurring_end_date",
)


class WorkOrdersService:
    """Business logic for work orders."""

    def __init__(self, conn: asyncpg.Connection, user_context: UserContext | None = None) -> None:
        """init  ."""
        self.conn = conn
        self.ctx = user_context
        self.repo = WorkOrderRepository(conn)
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
        state: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        assert self.ctx and self.ctx.organization_id
        return await self.repo.list(
            organization_id=self.ctx.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
            state=state,
        )

    async def get(self, *, project_id: str, entity_id: str) -> dict[str, Any] | None:
        """Get."""
        assert self.ctx and self.ctx.organization_id
        return await self.repo.get_by_id(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )

    async def get_by_vendor_token(self, token_hash: str) -> dict[str, Any] | None:
        """Get by vendor token."""
        return await self.repo.get_by_vendor_token_hash(token_hash)

    async def create(self, *, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        payload = {**self._scope(project_id), **data}
        if not payload.get("vendor_token_hash"):
            raw, token_hash = generate_vendor_token()
            payload["vendor_token_hash"] = token_hash
            payload.setdefault(
                "timeline",
                [{"type": "created", "note": f"Vendor token issued: {raw}"}],
            )
        record = await self.repo.create(payload)
        await self.events.record_and_dispatch(
            entity="work_order",
            action="created",
            record=record,
            actor_name=self.ctx.email if self.ctx else None,
            actor_user_id=self.ctx.user_id if self.ctx else None,
        )
        from apps.work_order_service.app.adapters.notifications import (
            notify_work_order_event,
        )

        await notify_work_order_event(
            event="created",
            organization_id=record["organization_id"],
            project_id=record["project_id"],
            work_order_id=record["id"],
            title=record.get("title", ""),
        )
        return record

    async def update(
        self, *, project_id: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update."""
        payload_data = dict(data)
        payload_data.pop("timeline", None)
        if self.ctx and self.ctx.organization_id:
            payload = {**self._scope(project_id), **payload_data}
        else:
            payload = {
                "organization_id": payload_data.get("organization_id"),
                "project_id": project_id,
                **payload_data,
            }
        before = None
        if self.ctx and self.ctx.organization_id:
            before = await self.get(project_id=project_id, entity_id=entity_id)
        was_template = bool(before and before.get("is_recurring"))
        change_keys = set(payload_data.keys())
        recurring_changed = was_template and any(k in change_keys for k in RECURRING_FIELDS)
        terminating = _is_terminated(payload_data.get("state"))
        record = await self.repo.update(entity_id, payload)
        if record:
            if was_template and recurring_changed and record.get("is_recurring"):
                await self.repo.cancel_recurring_children(entity_id, "Recurring schedule changed")
            elif was_template and payload_data.get("is_recurring") is False:
                await self.repo.cancel_recurring_children(entity_id, "Recurring schedule disabled")
            elif was_template and terminating:
                await self.repo.cancel_recurring_children(
                    entity_id, "Cancelled — template terminated"
                )
        if record and before:
            await self.events.record_and_dispatch(
                entity="work_order",
                action="updated",
                record=record,
                changes=diff_records(before, record),
                actor_name=self.ctx.email if self.ctx else None,
                actor_user_id=self.ctx.user_id if self.ctx else None,
                source="vendor_portal" if not self.ctx else "fm",
            )
        elif record and not self.ctx:
            await self.events.record_and_dispatch(
                entity="work_order",
                action="updated",
                record=record,
                source="vendor_portal",
            )
        return record

    async def delete(self, *, project_id: str, entity_id: str) -> bool:
        """Delete."""
        before = await self.get(project_id=project_id, entity_id=entity_id)
        assert self.ctx and self.ctx.organization_id
        if before and before.get("is_recurring"):
            await self.repo.cancel_recurring_children(entity_id, "Cancelled — template deleted")
        ok = await self.repo.soft_delete(
            entity_id=entity_id,
            organization_id=self.ctx.organization_id,
            project_id=project_id,
        )
        if ok and before:
            await self.events.record_and_dispatch(
                entity="work_order",
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
        if timeline:
            record = await self.get(project_id=project_id, entity_id=entity_id)
            if record:
                await self.events.record_and_dispatch(
                    entity="work_order",
                    action="updated",
                    record=record,
                    actor_name=self.ctx.email,
                    actor_user_id=self.ctx.user_id,
                )
        return timeline


def _is_terminated(state: Any) -> bool:
    if state is None:
        return False
    if isinstance(state, WorkOrderState):
        return state == WorkOrderState.TERMINATED
    return str(state) == WorkOrderState.TERMINATED.value
