"""Dashboard metrics — org-scoped aggregations (asyncpg).

``fetch_dashboard`` reads IANA timezone from ``organization_members.timezone``, validates it
in Python (invalid values fall back to UTC), then runs the dashboard aggregate query.

Indexes: ``leads (organization_id, created_at DESC)`` can help weekly/daily facets; add if
``EXPLAIN`` shows seq scans.
"""

from __future__ import annotations

from textwrap import dedent
from typing import Any

import asyncpg

from apps.user_service.app.schemas.dashboard import validate_iana_timezone
from apps.user_service.app.schemas.enums import ProjectStatus

# Terminal pipeline keys (see ``LeadStatus`` / default lead stages).
CLOSED_LEAD_STAGE_KEYS: frozenset[str] = frozenset({"converted", "lost"})

_ACTIVE_PIPELINE_PROJECT_STATUSES: tuple[str, ...] = (
    ProjectStatus.DISCOVERY.value,
    ProjectStatus.ACTIVE.value,
    ProjectStatus.ON_HOLD.value,
)

_DEFAULT_MY_PROJECTS_LIMIT = 50

# --- ``fetch_dashboard`` aggregate SQL (validated IANA name bound as $2) ---
# $1 organization_id, $2 timezone (text), $3 closed lead stage keys, $4 active project
# statuses, $5 archived status, $6 user_id, $7 my_projects limit.

_CTE_BOUNDS = dedent("""
    bounds AS (
        SELECT
            ((date_trunc(
                'week',
                (now() AT TIME ZONE $2::text)::timestamp
            )) AT TIME ZONE $2::text) AS week_start,
            (((date_trunc(
                'week',
                (now() AT TIME ZONE $2::text)::timestamp
            ) + interval '7 days')) AT TIME ZONE $2::text) AS next_week_start,
            (((date_trunc(
                'week',
                (now() AT TIME ZONE $2::text)::timestamp
            ) - interval '7 days')) AT TIME ZONE $2::text) AS prev_week_start
    )
""").strip()

_CTE_DAILY_LEADS = dedent("""
    daily_leads AS (
        SELECT (l.created_at AT TIME ZONE $2::text)::date AS d,
               COUNT(*)::int AS leads_count
        FROM leads l
        WHERE l.organization_id = $1::uuid
        GROUP BY 1
    )
""").strip()

_CTE_WEEKLY_SERIES = dedent("""
    weekly_series AS (
        SELECT gs.d::date AS day,
               COALESCE(dl.leads_count, 0)::int AS leads_count
        FROM generate_series(
            ((now() AT TIME ZONE $2::text)::date - 6),
            (now() AT TIME ZONE $2::text)::date,
            interval '1 day'
        ) AS gs(d)
        LEFT JOIN daily_leads dl ON dl.d = gs.d::date
    )
""").strip()

_CTE_PIPELINE_ROWS = dedent("""
    pipeline_rows AS (
        SELECT
            ls.stage_key,
            ls.stage_name,
            ls.sort_order,
            COUNT(l.id)::int AS count
        FROM lead_stages ls
        LEFT JOIN leads l
          ON l.stage_id = ls.id AND l.organization_id = ls.organization_id
        WHERE ls.organization_id = $1::uuid
        GROUP BY ls.id, ls.stage_key, ls.stage_name, ls.sort_order
    )
""").strip()

_CTE_MY_PROJECT_ROWS = dedent("""
    my_project_rows AS (
        SELECT
            p.id,
            p.project_id,
            p.project_title,
            p.status,
            p.priority,
            p.target_end_date,
            p.start_date,
            p.created_at
        FROM projects p
        INNER JOIN team_members tm
          ON tm.team_id = p.team_id AND tm.user_id = $6::uuid
        WHERE p.organization_id = $1::uuid
          AND p.status <> $5::text
        ORDER BY p.created_at DESC
        LIMIT $7::int
    )
""").strip()

_SELECT_DASHBOARD_OUTER = dedent("""
    SELECT
        (SELECT COUNT(*)::int FROM contacts c
         WHERE c.organization_id = $1::uuid AND c.status <> 'deleted') AS total_contacts,
        (SELECT COUNT(*)::int FROM companies c
         WHERE c.organization_id = $1::uuid AND c.status <> 'deleted') AS total_companies,
        (SELECT COUNT(*)::int
         FROM leads l
         WHERE l.organization_id = $1::uuid
           AND (
             l.stage_id IS NULL
             OR NOT EXISTS (
               SELECT 1
               FROM lead_stages s
               WHERE s.id = l.stage_id
                 AND s.organization_id = l.organization_id
                 AND s.stage_key = ANY ($3::text[])
             )
           )) AS open_leads,
        (SELECT COUNT(*)::int FROM leads l
         WHERE l.organization_id = $1::uuid AND l.stage_id IS NULL) AS leads_without_stage,
        (SELECT COUNT(*)::int FROM projects p
         WHERE p.organization_id = $1::uuid
           AND p.status = ANY ($4::text[])) AS active_projects,
        (SELECT COUNT(*)::int FROM projects p
         WHERE p.organization_id = $1::uuid
           AND p.status = ANY ($4::text[])
           AND p.start_date IS NOT NULL
           AND p.start_date > (CURRENT_TIMESTAMP AT TIME ZONE $2::text)::date
           AND p.start_date <= (CURRENT_TIMESTAMP AT TIME ZONE $2::text)::date + 14
        ) AS launching_soon,
        (SELECT COUNT(*)::int FROM contacts c, bounds b
         WHERE c.organization_id = $1::uuid AND c.status <> 'deleted'
           AND c.created_at >= b.week_start AND c.created_at < b.next_week_start
        ) AS contacts_new_this_week,
        (SELECT COUNT(*)::int FROM contacts c, bounds b
         WHERE c.organization_id = $1::uuid AND c.status <> 'deleted'
           AND c.created_at >= b.prev_week_start AND c.created_at < b.week_start
        ) AS contacts_new_prev_week,
        (SELECT COUNT(*)::int FROM companies c, bounds b
         WHERE c.organization_id = $1::uuid AND c.status <> 'deleted'
           AND c.created_at >= b.week_start AND c.created_at < b.next_week_start
        ) AS companies_new_this_week,
        (SELECT COUNT(*)::int FROM companies c, bounds b
         WHERE c.organization_id = $1::uuid AND c.status <> 'deleted'
           AND c.created_at >= b.prev_week_start AND c.created_at < b.week_start
        ) AS companies_new_prev_week,
        (SELECT COUNT(*)::int FROM leads l, bounds b
         WHERE l.organization_id = $1::uuid
           AND l.created_at >= b.week_start AND l.created_at < b.next_week_start
        ) AS leads_new_this_week,
        (SELECT COUNT(*)::int FROM leads l, bounds b
         WHERE l.organization_id = $1::uuid
           AND l.created_at >= b.prev_week_start AND l.created_at < b.week_start
        ) AS leads_new_prev_week,
        (SELECT COUNT(*)::int FROM projects p, bounds b
         WHERE p.organization_id = $1::uuid AND p.status <> $5::text
           AND p.created_at >= b.week_start AND p.created_at < b.next_week_start
        ) AS projects_new_this_week,
        (SELECT COUNT(*)::int FROM projects p, bounds b
         WHERE p.organization_id = $1::uuid AND p.status <> $5::text
           AND p.created_at >= b.prev_week_start AND p.created_at < b.week_start
        ) AS projects_new_prev_week,
        COALESCE(
            (SELECT json_agg(
                json_build_object('day', day, 'leads_count', leads_count)
                ORDER BY day
            ) FROM weekly_series),
            '[]'::json
        ) AS weekly_activity,
        COALESCE(
            (SELECT json_agg(
                json_build_object(
                    'stage_key', stage_key,
                    'stage_name', stage_name,
                    'sort_order', sort_order,
                    'count', count
                )
                ORDER BY sort_order
            ) FROM pipeline_rows),
            '[]'::json
        ) AS lead_pipeline,
        COALESCE(
            (SELECT json_agg(
                json_build_object(
                    'id', id,
                    'project_id', project_id,
                    'project_title', project_title,
                    'status', status,
                    'priority', priority,
                    'target_end_date', target_end_date,
                    'start_date', start_date,
                    'created_at', created_at
                )
                ORDER BY created_at DESC
            ) FROM my_project_rows),
            '[]'::json
        ) AS my_projects
""").strip()

_FETCH_DASHBOARD_SQL = "\n".join(
    (
        "WITH",
        _CTE_BOUNDS + ",",
        _CTE_DAILY_LEADS + ",",
        _CTE_WEEKLY_SERIES + ",",
        _CTE_PIPELINE_ROWS + ",",
        _CTE_MY_PROJECT_ROWS,
        _SELECT_DASHBOARD_OUTER,
    )
)


_MEMBER_TIMEZONE_SQL = dedent("""
    SELECT COALESCE(NULLIF(TRIM(timezone::text), ''), 'UTC') AS tz
    FROM organization_members
    WHERE organization_id = $1::uuid
      AND user_id = $2::uuid
      AND status <> 'deleted'
    LIMIT 1
""").strip()


class DashboardRepository:
    """Read-only dashboard SQL for one organization."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self._db = db_connection

    async def _resolve_dashboard_timezone(self, organization_id: str, user_id: str) -> str:
        """Resolve dashboard timezone from organization member row."""
        row = await self._db.fetchrow(_MEMBER_TIMEZONE_SQL, organization_id, user_id)
        raw = (dict(row) if row else {}).get("tz") or "UTC"
        try:
            return validate_iana_timezone(str(raw).strip())
        except ValueError:
            return "UTC"

    async def fetch_dashboard(
        self,
        organization_id: str,
        user_id: str,
        my_projects_limit: int = _DEFAULT_MY_PROJECTS_LIMIT,
    ) -> dict[str, Any]:
        """Dashboard aggregates; timezone from member row (validated), then one aggregate query."""
        timezone_name = await self._resolve_dashboard_timezone(organization_id, user_id)
        closed_keys = sorted(CLOSED_LEAD_STAGE_KEYS)
        row = await self._db.fetchrow(
            _FETCH_DASHBOARD_SQL,
            organization_id,
            timezone_name,
            closed_keys,
            list(_ACTIVE_PIPELINE_PROJECT_STATUSES),
            ProjectStatus.ARCHIVED.value,
            user_id,
            my_projects_limit,
        )
        out = dict(row) if row else {}
        out["user_timezone"] = timezone_name
        return out
