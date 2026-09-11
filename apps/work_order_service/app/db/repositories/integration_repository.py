"""Integration tables: audit events, triggers, API keys, webhook deliveries."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.utils.records import jsonb_bind, record_to_dict


class IntegrationRepository:
    """Repository for work_order integration tables."""

    def __init__(self, conn) -> None:
        """init  ."""
        self.conn = conn

    # --- audit_events ---

    async def insert_audit_event(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert audit event."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.audit_events (
                organization_id, project_id, entity, entity_id, entity_label,
                action, actor_name, actor_user_id, source, changes, snapshot
            ) VALUES (
                $1::uuid, $2::uuid,
                $3::work_order.work_order_trigger_entity, $4::uuid, $5,
                $6::work_order.work_order_audit_action, $7, $8::uuid,
                COALESCE($9::work_order.work_order_audit_source, 'fm'),
                COALESCE($10::jsonb, '[]'::jsonb),
                COALESCE($11::jsonb, '{}'::jsonb)
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["entity"],
            data["entity_id"],
            data.get("entity_label"),
            data["action"],
            data.get("actor_name"),
            data.get("actor_user_id"),
            data.get("source", "fm"),
            jsonb_bind(data.get("changes")),
            jsonb_bind(data.get("snapshot")),
        )
        return record_to_dict(row)

    async def list_audit_events(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        entity: str | None = None,
        entity_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List audit events."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        filters = ""
        if entity:
            params.append(entity)
            filters += f" AND entity = ${len(params)}::work_order.work_order_trigger_entity"
        if entity_id:
            params.append(entity_id)
            filters += f" AND entity_id = ${len(params)}::uuid"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.audit_events
            WHERE organization_id = $1::uuid AND project_id = $2::uuid{filters}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.audit_events
            WHERE organization_id = $1::uuid AND project_id = $2::uuid{filters}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    # --- trigger_configs ---

    async def get_trigger(
        self, *, entity_id: str, organization_id: str, project_id: str
    ) -> dict[str, Any] | None:
        """Get trigger."""
        row = await self.conn.fetchrow(
            """
            SELECT * FROM work_order.trigger_configs
            WHERE id = $1::uuid AND organization_id = $2::uuid
              AND project_id = $3::uuid AND record_status = 'active'
            """,
            entity_id,
            organization_id,
            project_id,
        )
        return record_to_dict(row) if row else None

    async def list_triggers(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """List triggers."""
        rows = await self.conn.fetch(
            """
            SELECT * FROM work_order.trigger_configs
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'
            ORDER BY name
            """,
            organization_id,
            project_id,
        )
        return [record_to_dict(r) for r in rows]

    async def list_matching_triggers(
        self, *, organization_id: str, project_id: str, entity: str, event: str
    ) -> list[dict[str, Any]]:
        """List matching triggers."""
        rows = await self.conn.fetch(
            """
            SELECT * FROM work_order.trigger_configs
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND entity = $3::work_order.work_order_trigger_entity
              AND event = $4::work_order.work_order_trigger_event
              AND is_active = true AND record_status = 'active'
            """,
            organization_id,
            project_id,
            entity,
            event,
        )
        return [record_to_dict(r) for r in rows]

    async def create_trigger(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create trigger."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.trigger_configs (
                organization_id, project_id, name, entity, event,
                is_active, webhook_url, secret
            ) VALUES (
                $1::uuid, $2::uuid, $3,
                $4::work_order.work_order_trigger_entity,
                $5::work_order.work_order_trigger_event,
                COALESCE($6, true), $7, $8
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["name"],
            data["entity"],
            data["event"],
            data.get("is_active", True),
            data["webhook_url"],
            data.get("secret"),
        )
        return record_to_dict(row)

    async def update_trigger(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update trigger."""
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.trigger_configs
            SET name = COALESCE($4, name),
                entity = COALESCE($5::work_order.work_order_trigger_entity, entity),
                event = COALESCE($6::work_order.work_order_trigger_event, event),
                is_active = COALESCE($7, is_active),
                webhook_url = COALESCE($8, webhook_url),
                secret = COALESCE($9, secret),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("name"),
            data.get("entity"),
            data.get("event"),
            data.get("is_active"),
            data.get("webhook_url"),
            data.get("secret"),
        )
        return record_to_dict(row) if row else None

    async def soft_delete_trigger(
        self, *, entity_id: str, organization_id: str, project_id: str
    ) -> bool:
        """Soft delete trigger."""
        result = await self.conn.execute(
            """
            UPDATE work_order.trigger_configs
            SET record_status = 'deleted', deleted_at = now(), updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            """,
            entity_id,
            organization_id,
            project_id,
        )
        return result.endswith("1")

    # --- api_keys ---

    async def list_api_keys(
        self, *, organization_id: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List api keys."""
        if project_id:
            rows = await self.conn.fetch(
                """
                SELECT id, organization_id, project_id, name, key_prefix,
                       last_used_at, created_at, updated_at
                FROM work_order.api_keys
                WHERE organization_id = $1::uuid AND project_id = $2::uuid
                  AND record_status = 'active'
                ORDER BY created_at DESC
                """,
                organization_id,
                project_id,
            )
        else:
            rows = await self.conn.fetch(
                """
                SELECT id, organization_id, project_id, name, key_prefix,
                       last_used_at, created_at, updated_at
                FROM work_order.api_keys
                WHERE organization_id = $1::uuid AND record_status = 'active'
                ORDER BY created_at DESC
                """,
                organization_id,
            )
        return [record_to_dict(r) for r in rows]

    async def create_api_key(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create api key."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.api_keys (
                organization_id, project_id, name, key_prefix, key_hash
            ) VALUES ($1::uuid, $2::uuid, $3, $4, $5)
            RETURNING id, organization_id, project_id, name, key_prefix, created_at
            """,
            data["organization_id"],
            data.get("project_id"),
            data["name"],
            data["key_prefix"],
            data["key_hash"],
        )
        return record_to_dict(row)

    async def get_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Get api key by hash."""
        row = await self.conn.fetchrow(
            """
            SELECT * FROM work_order.api_keys
            WHERE key_hash = $1 AND record_status = 'active'
            LIMIT 1
            """,
            key_hash,
        )
        return record_to_dict(row) if row else None

    async def soft_delete_api_key(
        self, *, entity_id: str, organization_id: str, project_id: str | None
    ) -> bool:
        """Soft delete api key."""
        if project_id:
            result = await self.conn.execute(
                """
                UPDATE work_order.api_keys
                SET record_status = 'deleted', deleted_at = now(), updated_at = now()
                WHERE id = $1::uuid AND organization_id = $2::uuid
                  AND project_id = $3::uuid AND record_status = 'active'
                """,
                entity_id,
                organization_id,
                project_id,
            )
        else:
            result = await self.conn.execute(
                """
                UPDATE work_order.api_keys
                SET record_status = 'deleted', deleted_at = now(), updated_at = now()
                WHERE id = $1::uuid AND organization_id = $2::uuid
                  AND record_status = 'active'
                """,
                entity_id,
                organization_id,
            )
        return result.endswith("1")

    async def touch_api_key_last_used(self, entity_id: str) -> None:
        """Touch api key last used."""
        await self.conn.execute(
            """
            UPDATE work_order.api_keys SET last_used_at = now() WHERE id = $1::uuid
            """,
            entity_id,
        )

    # --- webhook_deliveries ---

    async def insert_webhook_delivery(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert webhook delivery."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.webhook_deliveries (
                organization_id, project_id, trigger_id, entity, entity_id, event,
                request_payload, response_status, error, attempt, duration_ms, delivered
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid,
                $4::work_order.work_order_trigger_entity, $5::uuid, $6,
                COALESCE($7::jsonb, '{}'::jsonb), $8, $9, COALESCE($10, 1),
                COALESCE($11, 0), COALESCE($12, false)
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data.get("trigger_id"),
            data.get("entity"),
            data.get("entity_id"),
            data["event"],
            jsonb_bind(data.get("request_payload")),
            data.get("response_status"),
            data.get("error"),
            data.get("attempt", 1),
            data.get("duration_ms", 0),
            data.get("delivered", False),
        )
        return record_to_dict(row)

    async def update_webhook_delivery(
        self,
        delivery_id: str,
        *,
        response_status: int | None,
        error: str | None,
        attempt: int,
        duration_ms: int,
        delivered: bool,
    ) -> None:
        """Update a pending webhook delivery with the HTTP result."""
        await self.conn.execute(
            """
            UPDATE work_order.webhook_deliveries
            SET response_status = $2,
                error = $3,
                attempt = $4,
                duration_ms = $5,
                delivered = $6
            WHERE id = $1::uuid
            """,
            delivery_id,
            response_status,
            error,
            attempt,
            duration_ms,
            delivered,
        )

    async def list_pending_webhook_deliveries(
        self,
        *,
        limit: int = 100,
        after_id: str | None = None,
        max_attempts: int = 3,
    ) -> list[dict[str, Any]]:
        """Return undelivered webhook rows for worker recovery."""
        rows = await self.conn.fetch(
            """
            SELECT d.*, t.webhook_url, t.secret
            FROM work_order.webhook_deliveries d
            LEFT JOIN work_order.trigger_configs t ON t.id = d.trigger_id
            WHERE d.delivered = false
              AND d.attempt < $2
              AND ($3::uuid IS NULL OR d.id > $3::uuid)
            ORDER BY d.id ASC
            LIMIT $1
            """,
            limit,
            max_attempts,
            after_id,
        )
        return [record_to_dict(row) for row in rows]

    async def list_webhook_deliveries(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List webhook deliveries."""
        offset = (page - 1) * page_size
        total = await self.conn.fetchval(
            """
            SELECT COUNT(*) FROM work_order.webhook_deliveries
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        rows = await self.conn.fetch(
            """
            SELECT * FROM work_order.webhook_deliveries
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
            """,
            organization_id,
            project_id,
            page_size,
            offset,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)
