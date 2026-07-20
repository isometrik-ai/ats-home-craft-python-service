"""Visitor logs admin business logic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.db.repositories.visitor_logs_repository import (
    VisitorLogsRepository,
)
from apps.user_service.app.services.passes_service import PassesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class VisitorLogsService:
    """Admin-facing visitor logs operations."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.logs_repo = VisitorLogsRepository(db_connection)
        self.passes_repo = PassesRepository(db_connection)
        self.events_repo = PassEventsRepository(db_connection)
        self._passes_service = PassesService(
            db_connection=db_connection,
            user_context=user_context,
        )

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        """Parse a DB datetime value."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return None

    @classmethod
    def _time_spent_minutes(cls, in_time: Any, out_time: Any) -> int | None:
        """Derive visit duration in minutes."""
        start = cls._parse_dt(in_time)
        end = cls._parse_dt(out_time)
        if not start or not end or end < start:
            return None
        return int((end - start).total_seconds() // 60)

    def _normalize_list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a repository row to the visitor log list item shape."""
        from apps.user_service.app.utils.common_utils import format_iso_datetime

        in_time = row.get("in_time")
        out_time = row.get("out_time")
        created_by = (row.get("created_by") or "").strip() or None
        return {
            "pass_id": str(row["pass_id"]),
            "pass_type": row.get("pass_type"),
            "unit_label": row.get("unit_label"),
            "tower_name": row.get("tower_name"),
            "created_by": created_by,
            "scheduled_from": format_iso_datetime(row.get("scheduled_from")),
            "scheduled_until": format_iso_datetime(row.get("scheduled_until")),
            "entry_method": row.get("entry_method"),
            "guard_name": row.get("guard_name"),
            "access_status": row.get("access_status"),
            "in_time": format_iso_datetime(in_time),
            "out_time": format_iso_datetime(out_time),
            "time_spent_minutes": self._time_spent_minutes(in_time, out_time),
        }

    async def list_logs(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        search: str | None = None,
        pass_type: str | None = None,
        entry_method: str | None = None,
        access_status: str | None = None,
        tower_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated visitor log rows."""
        org_id = self.user_context.organization_id
        assert org_id
        rows, total = await self.logs_repo.list_logs(
            organization_id=org_id,
            start_at=start_at,
            end_at=end_at,
            search=search,
            pass_type=pass_type,
            entry_method=entry_method,
            access_status=access_status,
            tower_id=tower_id,
            page=page,
            page_size=page_size,
        )
        return [self._normalize_list_item(row) for row in rows], total

    async def get_overview(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Return overview card metrics."""
        from apps.user_service.app.utils.common_utils import format_iso_datetime

        org_id = self.user_context.organization_id
        assert org_id
        result = await self.logs_repo.get_overview(
            organization_id=org_id,
            start_at=start_at,
            end_at=end_at,
        )
        return {
            **result,
            "start_at": format_iso_datetime(result.get("start_at")),
            "end_at": format_iso_datetime(result.get("end_at")),
        }

    async def get_log_detail(self, *, pass_id: str) -> dict[str, Any]:
        """Return pass detail with full timeline for admin."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.passes_repo.get_by_id(
            organization_id=org_id,
            pass_id=pass_id,
        )
        if not row:
            raise NotFoundException(
                message_key="visitor_logs.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        event_rows = await self.events_repo.list_by_pass(
            organization_id=org_id,
            pass_id=pass_id,
        )
        events = [self._passes_service._normalize_event(event_row) for event_row in event_rows]
        return self._passes_service._normalize_pass(row, events=events, include_events=True)
