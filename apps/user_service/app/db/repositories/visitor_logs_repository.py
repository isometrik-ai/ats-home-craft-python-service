"""Visitor logs admin query persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import PassEventType, PassType


class VisitorLogsRepository(BaseRepository):
    """Database operations for admin visitor logs views."""

    @staticmethod
    def _current_month_bounds() -> tuple[datetime, datetime]:
        """Return UTC bounds for the current calendar month."""
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        if now.month == 12:
            end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        return start, end

    @classmethod
    def _resolve_range(
        cls,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> tuple[datetime, datetime]:
        """Resolve query bounds; default to the current month when omitted."""
        if start_at is None and end_at is None:
            return cls._current_month_bounds()
        if start_at is None or end_at is None:
            raise ValueError("start_at and end_at must be provided together")
        start = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
        end = end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)
        if end <= start:
            raise ValueError("end_at must be after start_at")
        return start, end

    def _build_filters(
        self,
        *,
        search: str | None,
        pass_type: str | None,
        entry_method: str | None,
        access_status: str | None,
        tower_id: str | None,
        param_index: int,
    ) -> tuple[str, list[Any]]:
        """Build dynamic WHERE fragments for visitor log list queries."""
        clauses: list[str] = []
        args: list[Any] = []
        idx = param_index

        if search:
            clauses.append(
                f"("
                f"  p.guest_name ILIKE ${idx}"
                f"  OR COALESCE(u.unit_label, '') ILIKE ${idx}"
                f"  OR COALESCE(p.guest_phone_number, '') ILIKE ${idx}"
                f")"
            )
            args.append(f"%{search}%")
            idx += 1

        if pass_type:
            clauses.append(f"p.pass_type = ${idx}::pass_type")
            args.append(pass_type)
            idx += 1

        if entry_method:
            clauses.append(f"ci.entry_method = ${idx}::pass_entry_method")
            args.append(entry_method)
            idx += 1

        if access_status:
            clauses.append(f"ci.access_status = ${idx}::pass_access_status")
            args.append(access_status)
            idx += 1

        if tower_id:
            clauses.append(f"u.tower_id = ${idx}::uuid")
            args.append(tower_id)
            idx += 1

        if not clauses:
            return "", args
        return " AND " + " AND ".join(clauses), args

    async def list_logs(
        self,
        *,
        organization_id: str,
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
        """List visitor log rows for the admin table."""
        range_start, range_end = self._resolve_range(start_at=start_at, end_at=end_at)
        args: list[Any] = [organization_id, range_start, range_end]
        filter_sql, filter_args = self._build_filters(
            search=search,
            pass_type=pass_type,
            entry_method=entry_method,
            access_status=access_status,
            tower_id=tower_id,
            param_index=4,
        )
        args.extend(filter_args)
        offset = (page - 1) * page_size
        limit_idx = len(args) + 1
        offset_idx = len(args) + 2
        args.extend([page_size, offset])

        base_from = f"""
            FROM passes p
            JOIN units u ON u.id = p.unit_id
            LEFT JOIN towers t ON t.id = u.tower_id
            JOIN contacts creator ON creator.id = p.created_by_contact_id
            LEFT JOIN LATERAL (
                SELECT
                  pe.occurred_at,
                  pe.entry_method,
                  pe.access_status,
                  pe.actor_label
                FROM pass_events pe
                WHERE pe.organization_id = p.organization_id
                  AND pe.pass_id = p.id
                  AND pe.event_type = '{PassEventType.CHECKED_IN.value}'::pass_event_type
                ORDER BY pe.occurred_at DESC, pe.created_at DESC
                LIMIT 1
            ) ci ON true
            LEFT JOIN LATERAL (
                SELECT pe.occurred_at
                FROM pass_events pe
                WHERE pe.organization_id = p.organization_id
                  AND pe.pass_id = p.id
                  AND pe.event_type = '{PassEventType.CHECKED_OUT.value}'::pass_event_type
                ORDER BY pe.occurred_at DESC, pe.created_at DESC
                LIMIT 1
            ) co ON true
            WHERE p.organization_id = $1::uuid
              AND p.valid_from >= $2
              AND p.valid_from < $3
              {filter_sql}
        """

        count = await self.db_connection.fetchval(
            f"SELECT COUNT(*) {base_from}",
            *args[:-2],
        )

        rows = await self.db_connection.fetch(
            f"""
            SELECT
              p.id::text AS pass_id,
              p.pass_type::text AS pass_type,
              u.unit_label,
              t.name AS tower_name,
              TRIM(
                COALESCE(creator.first_name, '') || ' ' || COALESCE(creator.last_name, '')
              ) AS created_by,
              p.valid_from AS scheduled_from,
              p.valid_until AS scheduled_until,
              ci.entry_method::text AS entry_method,
              ci.actor_label AS guard_name,
              ci.access_status::text AS access_status,
              ci.occurred_at AS in_time,
              co.occurred_at AS out_time
            {base_from}
            ORDER BY COALESCE(ci.occurred_at, p.valid_from) DESC, p.created_at DESC
            LIMIT ${limit_idx} OFFSET ${offset_idx}
            """,
            *args,
        )
        return [dict(row) for row in rows], int(count or 0)

    async def get_overview(
        self,
        *,
        organization_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate overview card metrics for a date range."""
        range_start, range_end = self._resolve_range(start_at=start_at, end_at=end_at)

        row = await self.db_connection.fetchrow(
            """
            SELECT
              (
                SELECT COUNT(*)
                FROM passes p
                WHERE p.organization_id = $1::uuid
                  AND p.valid_from >= $2
                  AND p.valid_from < $3
              ) AS total_visitors,
              (
                SELECT COUNT(*)
                FROM pass_events pe
                WHERE pe.organization_id = $1::uuid
                  AND pe.event_type = $4::pass_event_type
                  AND pe.occurred_at >= $2
                  AND pe.occurred_at < $3
              ) AS in_count,
              (
                SELECT COUNT(*)
                FROM passes p
                WHERE p.organization_id = $1::uuid
                  AND p.valid_from >= $2
                  AND p.valid_from < $3
                  AND p.pass_type = $5::pass_type
              ) AS deliveries,
              (
                SELECT COUNT(*)
                FROM passes p
                WHERE p.organization_id = $1::uuid
                  AND p.valid_from >= $2
                  AND p.valid_from < $3
                  AND p.pass_type = $6::pass_type
              ) AS daily_help
            """,
            organization_id,
            range_start,
            range_end,
            PassEventType.CHECKED_IN.value,
            PassType.DELIVERY.value,
            PassType.SERVICE.value,
        )
        payload = dict(row) if row else {}
        return {
            "start_at": range_start,
            "end_at": range_end,
            "total_visitors": int(payload.get("total_visitors") or 0),
            "in_count": int(payload.get("in_count") or 0),
            "deliveries": int(payload.get("deliveries") or 0),
            "daily_help": int(payload.get("daily_help") or 0),
        }
