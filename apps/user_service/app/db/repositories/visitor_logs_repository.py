"""Visitor logs admin query persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import (
    PassAccessStatus,
    PassEntryMethod,
    PassEventType,
    PassStatus,
    PassType,
    VisitorLogBucket,
    VisitorLogVisitStatus,
    VisitorType,
    WalkInEventType,
    WalkInStatus,
)

# Filter-only type for visitor logs union; not stored on passes.pass_type.
WALK_IN_LOG_TYPE = "walk_in"

# Active contact_roles.role_type values treated as a flat resident for visitor logs.
_RESIDENT_ROLE_TYPES_SQL = """
                  'Owner'::contact_role_type,
                  'Tenant'::contact_role_type,
                  'Family'::contact_role_type
"""

_PERSON_NAME_SQL = """
TRIM(
    COALESCE(c.prefix, '') || ' ' ||
    COALESCE(c.first_name, '') || ' ' ||
    COALESCE(c.last_name, '')
)
"""

_UNION_OUTPUT_COLUMNS = """
  source,
  pass_id,
  pass_type,
  guest_name,
  visitor_phone_isd_code,
  visitor_phone_number,
  unit_label,
  tower_name,
  resident_contact_id,
  resident_person_name,
  resident_role,
  created_by,
  scheduled_from,
  scheduled_until,
  validity_type,
  entry_method,
  guard_user_id,
  guard_salutation,
  guard_first_name,
  guard_last_name,
  guard_name_fallback,
  access_status,
  visit_status,
  pass_code,
  is_private,
  in_time,
  out_time,
  flats_count,
  pass_image_path,
  daily_help_category_name,
  visitor_photo_paths,
  vehicle_photo_paths,
  sort_time,
  tie_breaker
"""


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

    @staticmethod
    def _branch_inclusion(
        *,
        pass_type: str | None,
        entry_method: str | None,
        access_status: str | None,
    ) -> tuple[bool, bool]:
        """Decide whether pass and walk-in branches participate in the union."""
        include_passes = True
        include_walk_ins = True

        if pass_type == WALK_IN_LOG_TYPE:
            include_passes = False
        elif pass_type is not None:
            include_walk_ins = False

        if entry_method is not None and entry_method != PassEntryMethod.MANUAL.value:
            include_walk_ins = False

        if access_status is not None and access_status != PassAccessStatus.GRANTED.value:
            include_walk_ins = False

        return include_passes, include_walk_ins

    def _build_pass_filters(
        self,
        *,
        search: str | None,
        pass_type: str | None,
        entry_method: str | None,
        access_status: str | None,
        tower_id: str | None,
        project_id: str | None,
        unit_id: str | None,
        param_index: int,
    ) -> tuple[str, list[Any], int]:
        """Build dynamic WHERE fragments for the pass branch."""
        clauses: list[str] = []
        args: list[Any] = []
        idx = param_index

        if search:
            clauses.append(
                f"("
                f"  p.guest_name ILIKE ${idx}"
                f"  OR COALESCE(u.unit_label, '') ILIKE ${idx}"
                f"  OR COALESCE(p.guest_phone_number, '') ILIKE ${idx}"
                f"  OR COALESCE(p.code, '') ILIKE ${idx}"
                f")"
            )
            args.append(f"%{search}%")
            idx += 1

        if pass_type and pass_type != WALK_IN_LOG_TYPE:
            clauses.append(f"p.pass_type = ${idx}::pass_type")
            args.append(pass_type)
            idx += 1

        if entry_method:
            clauses.append(
                f"ci.occurred_at IS NOT NULL AND ci.entry_method = ${idx}::pass_entry_method"
            )
            args.append(entry_method)
            idx += 1

        if access_status:
            clauses.append(
                f"ci.occurred_at IS NOT NULL AND ci.access_status = ${idx}::pass_access_status"
            )
            args.append(access_status)
            idx += 1

        if tower_id:
            clauses.append(f"u.tower_id = ${idx}::uuid")
            args.append(tower_id)
            idx += 1

        if project_id:
            clauses.append(f"p.project_id = ${idx}::uuid")
            args.append(project_id)
            idx += 1

        if unit_id:
            clauses.append(f"p.unit_id = ${idx}::uuid")
            args.append(unit_id)
            idx += 1

        if not clauses:
            return "", args, idx
        return " AND " + " AND ".join(clauses), args, idx

    def _build_walk_in_filters(
        self,
        *,
        search: str | None,
        tower_id: str | None,
        project_id: str | None,
        unit_id: str | None,
        param_index: int,
    ) -> tuple[str, list[Any], int]:
        """Build dynamic WHERE fragments for the walk-in branch."""
        clauses: list[str] = []
        args: list[Any] = []
        idx = param_index

        if search:
            clauses.append(
                f"("
                f"  w.visitor_first_name ILIKE ${idx}"
                f"  OR COALESCE(w.visitor_last_name, '') ILIKE ${idx}"
                f"  OR w.visitor_phone_number ILIKE ${idx}"
                f"  OR EXISTS ("
                f"    SELECT 1"
                f"    FROM walk_in_visit_units vu"
                f"    JOIN units u ON u.id = vu.unit_id AND u.organization_id = vu.organization_id"
                f"    WHERE vu.walk_in_entry_id = w.id"
                f"      AND COALESCE(u.unit_label, u.code, '') ILIKE ${idx}"
                f"  )"
                f")"
            )
            args.append(f"%{search}%")
            idx += 1

        if tower_id:
            clauses.append(
                f"EXISTS ("
                f"  SELECT 1 FROM walk_in_visit_units vu"
                f"  WHERE vu.walk_in_entry_id = w.id"
                f"    AND vu.tower_id = ${idx}::uuid"
                f")"
            )
            args.append(tower_id)
            idx += 1

        if project_id:
            clauses.append(f"w.project_id = ${idx}::uuid")
            args.append(project_id)
            idx += 1

        if unit_id:
            clauses.append(
                f"EXISTS ("
                f"  SELECT 1 FROM walk_in_visit_units vu"
                f"  WHERE vu.walk_in_entry_id = w.id"
                f"    AND vu.unit_id = ${idx}::uuid"
                f")"
            )
            args.append(unit_id)
            idx += 1

        if not clauses:
            return "", args, idx
        return " AND " + " AND ".join(clauses), args, idx

    @staticmethod
    def _build_outer_filters(
        *,
        bucket: str | None,
        visitor_type: str | None,
        guard_user_id: str | None,
        param_index: int,
    ) -> tuple[str, list[Any], int]:
        """Build WHERE fragments applied to the combined union subquery."""
        clauses: list[str] = []
        args: list[Any] = []
        idx = param_index

        if bucket and bucket != VisitorLogBucket.ALL.value:
            if bucket == VisitorLogBucket.AWAITING_APPROVAL.value:
                clauses.append(f"combined.visit_status = ${idx}")
                args.append(VisitorLogVisitStatus.AWAITING_APPROVAL.value)
                idx += 1
            elif bucket == VisitorLogBucket.INSIDE_NOW.value:
                clauses.append(f"combined.visit_status = ${idx}")
                args.append(VisitorLogVisitStatus.INSIDE.value)
                idx += 1
            elif bucket == VisitorLogBucket.COMPLETED.value:
                clauses.append(f"combined.visit_status = ${idx}")
                args.append(VisitorLogVisitStatus.EXITED.value)
                idx += 1
            elif bucket == VisitorLogBucket.DENIED_EXPIRED.value:
                clauses.append(f"combined.visit_status IN (${idx}, ${idx + 1})")
                args.extend(
                    [
                        VisitorLogVisitStatus.DENIED.value,
                        VisitorLogVisitStatus.EXPIRED.value,
                    ]
                )
                idx += 2

        if visitor_type == VisitorType.GUEST.value:
            clauses.append(f"combined.source = 'pass' AND combined.pass_type = ${idx}")
            args.append(PassType.GUEST.value)
            idx += 1
        elif visitor_type == VisitorType.VISITOR.value:
            clauses.append(f"(combined.source = 'walk_in' OR combined.pass_type <> ${idx})")
            args.append(PassType.GUEST.value)
            idx += 1

        if guard_user_id:
            clauses.append(f"combined.guard_user_id = ${idx}::text")
            args.append(guard_user_id)
            idx += 1

        if not clauses:
            return "", args, idx
        return " WHERE " + " AND ".join(clauses), args, idx

    @classmethod
    def _contact_role_sql(cls, *, org_expr: str, contact_expr: str, unit_expr: str) -> str:
        """Subquery for an active Owner/Tenant/Family role on a contact-unit pair."""
        return f"""(
            SELECT cr.role_type::text
            FROM contact_roles cr
            WHERE cr.organization_id = {org_expr}
              AND cr.contact_id = {contact_expr}
              AND cr.unit_id = {unit_expr}
              AND cr.status = 'active'::public.contact_role_status
              AND cr.ended_at IS NULL
              AND cr.role_type IN ({_RESIDENT_ROLE_TYPES_SQL})
            ORDER BY cr.started_at DESC
            LIMIT 1
        )"""

    @classmethod
    def _pass_resident_select_columns(cls) -> str:
        """SQL columns for the pass requester on the visited flat."""
        person_name = """
TRIM(
    COALESCE(creator.prefix, '') || ' ' ||
    COALESCE(creator.first_name, '') || ' ' ||
    COALESCE(creator.last_name, '')
)
"""
        return f"""
              p.created_by_contact_id::text AS resident_contact_id,
              CASE
                WHEN p.created_by_contact_id IS NOT NULL THEN {person_name}
                ELSE NULL
              END AS resident_person_name,
              CASE
                WHEN p.created_by_contact_id IS NOT NULL AND p.unit_id IS NOT NULL THEN {
            cls._contact_role_sql(
                org_expr="p.organization_id",
                contact_expr="p.created_by_contact_id",
                unit_expr="p.unit_id",
            )
        }
                ELSE NULL
              END AS resident_role,
        """

    @classmethod
    def _walk_in_resident_select_columns(cls) -> str:
        """SQL columns for the walk-in approver on the primary visited flat."""
        return f"""
              (
                SELECT vu.approved_by_contact_id::text
                FROM walk_in_visit_units vu
                WHERE vu.walk_in_entry_id = w.id
                  AND vu.approved_by_contact_id IS NOT NULL
                ORDER BY vu.sort_order, vu.created_at
                LIMIT 1
              ) AS resident_contact_id,
              (
                SELECT TRIM(
                    COALESCE(c.prefix, '') || ' ' ||
                    COALESCE(c.first_name, '') || ' ' ||
                    COALESCE(c.last_name, '')
                )
                FROM walk_in_visit_units vu
                JOIN contacts c
                  ON c.id = vu.approved_by_contact_id
                 AND c.organization_id = vu.organization_id
                WHERE vu.walk_in_entry_id = w.id
                  AND vu.approved_by_contact_id IS NOT NULL
                ORDER BY vu.sort_order, vu.created_at
                LIMIT 1
              ) AS resident_person_name,
              (
                SELECT cr.role_type::text
                FROM walk_in_visit_units vu
                JOIN contact_roles cr
                  ON cr.organization_id = vu.organization_id
                 AND cr.contact_id = vu.approved_by_contact_id
                 AND cr.unit_id = vu.unit_id
                 AND cr.status = 'active'::public.contact_role_status
                 AND cr.ended_at IS NULL
                 AND cr.role_type IN ({_RESIDENT_ROLE_TYPES_SQL})
                WHERE vu.walk_in_entry_id = w.id
                  AND vu.approved_by_contact_id IS NOT NULL
                ORDER BY vu.sort_order, vu.created_at, cr.started_at DESC
                LIMIT 1
              ) AS resident_role,
        """

    @staticmethod
    def _pass_branch_sql(*, filter_sql: str) -> str:
        """SQL selecting normalized pass rows (with or without gate check-in)."""
        resident_columns = VisitorLogsRepository._pass_resident_select_columns()
        approved = VisitorLogVisitStatus.APPROVED.value
        inside = VisitorLogVisitStatus.INSIDE.value
        exited = VisitorLogVisitStatus.EXITED.value
        expired = VisitorLogVisitStatus.EXPIRED.value
        denied = VisitorLogVisitStatus.DENIED.value
        pass_cancelled = PassStatus.CANCELLED.value
        pass_expired = PassStatus.EXPIRED.value
        access_denied = PassAccessStatus.DENIED.value
        return f"""
            SELECT
              'pass'::text AS source,
              p.id::text AS pass_id,
              p.pass_type::text AS pass_type,
              p.guest_name,
              p.guest_phone_isd_code AS visitor_phone_isd_code,
              p.guest_phone_number AS visitor_phone_number,
              u.unit_label,
              t.name AS tower_name,
              {resident_columns}
              TRIM(
                COALESCE(creator.first_name, '') || ' ' || COALESCE(creator.last_name, '')
              ) AS created_by,
              p.valid_from AS scheduled_from,
              p.valid_until AS scheduled_until,
              p.validity_type::text AS validity_type,
              ci.entry_method::text AS entry_method,
              ci.actor_user_id::text AS guard_user_id,
              guard_om.salutation AS guard_salutation,
              guard_om.first_name AS guard_first_name,
              guard_om.last_name AS guard_last_name,
              ci.actor_label AS guard_name_fallback,
              ci.access_status::text AS access_status,
              CASE
                WHEN ci.occurred_at IS NOT NULL AND co.occurred_at IS NOT NULL THEN '{exited}'
                WHEN ci.occurred_at IS NOT NULL AND co.occurred_at IS NULL THEN '{inside}'
                WHEN ci.occurred_at IS NOT NULL AND ci.access_status = '{access_denied}' THEN '{denied}'
                WHEN p.status = '{pass_cancelled}'::pass_status THEN '{denied}'
                WHEN p.status = '{pass_expired}'::pass_status THEN '{expired}'
                WHEN p.valid_until IS NOT NULL
                     AND p.valid_until < NOW()
                     AND ci.occurred_at IS NULL THEN '{expired}'
                ELSE '{approved}'
              END AS visit_status,
              p.code AS pass_code,
              COALESCE(p.is_private, false) AS is_private,
              ci.occurred_at AS in_time,
              co.occurred_at AS out_time,
              NULL::integer AS flats_count,
              COALESCE(p.pass_image_path, dh.photo_path) AS pass_image_path,
              NULL::text[] AS visitor_photo_paths,
              NULL::text[] AS vehicle_photo_paths,
              COALESCE(ci.occurred_at, p.valid_from) AS sort_time,
              p.created_at AS tie_breaker,
              dhc.name AS daily_help_category_name
            FROM passes p
            LEFT JOIN units u ON u.id = p.unit_id
            LEFT JOIN towers t ON t.id = u.tower_id
            LEFT JOIN contacts creator ON creator.id = p.created_by_contact_id
            LEFT JOIN daily_help_profiles dh
              ON dh.id = p.daily_help_id
             AND dh.organization_id = p.organization_id
            LEFT JOIN daily_help_categories dhc
              ON dhc.id = dh.category_id
             AND dhc.organization_id = dh.organization_id
            LEFT JOIN LATERAL (
                SELECT
                  pe.occurred_at,
                  pe.entry_method,
                  pe.access_status,
                  pe.actor_user_id,
                  pe.actor_label
                FROM pass_events pe
                WHERE pe.organization_id = p.organization_id
                  AND pe.pass_id = p.id
                  AND pe.event_type = '{PassEventType.CHECKED_IN.value}'::pass_event_type
                ORDER BY pe.occurred_at DESC, pe.created_at DESC
                LIMIT 1
            ) ci ON true
            LEFT JOIN organization_members guard_om
              ON guard_om.organization_id = p.organization_id
             AND guard_om.user_id = ci.actor_user_id
             AND guard_om.status <> 'deleted'
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
              AND (
                (ci.occurred_at IS NOT NULL AND ci.occurred_at >= $2 AND ci.occurred_at < $3)
                OR (
                  ci.occurred_at IS NULL
                  AND p.valid_from < $3
                  AND (p.valid_until IS NULL OR p.valid_until >= $2)
                )
              )
              {filter_sql}
        """

    @staticmethod
    def _walk_in_branch_sql(*, filter_sql: str) -> str:
        """SQL selecting normalized walk-in rows (with or without gate entry)."""
        entered_event = WalkInEventType.ENTERED.value
        resident_columns = VisitorLogsRepository._walk_in_resident_select_columns()
        awaiting = VisitorLogVisitStatus.AWAITING_APPROVAL.value
        approved = VisitorLogVisitStatus.APPROVED.value
        inside = VisitorLogVisitStatus.INSIDE.value
        exited = VisitorLogVisitStatus.EXITED.value
        denied = VisitorLogVisitStatus.DENIED.value
        wi_awaiting = WalkInStatus.AWAITING.value
        wi_approved = WalkInStatus.APPROVED.value
        wi_entered = WalkInStatus.ENTERED.value
        wi_exited = WalkInStatus.EXITED.value
        wi_cancelled = WalkInStatus.CANCELLED.value
        return f"""
            SELECT
              'walk_in'::text AS source,
              w.id::text AS pass_id,
              '{WALK_IN_LOG_TYPE}'::text AS pass_type,
              TRIM(
                COALESCE(w.visitor_first_name, '')
                || ' ' || COALESCE(w.visitor_last_name, '')
              ) AS guest_name,
              w.visitor_phone_isd_code,
              w.visitor_phone_number,
              (
                SELECT COALESCE(u.unit_label, u.code)
                FROM walk_in_visit_units vu
                JOIN units u
                  ON u.id = vu.unit_id
                 AND u.organization_id = vu.organization_id
                WHERE vu.walk_in_entry_id = w.id
                ORDER BY vu.sort_order, vu.created_at
                LIMIT 1
              ) AS unit_label,
              (
                SELECT t.name
                FROM walk_in_visit_units vu
                JOIN towers t
                  ON t.id = vu.tower_id
                 AND t.organization_id = vu.organization_id
                WHERE vu.walk_in_entry_id = w.id
                ORDER BY vu.sort_order, vu.created_at
                LIMIT 1
              ) AS tower_name,
              {resident_columns}
              TRIM(
                COALESCE(requester.salutation, '') || ' '
                || COALESCE(requester.first_name, '') || ' '
                || COALESCE(requester.last_name, '')
              ) AS created_by,
              w.requested_at AS scheduled_from,
              NULL::timestamptz AS scheduled_until,
              NULL::text AS validity_type,
              '{PassEntryMethod.MANUAL.value}'::text AS entry_method,
              wi_enter.actor_user_id::text AS guard_user_id,
              guard_om.salutation AS guard_salutation,
              guard_om.first_name AS guard_first_name,
              guard_om.last_name AS guard_last_name,
              wi_enter.actor_label AS guard_name_fallback,
              '{PassAccessStatus.GRANTED.value}'::text AS access_status,
              CASE w.status::text
                WHEN '{wi_awaiting}' THEN '{awaiting}'
                WHEN '{wi_approved}' THEN '{approved}'
                WHEN '{wi_entered}' THEN '{inside}'
                WHEN '{wi_exited}' THEN '{exited}'
                WHEN '{wi_cancelled}' THEN '{denied}'
                ELSE '{approved}'
              END AS visit_status,
              NULL::text AS pass_code,
              false AS is_private,
              w.entered_at AS in_time,
              w.exited_at AS out_time,
              w.flats_count,
              NULL::text AS pass_image_path,
              w.visitor_photo_paths,
              w.vehicle_photo_paths,
              COALESCE(w.entered_at, w.requested_at) AS sort_time,
              w.created_at AS tie_breaker,
              NULL::text AS daily_help_category_name
            FROM walk_in_entries w
            LEFT JOIN organization_members requester
              ON requester.organization_id = w.organization_id
             AND requester.user_id = w.requested_by_user_id
             AND requester.status <> 'deleted'
            LEFT JOIN LATERAL (
                SELECT
                  we.actor_user_id,
                  we.actor_label
                FROM walk_in_events we
                WHERE we.organization_id = w.organization_id
                  AND we.walk_in_entry_id = w.id
                  AND we.event_type = '{entered_event}'::walk_in_event_type
                ORDER BY we.occurred_at DESC, we.created_at DESC
                LIMIT 1
            ) wi_enter ON true
            LEFT JOIN organization_members guard_om
              ON guard_om.organization_id = w.organization_id
             AND guard_om.user_id = wi_enter.actor_user_id
             AND guard_om.status <> 'deleted'
            WHERE w.organization_id = $1::uuid
              AND (
                (w.entered_at IS NOT NULL AND w.entered_at >= $2 AND w.entered_at < $3)
                OR (
                  w.entered_at IS NULL
                  AND w.requested_at >= $2
                  AND w.requested_at < $3
                )
              )
              {filter_sql}
        """

    def _build_union_query(
        self,
        *,
        include_passes: bool,
        include_walk_ins: bool,
        pass_filter_sql: str,
        walk_in_filter_sql: str,
    ) -> str:
        """Combine enabled branches into a UNION ALL subquery."""
        branches: list[str] = []
        if include_passes:
            branches.append(self._pass_branch_sql(filter_sql=pass_filter_sql))
        if include_walk_ins:
            branches.append(self._walk_in_branch_sql(filter_sql=walk_in_filter_sql))
        if not branches:
            return ""
        return " UNION ALL ".join(branches)

    def _prepare_union(
        self,
        *,
        organization_id: str,
        range_start: datetime,
        range_end: datetime,
        search: str | None = None,
        pass_type: str | None = None,
        entry_method: str | None = None,
        access_status: str | None = None,
        tower_id: str | None = None,
        project_id: str | None = None,
        unit_id: str | None = None,
    ) -> tuple[str, list[Any]]:
        """Build union SQL and bound args for list/overview queries."""
        include_passes, include_walk_ins = self._branch_inclusion(
            pass_type=pass_type,
            entry_method=entry_method,
            access_status=access_status,
        )

        args: list[Any] = [organization_id, range_start, range_end]
        pass_filter_sql, pass_filter_args, next_idx = self._build_pass_filters(
            search=search,
            pass_type=pass_type,
            entry_method=entry_method,
            access_status=access_status,
            tower_id=tower_id,
            project_id=project_id,
            unit_id=unit_id,
            param_index=4,
        )
        args.extend(pass_filter_args)

        walk_in_filter_sql = ""
        if include_walk_ins:
            walk_in_filter_sql, walk_in_filter_args, _ = self._build_walk_in_filters(
                search=search,
                tower_id=tower_id,
                project_id=project_id,
                unit_id=unit_id,
                param_index=next_idx,
            )
            args.extend(walk_in_filter_args)

        union_sql = self._build_union_query(
            include_passes=include_passes,
            include_walk_ins=include_walk_ins,
            pass_filter_sql=pass_filter_sql,
            walk_in_filter_sql=walk_in_filter_sql,
        )
        return union_sql, args

    async def list_logs(
        self,
        *,
        organization_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        search: str | None = None,
        bucket: str | None = None,
        visitor_type: str | None = None,
        pass_type: str | None = None,
        entry_method: str | None = None,
        access_status: str | None = None,
        tower_id: str | None = None,
        guard_user_id: str | None = None,
        project_id: str | None = None,
        unit_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List visitor log rows for the admin table."""
        range_start, range_end = self._resolve_range(start_at=start_at, end_at=end_at)
        union_sql, args = self._prepare_union(
            organization_id=organization_id,
            range_start=range_start,
            range_end=range_end,
            search=search,
            pass_type=pass_type,
            entry_method=entry_method,
            access_status=access_status,
            tower_id=tower_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not union_sql:
            return [], 0

        outer_filter_sql, outer_filter_args, _ = self._build_outer_filters(
            bucket=bucket,
            visitor_type=visitor_type,
            guard_user_id=guard_user_id,
            param_index=len(args) + 1,
        )
        args.extend(outer_filter_args)
        combined_sql = f"({union_sql}) combined{outer_filter_sql}"

        count = await self.db_connection.fetchval(
            f"SELECT COUNT(*) FROM {combined_sql}",
            *args,
        )

        offset = (page - 1) * page_size
        limit_idx = len(args) + 1
        offset_idx = len(args) + 2
        args.extend([page_size, offset])

        rows = await self.db_connection.fetch(
            f"""
            SELECT {_UNION_OUTPUT_COLUMNS}
            FROM {combined_sql}
            ORDER BY sort_time DESC, tie_breaker DESC
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
        project_id: str | None = None,
        unit_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate overview card metrics for a date range."""
        range_start, range_end = self._resolve_range(start_at=start_at, end_at=end_at)
        union_sql, args = self._prepare_union(
            organization_id=organization_id,
            range_start=range_start,
            range_end=range_end,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not union_sql:
            return {
                "start_at": range_start,
                "end_at": range_end,
                "total_entries": 0,
                "inside_now": 0,
                "awaiting_approval": 0,
                "walk_ins": 0,
                "exited": 0,
                "denied_expired": 0,
            }

        awaiting = VisitorLogVisitStatus.AWAITING_APPROVAL.value
        inside = VisitorLogVisitStatus.INSIDE.value
        exited = VisitorLogVisitStatus.EXITED.value
        denied = VisitorLogVisitStatus.DENIED.value
        expired = VisitorLogVisitStatus.EXPIRED.value

        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              COUNT(*)::int AS total_entries,
              COUNT(*) FILTER (
                WHERE visit_status = '{inside}'
              )::int AS inside_now,
              COUNT(*) FILTER (
                WHERE visit_status = '{awaiting}'
              )::int AS awaiting_approval,
              COUNT(*) FILTER (
                WHERE source = 'walk_in'
              )::int AS walk_ins,
              COUNT(*) FILTER (
                WHERE visit_status = '{exited}'
              )::int AS exited,
              COUNT(*) FILTER (
                WHERE visit_status IN ('{denied}', '{expired}')
              )::int AS denied_expired
            FROM ({union_sql}) combined
            """,
            *args,
        )
        payload = dict(row) if row else {}
        return {
            "start_at": range_start,
            "end_at": range_end,
            "total_entries": int(payload.get("total_entries") or 0),
            "inside_now": int(payload.get("inside_now") or 0),
            "awaiting_approval": int(payload.get("awaiting_approval") or 0),
            "walk_ins": int(payload.get("walk_ins") or 0),
            "exited": int(payload.get("exited") or 0),
            "denied_expired": int(payload.get("denied_expired") or 0),
        }
