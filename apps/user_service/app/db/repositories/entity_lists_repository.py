"""Repository for entity lists and memberships.

This repository assumes the following tables already exist in the database:
- `entity_lists`
- `entity_list_members`
"""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    EntityListStatus,
    EntityTable,
    EntityType,
)
from apps.user_service.app.utils.common_utils import parse_json_any

_CONTACT_ENTITY_TYPE = EntityType.CONTACT.value
_COMPANY_ENTITY_TYPE = EntityType.COMPANY.value
_LEAD_ENTITY_TYPE = EntityType.LEAD.value
_DELETED_STATUS_VALUE = ClientStatus.DELETED.value


def _resolve_entity_table_and_filters(
    *,
    entity_type: EntityType,
) -> tuple[EntityTable, str]:
    """Resolve entity table name and soft-delete filter for the list entity type."""
    if entity_type == EntityType.CONTACT:
        return EntityTable.CONTACTS, f"AND e.status != '{_DELETED_STATUS_VALUE}'::text"
    if entity_type == EntityType.COMPANY:
        return EntityTable.COMPANIES, f"AND e.status != '{_DELETED_STATUS_VALUE}'::text"
    return EntityTable.LEADS, ""


def _coerce_json_fields(row: dict[str, Any], field_names: tuple[str, ...]) -> dict[str, Any]:
    """Coerce JSON-like fields that may arrive as JSON strings."""
    out = dict(row)
    for field_name in field_names:
        out[field_name] = parse_json_any(out.get(field_name), default=None)
    return out


def _build_where_sql_and_params(
    *,
    base_conditions: list[str],
    base_params: list[Any],
    optional_conditions: dict[str, Any],
) -> tuple[str, list[Any], int]:
    """Build a WHERE clause with positional parameters for asyncpg.

    Args:
        base_conditions: SQL fragments that already contain correct positional params.
        base_params: Parameters matching the base conditions.
        optional_conditions: Mapping of SQL fragment templates to values.
            Each key must be a SQL fragment that contains `{param}` placeholder where a
            positional parameter should be placed.

    Returns:
        Tuple of `(where_sql, params, next_param_index)`.
    """
    where_parts = list(base_conditions)
    params = list(base_params)
    next_param_index = len(params) + 1
    for condition_template, value in optional_conditions.items():
        if value is None:
            continue
        where_parts.append(condition_template.format(param=next_param_index))
        params.append(value)
        next_param_index += 1
    return " AND ".join(where_parts), params, next_param_index


_ENTITY_TYPE_LIST_QUERY_CONFIG: dict[EntityType, dict[str, Any]] = {
    EntityType.CONTACT: {
        "entity_table": EntityTable.CONTACTS,
        "has_soft_delete": True,
        "has_enrichment_fields": True,
    },
    EntityType.COMPANY: {
        "entity_table": EntityTable.COMPANIES,
        "has_soft_delete": True,
        "has_enrichment_fields": True,
    },
    EntityType.LEAD: {
        "entity_table": EntityTable.LEADS,
        "has_soft_delete": False,
        "has_enrichment_fields": False,
    },
}


class EntityListsRepository(BaseRepository):
    """Database operations for lists and list membership."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Create a repository with a request-scoped db connection."""
        super().__init__(db_connection=db_connection)

    async def create_list(
        self,
        *,
        organization_id: str,
        name: str,
        entity_type: EntityType,
        description: str | None,
        tags: list[str],
        entity_ids: list[str] | None,
    ) -> dict[str, Any]:
        """Create a list and add initial members.

        The list is always created. Members are inserted only when `entity_ids` is non-empty.
        Invalid entity IDs (wrong org / missing / soft-deleted) are reported.
        """
        ids = entity_ids or []
        entity_table, soft_delete_filter = _resolve_entity_table_and_filters(
            entity_type=entity_type,
        )

        row = await self.db_connection.fetchrow(
            f"""
            WITH created AS (
              INSERT INTO entity_lists (
                organization_id,
                name,
                entity_type,
                description,
                tags,
                status
              )
              VALUES ($1::uuid, $2::text, $3::text, $4::text, $5::text[], $6::text)
              RETURNING *
            ),
            requested AS (
              SELECT DISTINCT unnest($7::uuid[]) AS entity_id
            ),
            existing_entities AS (
              SELECT r.entity_id
              FROM requested r
              INNER JOIN {entity_table.value} e
                ON e.id = r.entity_id
               AND e.organization_id = $1::uuid
               {soft_delete_filter}
            ),
            inserted AS (
              INSERT INTO entity_list_members (list_id, entity_id)
              SELECT c.id, ee.entity_id
              FROM created c
              INNER JOIN existing_entities ee ON TRUE
              ON CONFLICT (list_id, entity_id) DO NOTHING
              RETURNING entity_id
            ),
            invalid_ids AS (
              SELECT r.entity_id::text AS entity_id
              FROM requested r
              LEFT JOIN existing_entities ee
                ON ee.entity_id = r.entity_id
              WHERE ee.entity_id IS NULL
            )
            SELECT
              to_jsonb(c.*) AS list,
              jsonb_build_object(
                'requested', (SELECT COUNT(*) FROM requested)::int,
                'added', (SELECT COUNT(*) FROM inserted)::int,
                'removed', 0,
                'already_present', 0,
                'invalid_ids', COALESCE((SELECT jsonb_agg(entity_id) FROM invalid_ids), '[]'::jsonb)
              ) AS members
            FROM created c
            """,
            organization_id,
            name,
            entity_type.value,
            description,
            tags,
            EntityListStatus.ACTIVE.value,
            ids,
        )
        if not row:
            return {}
        return _coerce_json_fields(
            dict(row),
            ("list", "members", "add_result", "remove_result"),
        )

    async def get_list(
        self,
        *,
        organization_id: str,
        list_id: str,
    ) -> dict[str, Any] | None:
        """Return a list row by id, scoped to an organization."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM entity_lists
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            """,
            list_id,
            organization_id,
        )
        return dict(row) if row else None

    async def get_list_details(
        self,
        *,
        organization_id: str,
        list_id: str,
    ) -> dict[str, Any] | None:
        """Return list details with derived counters (single list)."""
        row = await self.db_connection.fetchrow(
            f"""
            WITH el AS (
              SELECT *
              FROM entity_lists
              WHERE id = $1::uuid
                AND organization_id = $2::uuid
            ),
            members AS (
              SELECT m.entity_id, m.created_at
              FROM entity_list_members m
              INNER JOIN el ON el.id = m.list_id
            ),
            contact_company_names AS (
              SELECT
                cc.contact_id,
                ARRAY_REMOVE(ARRAY_AGG(co.name ORDER BY co.name), NULL) AS company_names
              FROM contact_companies cc
              INNER JOIN el
                ON el.organization_id = cc.organization_id
               AND el.entity_type = '{_CONTACT_ENTITY_TYPE}'::text
              INNER JOIN members m ON m.entity_id = cc.contact_id
              INNER JOIN companies co
                ON co.id = cc.company_id
               AND co.organization_id = cc.organization_id
               AND co.status != '{_DELETED_STATUS_VALUE}'::text
              GROUP BY cc.contact_id
            ),
            resolved AS (
              SELECT
                m.entity_id,
                m.created_at,
                COALESCE(ct.enrichment_done, co.enrichment_done, FALSE) AS enrichment_done,
                COALESCE(ct.enrichment_status, co.enrichment_status, NULL::text) AS enrichment_status,
                CASE
                  WHEN ct.id IS NOT NULL THEN
                    jsonb_build_object(
                      'id', ct.id::text,
                      'organization_id', ct.organization_id::text,
                      'status', ct.status,
                      'first_name', ct.first_name,
                      'last_name', ct.last_name,
                      'title', ct.title,
                      'email', NULLIF(au.email::text, ''),
                      'profile_photo_url', ct.profile_photo_url,
                      'phones', COALESCE(ct.phones, '[]'::jsonb),
                      'company_names', COALESCE(ccn.company_names, ARRAY[]::text[]),
                      'enrichment_done', COALESCE(ct.enrichment_done, FALSE),
                      'enrichment_status', ct.enrichment_status,
                      'created_at', ct.created_at::text,
                      'updated_at', ct.updated_at::text
                    )
                  WHEN co.id IS NOT NULL THEN
                    jsonb_build_object(
                      'id', co.id::text,
                      'organization_id', co.organization_id::text,
                      'status', co.status,
                      'name', co.name,
                      'industry', co.industry,
                      'email', co.email,
                      'profile_photo_url', co.profile_photo_url,
                      'phones', COALESCE(co.phones, '[]'::jsonb),
                      'tags', COALESCE(co.tags, ARRAY[]::text[]),
                      'enrichment_done', COALESCE(co.enrichment_done, FALSE),
                      'enrichment_status', co.enrichment_status,
                      'created_at', co.created_at::text,
                      'updated_at', co.updated_at::text
                    )
                  WHEN ld.id IS NOT NULL THEN
                    jsonb_build_object(
                      'id', ld.id::text,
                      'organization_id', ld.organization_id::text,
                      'name', ld.name,
                      'stage_id', ld.stage_id::text,
                      'lead_source', ld.lead_source,
                      'referral_source', ld.referral_source,
                      'deal_type', ld.deal_type,
                      'priority', ld.priority,
                      'lead_score', ld.lead_score,
                      'close_date', ld.close_date,
                      'amount', ld.amount,
                      'owner_id', ld.owner_id::text,
                      'created_at', ld.created_at::text,
                      'updated_at', ld.updated_at::text
                    )
                  ELSE NULL::jsonb
                END AS item
              FROM members m
              INNER JOIN el ON TRUE
              LEFT JOIN contacts ct
                ON el.entity_type = '{_CONTACT_ENTITY_TYPE}'::text
               AND ct.id = m.entity_id
               AND ct.organization_id = el.organization_id
               AND ct.status != '{_DELETED_STATUS_VALUE}'::text
              LEFT JOIN auth.users au ON au.id = ct.user_id
              LEFT JOIN contact_company_names ccn ON ccn.contact_id = ct.id
              LEFT JOIN companies co
                ON el.entity_type = '{_COMPANY_ENTITY_TYPE}'::text
               AND co.id = m.entity_id
               AND co.organization_id = el.organization_id
               AND co.status != '{_DELETED_STATUS_VALUE}'::text
              LEFT JOIN leads ld
                ON el.entity_type = '{_LEAD_ENTITY_TYPE}'::text
               AND ld.id = m.entity_id
               AND ld.organization_id = el.organization_id
              WHERE ct.id IS NOT NULL OR co.id IS NOT NULL OR ld.id IS NOT NULL
            )
            SELECT
              el.id::text AS id,
              el.organization_id::text AS organization_id,
              el.name,
              el.entity_type,
              el.description,
              el.tags,
              el.status,
              el.created_at::text AS created_at,
              el.updated_at::text AS updated_at,
              COUNT(r.entity_id) AS total_items,
              COUNT(r.entity_id) FILTER (
                WHERE COALESCE(r.enrichment_done, FALSE) = TRUE
                  OR LOWER(COALESCE(r.enrichment_status, '')) IN (
                    'enriched',
                    'done',
                    'completed'
                  )
              ) AS enriched,
              COUNT(r.entity_id) FILTER (
                WHERE LOWER(COALESCE(r.enrichment_status, '')) IN (
                  'pending',
                  'requested',
                  'processing'
                )
              ) AS pending,
              COUNT(r.entity_id) FILTER (
                WHERE LOWER(COALESCE(r.enrichment_status, '')) IN ('failed', 'error')
              ) AS failed
              ,
              COALESCE(
                jsonb_agg(r.item ORDER BY r.created_at DESC) FILTER (WHERE r.item IS NOT NULL),
                '[]'::jsonb
              ) AS items
            FROM el
            LEFT JOIN resolved r ON TRUE
            GROUP BY
              el.id,
              el.organization_id,
              el.name,
              el.entity_type,
              el.description,
              el.tags,
              el.status,
              el.created_at,
              el.updated_at
            """,
            list_id,
            organization_id,
        )
        if not row:
            return None
        result = dict(row)
        result["items"] = parse_json_any(result.get("items"), default=[])
        return result

    async def update_list(
        self,
        *,
        organization_id: str,
        list_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update list metadata and membership.

        Supported keys in update_data:
        - `name` (text)
        - `description` (text)
        - `tags` (text[])
        - `status` (text)
        - `add_entity_ids` (list[str]) - optional membership add IDs
        - `remove_entity_ids` (list[str]) - optional membership remove IDs
        """
        add_entity_ids = list(update_data.pop("add_entity_ids", []) or [])
        remove_entity_ids = list(update_data.pop("remove_entity_ids", []) or [])

        row = await self.db_connection.fetchrow(
            f"""
            WITH updated AS (
              UPDATE entity_lists
              SET
                name = COALESCE($3::text, name),
                description = COALESCE($4::text, description),
                tags = COALESCE($5::text[], tags),
                status = COALESCE($6::text, status),
                updated_at = NOW()
              WHERE id = $1::uuid
                AND organization_id = $2::uuid
                AND status != '{_DELETED_STATUS_VALUE}'::text
              RETURNING id, organization_id, entity_type
            ),
            requested_add AS (
              SELECT DISTINCT unnest($7::uuid[]) AS entity_id
            ),
            requested_remove AS (
              SELECT DISTINCT unnest($8::uuid[]) AS entity_id
            ),
            existing_add_entities AS (
              SELECT ra.entity_id
              FROM requested_add ra
              INNER JOIN updated u ON TRUE
              LEFT JOIN contacts ct
                ON u.entity_type = '{_CONTACT_ENTITY_TYPE}'::text
               AND ct.id = ra.entity_id
               AND ct.organization_id = u.organization_id
               AND ct.status != '{_DELETED_STATUS_VALUE}'::text
              LEFT JOIN companies co
                ON u.entity_type = '{_COMPANY_ENTITY_TYPE}'::text
               AND co.id = ra.entity_id
               AND co.organization_id = u.organization_id
               AND co.status != '{_DELETED_STATUS_VALUE}'::text
              LEFT JOIN leads ld
                ON u.entity_type = '{_LEAD_ENTITY_TYPE}'::text
               AND ld.id = ra.entity_id
               AND ld.organization_id = u.organization_id
              WHERE (u.entity_type = '{_CONTACT_ENTITY_TYPE}'::text AND ct.id IS NOT NULL)
                 OR (u.entity_type = '{_COMPANY_ENTITY_TYPE}'::text AND co.id IS NOT NULL)
                 OR (u.entity_type = '{_LEAD_ENTITY_TYPE}'::text AND ld.id IS NOT NULL)
            ),
            already_present AS (
              SELECT eae.entity_id
              FROM existing_add_entities eae
              INNER JOIN entity_list_members m
                ON m.list_id = $1::uuid
               AND m.entity_id = eae.entity_id
            ),
            to_insert AS (
              SELECT eae.entity_id
              FROM existing_add_entities eae
              LEFT JOIN entity_list_members m
                ON m.list_id = $1::uuid
               AND m.entity_id = eae.entity_id
              WHERE m.entity_id IS NULL
            ),
            inserted AS (
              INSERT INTO entity_list_members (list_id, entity_id)
              SELECT $1::uuid, ti.entity_id
              FROM to_insert ti
              ON CONFLICT (list_id, entity_id) DO NOTHING
              RETURNING entity_id
            ),
            removed AS (
              DELETE FROM entity_list_members m
              USING requested_remove rr
              WHERE m.list_id = $1::uuid
                AND m.entity_id = rr.entity_id
              RETURNING m.entity_id
            ),
            invalid_add_ids AS (
              SELECT ra.entity_id::text AS entity_id
              FROM requested_add ra
              LEFT JOIN existing_add_entities eae
                ON eae.entity_id = ra.entity_id
              WHERE eae.entity_id IS NULL
            ),
            invalid_remove_ids AS (
              SELECT rr.entity_id::text AS entity_id
              FROM requested_remove rr
              LEFT JOIN removed r
                ON r.entity_id = rr.entity_id
              WHERE r.entity_id IS NULL
            )
            SELECT
              u.id::text AS id,
              u.organization_id::text AS organization_id,
              u.entity_type AS entity_type,
              jsonb_build_object(
                'requested', (SELECT COUNT(*) FROM requested_add)::int,
                'added', (SELECT COUNT(*) FROM inserted)::int,
                'removed', 0,
                'already_present', (SELECT COUNT(*) FROM already_present)::int,
                'invalid_ids',
                  COALESCE((SELECT jsonb_agg(entity_id) FROM invalid_add_ids), '[]'::jsonb)
              ) AS add_result,
              jsonb_build_object(
                'requested', (SELECT COUNT(*) FROM requested_remove)::int,
                'added', 0,
                'removed', (SELECT COUNT(*) FROM removed)::int,
                'already_present', 0,
                'invalid_ids',
                  COALESCE((SELECT jsonb_agg(entity_id) FROM invalid_remove_ids), '[]'::jsonb)
              ) AS remove_result
            FROM updated u
            """,
            list_id,
            organization_id,
            update_data.get("name"),
            update_data.get("description"),
            update_data.get("tags"),
            update_data.get("status"),
            add_entity_ids,
            remove_entity_ids,
        )
        return _coerce_json_fields(dict(row), ("add_result", "remove_result")) if row else None

    async def list_lists_with_counts_for_entity_type(
        self,
        *,
        organization_id: str,
        entity_type: EntityType,
        status: EntityListStatus | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List lists for an entity type with derived counters."""
        config = _ENTITY_TYPE_LIST_QUERY_CONFIG.get(entity_type)
        if config is None:
            return [], 0
        query = {
            "organization_id": organization_id,
            "entity_type": entity_type,
            "status": status,
            "search": search,
            "limit": limit,
            "offset": offset,
            **config,
        }
        return await self._list_lists_with_entity_join(query=query)

    async def _list_lists_with_entity_join(
        self,
        *,
        query: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int]:
        """List lists with counts by joining memberships to an entity table."""
        organization_id = str(query["organization_id"])
        entity_type = query["entity_type"]
        entity_table_value = query["entity_table"]
        entity_table = (
            entity_table_value.value
            if isinstance(entity_table_value, EntityTable)
            else str(entity_table_value)
        )
        status = query.get("status")
        search = query.get("search")
        limit = int(query["limit"])
        offset = int(query["offset"])
        has_soft_delete = bool(query.get("has_soft_delete"))
        has_enrichment_fields = bool(query.get("has_enrichment_fields"))

        base_conditions = [
            "el.organization_id = $1::uuid",
            "el.entity_type = $2::text",
        ]
        base_params: list[Any] = [organization_id, entity_type.value]
        optional_conditions = {
            "el.status = ${param}::text": status.value if status is not None else None,
            "el.name ILIKE ${param}::text": f"%{search}%" if search else None,
        }
        where_sql, params, next_param_index = _build_where_sql_and_params(
            base_conditions=base_conditions,
            base_params=base_params,
            optional_conditions=optional_conditions,
        )
        limit_param = next_param_index
        offset_param = next_param_index + 1
        params.extend([limit, offset])

        deleted_filter = (
            f"AND e.status != '{_DELETED_STATUS_VALUE}'::text" if has_soft_delete else ""
        )

        if has_enrichment_fields:
            enrichment_counts_sql = """
              COUNT(e.id) FILTER (
                WHERE COALESCE(e.enrichment_done, FALSE) = TRUE
                  OR LOWER(COALESCE(e.enrichment_status, '')) IN ('enriched', 'done', 'completed')
              ) AS enriched,
              COUNT(e.id) FILTER (
                WHERE LOWER(COALESCE(e.enrichment_status, '')) IN (
                  'pending',
                  'requested',
                  'processing'
                )
              ) AS pending,
              COUNT(e.id) FILTER (
                WHERE LOWER(COALESCE(e.enrichment_status, '')) IN ('failed', 'error')
              ) AS failed
            """.strip()
        else:
            enrichment_counts_sql = "0::int AS enriched, 0::int AS pending, 0::int AS failed"

        rows = await self.db_connection.fetch(
            f"""
            SELECT
              el.id::text AS id,
              el.organization_id::text AS organization_id,
              el.name,
              el.entity_type,
              el.description,
              el.tags,
              el.status,
              el.created_at::text AS created_at,
              el.updated_at::text AS updated_at,
              COUNT(*) OVER() AS total_count,
              COUNT(e.id) AS total_items,
              {enrichment_counts_sql}
            FROM entity_lists el
            LEFT JOIN entity_list_members m
              ON m.list_id = el.id
            LEFT JOIN {entity_table} e
              ON e.id = m.entity_id
             AND e.organization_id = el.organization_id
             {deleted_filter}
            WHERE {where_sql}
            GROUP BY
              el.id,
              el.organization_id,
              el.name,
              el.entity_type,
              el.description,
              el.tags,
              el.status,
              el.created_at,
              el.updated_at
            ORDER BY el.updated_at DESC NULLS LAST, el.created_at DESC
            LIMIT ${limit_param}::int OFFSET ${offset_param}::int
            """,
            *params,
        )
        items = [dict(r) for r in rows]
        total = int(items[0]["total_count"]) if items else 0
        return items, total

    async def list_member_ids(
        self,
        *,
        list_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[str], int]:
        """Return member entity IDs for a list (paginated) and total membership count."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              m.entity_id::text AS entity_id,
              COUNT(*) OVER() AS total_count
            FROM entity_list_members m
            WHERE m.list_id = $1::uuid
            ORDER BY m.created_at DESC
            LIMIT $2::int OFFSET $3::int
            """,
            list_id,
            limit,
            offset,
        )
        entity_ids = [str(r["entity_id"]) for r in rows]
        total = int(rows[0]["total_count"]) if rows else 0
        return entity_ids, total
