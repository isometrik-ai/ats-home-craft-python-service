"""Notice board persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import NOTICE_MAX_PIN_SLOTS, NoticeListStatus

_NOTICE_SELECT_COLUMNS = """
  n.id::text AS id,
  n.organization_id::text AS organization_id,
  n.project_id::text AS project_id,
  n.display_code,
  n.sequence_number,
  n.title,
  n.description,
  n.category::text AS category,
  n.status::text AS status,
  n.scope_type::text AS scope_type,
  n.publish_at,
  n.published_at,
  n.deleted_at,
  n.deleted_reason,
  n.duplicate_of_id::text AS duplicate_of_id,
  n.view_count,
  n.like_count,
  n.created_by_user_id::text AS created_by_user_id,
  n.updated_by_user_id::text AS updated_by_user_id,
  n.created_at,
  n.updated_at,
  np.slot_index AS pin_slot_index,
  np.pin_duration::text AS pin_duration,
  (np.id IS NOT NULL) AS pinned
"""


class NoticesRepository(BaseRepository):
    """Database operations for notice board tables."""

    async def allocate_sequence_number(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> int:
        """Return next monotonic sequence number for a project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence
            FROM notices
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return int(row["next_sequence"])

    async def insert_notice(
        self,
        *,
        organization_id: str,
        project_id: str,
        display_code: str,
        sequence_number: int,
        title: str,
        description: str,
        category: str,
        status: str,
        scope_type: str,
        publish_at: datetime | None,
        published_at: datetime | None,
        duplicate_of_id: str | None,
        created_by_user_id: str | None,
        updated_by_user_id: str | None,
    ) -> dict[str, Any]:
        """Insert notices header row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO notices (
                organization_id,
                project_id,
                display_code,
                sequence_number,
                title,
                description,
                category,
                status,
                scope_type,
                publish_at,
                published_at,
                duplicate_of_id,
                created_by_user_id,
                updated_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6,
                $7::notice_category, $8::notice_status, $9::notice_scope_type,
                $10, $11, $12::uuid, $13::uuid, $14::uuid
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            display_code,
            sequence_number,
            title,
            description,
            category,
            status,
            scope_type,
            publish_at,
            published_at,
            duplicate_of_id,
            created_by_user_id,
            updated_by_user_id,
        )
        return await self.fetch_notice_by_id(
            organization_id=organization_id,
            project_id=project_id,
            notice_id=str(row["id"]),
        )

    async def update_notice_fields(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update notice header fields."""
        if not fields:
            return await self.fetch_notice_by_id(
                organization_id=organization_id,
                project_id=project_id,
                notice_id=notice_id,
            )

        set_parts: list[str] = []
        values: list[Any] = [organization_id, project_id, notice_id]
        idx = 4
        casts = {
            "category": "::notice_category",
            "status": "::notice_status",
            "scope_type": "::notice_scope_type",
        }
        for key, value in fields.items():
            cast = casts.get(key, "")
            set_parts.append(f"{key} = ${idx}{cast}")
            values.append(value)
            idx += 1
        set_parts.append("updated_at = now()")

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE notices n
            SET {", ".join(set_parts)}
            WHERE n.organization_id = $1::uuid
              AND n.project_id = $2::uuid
              AND n.id = $3::uuid
            RETURNING n.id::text AS id
            """,
            *values,
        )
        if row is None:
            return None
        return await self.fetch_notice_by_id(
            organization_id=organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )

    async def fetch_notice_by_id(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one notice with pin state."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
            {_NOTICE_SELECT_COLUMNS}
            FROM notices n
            LEFT JOIN notice_pins np
              ON np.notice_id = n.id
             AND np.is_active = true
            WHERE n.organization_id = $1::uuid
              AND n.project_id = $2::uuid
              AND n.id = $3::uuid
            """,
            organization_id,
            project_id,
            notice_id,
        )
        return dict(row) if row else None

    async def replace_recipients(
        self,
        *,
        organization_id: str,
        notice_id: str,
        recipient_groups: list[str],
    ) -> None:
        """Replace notice recipient groups."""
        await self.db_connection.execute(
            """
            DELETE FROM notice_recipients
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        if not recipient_groups:
            return
        await self.db_connection.executemany(
            """
            INSERT INTO notice_recipients (organization_id, notice_id, recipient_group)
            VALUES ($1::uuid, $2::uuid, $3::notice_recipient_group)
            """,
            [(organization_id, notice_id, group) for group in recipient_groups],
        )

    async def replace_towers(
        self,
        *,
        organization_id: str,
        notice_id: str,
        tower_ids: list[str],
    ) -> None:
        """Replace notice tower scope rows."""
        await self.db_connection.execute(
            """
            DELETE FROM notice_towers
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        if not tower_ids:
            return
        await self.db_connection.executemany(
            """
            INSERT INTO notice_towers (organization_id, notice_id, tower_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            """,
            [(organization_id, notice_id, tower_id) for tower_id in tower_ids],
        )

    async def replace_attachments(
        self,
        *,
        organization_id: str,
        notice_id: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        """Replace notice attachment rows."""
        await self.db_connection.execute(
            """
            DELETE FROM notice_attachments
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        if not attachments:
            return
        await self.db_connection.executemany(
            """
            INSERT INTO notice_attachments (
                organization_id,
                notice_id,
                file_path,
                file_name,
                mime_type,
                size_bytes,
                sort_order
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
            """,
            [
                (
                    organization_id,
                    notice_id,
                    item["file_path"],
                    item.get("file_name"),
                    item["mime_type"],
                    item["size_bytes"],
                    item["sort_order"],
                )
                for item in attachments
            ],
        )

    async def list_recipient_groups(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> list[str]:
        """Return recipient groups for a notice."""
        rows = await self.db_connection.fetch(
            """
            SELECT recipient_group::text AS recipient_group
            FROM notice_recipients
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
            ORDER BY recipient_group
            """,
            organization_id,
            notice_id,
        )
        return [str(row["recipient_group"]) for row in rows]

    async def list_towers_for_notice(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> list[dict[str, Any]]:
        """Return tower ids and names for a notice."""
        rows = await self.db_connection.fetch(
            """
            SELECT nt.tower_id::text AS tower_id, t.name AS tower_name
            FROM notice_towers nt
            JOIN towers t
              ON t.id = nt.tower_id
             AND t.organization_id = nt.organization_id
            WHERE nt.organization_id = $1::uuid
              AND nt.notice_id = $2::uuid
            ORDER BY t.name
            """,
            organization_id,
            notice_id,
        )
        return [dict(row) for row in rows]

    async def list_attachments(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> list[dict[str, Any]]:
        """Return attachments for a notice."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              file_path,
              file_name,
              mime_type,
              size_bytes,
              sort_order
            FROM notice_attachments
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            notice_id,
        )
        return [dict(row) for row in rows]

    async def get_summary_counts(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """Return tab counts and live-by-group counts."""
        status_row = await self.db_connection.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE status <> 'deleted')::int AS all_count,
              COUNT(*) FILTER (WHERE status = 'live')::int AS live_count,
              COUNT(*) FILTER (WHERE status = 'scheduled')::int AS scheduled_count,
              COUNT(*) FILTER (WHERE status = 'deleted')::int AS deleted_count
            FROM notices
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        group_rows = await self.db_connection.fetch(
            """
            SELECT nr.recipient_group::text AS recipient_group, COUNT(DISTINCT n.id)::int AS cnt
            FROM notices n
            JOIN notice_recipients nr
              ON nr.notice_id = n.id
             AND nr.organization_id = n.organization_id
            WHERE n.organization_id = $1::uuid
              AND n.project_id = $2::uuid
              AND n.status = 'live'
            GROUP BY nr.recipient_group
            """,
            organization_id,
            project_id,
        )
        live_by_group = {str(row["recipient_group"]): int(row["cnt"]) for row in group_rows}
        return {
            "all": int(status_row["all_count"]),
            "live": int(status_row["live_count"]),
            "scheduled": int(status_row["scheduled_count"]),
            "deleted": int(status_row["deleted_count"]),
            "live_by_group": live_by_group,
        }

    async def list_notices(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: NoticeListStatus,
        group: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List notices for admin with filters."""
        conditions = ["n.organization_id = $1::uuid", "n.project_id = $2::uuid"]
        values: list[Any] = [organization_id, project_id]
        idx = 3

        if status == NoticeListStatus.ALL:
            conditions.append("n.status <> 'deleted'")
        elif status == NoticeListStatus.LIVE:
            conditions.append("n.status = 'live'")
        elif status == NoticeListStatus.SCHEDULED:
            conditions.append("n.status = 'scheduled'")
        elif status == NoticeListStatus.DELETED:
            conditions.append("n.status = 'deleted'")

        if group:
            conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM notice_recipients nr
                    WHERE nr.notice_id = n.id
                      AND nr.organization_id = n.organization_id
                      AND nr.recipient_group = ${idx}::notice_recipient_group
                )
                """
            )
            values.append(group)
            idx += 1

        if search:
            conditions.append(f"n.title ILIKE ${idx}")
            values.append(f"%{search}%")
            idx += 1

        where_sql = " AND ".join(conditions)

        if status == NoticeListStatus.LIVE:
            order_sql = """
            ORDER BY
              CASE WHEN np.id IS NOT NULL THEN 0 ELSE 1 END,
              n.published_at DESC NULLS LAST,
              n.created_at DESC
            """
        elif status == NoticeListStatus.SCHEDULED:
            order_sql = "ORDER BY n.publish_at ASC NULLS LAST, n.created_at DESC"
        elif status == NoticeListStatus.DELETED:
            order_sql = "ORDER BY n.deleted_at DESC NULLS LAST, n.created_at DESC"
        else:
            order_sql = "ORDER BY n.updated_at DESC, n.created_at DESC"

        total_row = await self.db_connection.fetchrow(
            f"""
            SELECT COUNT(*)::int AS total
            FROM notices n
            WHERE {where_sql}
            """,
            *values,
        )
        total = int(total_row["total"])

        rows = await self.db_connection.fetch(
            f"""
            SELECT
            {_NOTICE_SELECT_COLUMNS},
            COALESCE(
              (
                SELECT array_agg(nr.recipient_group::text ORDER BY nr.recipient_group)
                FROM notice_recipients nr
                WHERE nr.notice_id = n.id
                  AND nr.organization_id = n.organization_id
              ),
              ARRAY[]::text[]
            ) AS recipient_groups,
            (
              SELECT string_agg(t.name, ', ' ORDER BY t.name)
              FROM notice_towers nt
              JOIN towers t ON t.id = nt.tower_id AND t.organization_id = nt.organization_id
              WHERE nt.notice_id = n.id AND nt.organization_id = n.organization_id
            ) AS scope_label
            FROM notices n
            LEFT JOIN notice_pins np
              ON np.notice_id = n.id
             AND np.is_active = true
            WHERE {where_sql}
            {order_sql}
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], total

    async def deactivate_pins_for_notice(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> None:
        """Unpin a notice."""
        await self.db_connection.execute(
            """
            UPDATE notice_pins
            SET is_active = false, unpinned_at = now()
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
              AND is_active = true
            """,
            organization_id,
            notice_id,
        )

    async def get_active_pin_for_notice(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> dict[str, Any] | None:
        """Return active pin row for a notice."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              id::text AS id,
              project_id::text AS project_id,
              slot_index,
              pin_duration::text AS pin_duration,
              pinned_at,
              expires_at
            FROM notice_pins
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
              AND is_active = true
            """,
            organization_id,
            notice_id,
        )
        return dict(row) if row else None

    async def get_active_pin_for_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_index: int,
    ) -> dict[str, Any] | None:
        """Return active pin occupying a slot."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              np.id::text AS id,
              np.notice_id::text AS notice_id,
              np.slot_index,
              n.display_code,
              n.title
            FROM notice_pins np
            JOIN notices n ON n.id = np.notice_id
            WHERE np.organization_id = $1::uuid
              AND np.project_id = $2::uuid
              AND np.slot_index = $3
              AND np.is_active = true
            """,
            organization_id,
            project_id,
            slot_index,
        )
        return dict(row) if row else None

    async def count_active_pins(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> int:
        """Count active banner pins for a project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COUNT(*)::int AS cnt
            FROM notice_pins
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND is_active = true
            """,
            organization_id,
            project_id,
        )
        return int(row["cnt"])

    async def list_active_pins_with_notices(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Return active pins joined to notice headers."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              np.slot_index,
              n.id::text AS notice_id,
              n.display_code,
              n.title,
              n.category::text AS category
            FROM notice_pins np
            JOIN notices n
              ON n.id = np.notice_id
             AND n.organization_id = np.organization_id
            WHERE np.organization_id = $1::uuid
              AND np.project_id = $2::uuid
              AND np.is_active = true
            ORDER BY np.slot_index
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def deactivate_pin_on_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_index: int,
    ) -> None:
        """Deactivate any active pin on a slot."""
        await self.db_connection.execute(
            """
            UPDATE notice_pins
            SET is_active = false, unpinned_at = now()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND slot_index = $3
              AND is_active = true
            """,
            organization_id,
            project_id,
            slot_index,
        )

    async def insert_pin(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_id: str,
        slot_index: int,
        pin_duration: str,
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        """Insert an active banner pin."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO notice_pins (
                organization_id,
                project_id,
                notice_id,
                slot_index,
                pin_duration,
                expires_at,
                is_active
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::notice_pin_duration, $6, true)
            RETURNING
              id::text AS id,
              slot_index,
              pin_duration::text AS pin_duration,
              pinned_at,
              expires_at
            """,
            organization_id,
            project_id,
            notice_id,
            slot_index,
            pin_duration,
            expires_at,
        )
        return dict(row)

    async def find_first_free_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> int | None:
        """Return lowest free slot index 1-6."""
        occupied = await self.db_connection.fetch(
            """
            SELECT slot_index
            FROM notice_pins
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND is_active = true
            """,
            organization_id,
            project_id,
        )
        used = {int(row["slot_index"]) for row in occupied}
        for slot in range(1, NOTICE_MAX_PIN_SLOTS + 1):
            if slot not in used:
                return slot
        return None

    async def publish_due_scheduled_notices(
        self,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        """Promote scheduled notices whose publish_at has passed."""
        conditions = ["status = 'scheduled'", "publish_at <= now()"]
        values: list[Any] = []
        idx = 1
        if organization_id:
            conditions.append(f"organization_id = ${idx}::uuid")
            values.append(organization_id)
            idx += 1
        if project_id:
            conditions.append(f"project_id = ${idx}::uuid")
            values.append(project_id)
            idx += 1

        rows = await self.db_connection.fetch(
            f"""
            UPDATE notices
            SET status = 'live',
                published_at = now(),
                updated_at = now()
            WHERE {" AND ".join(conditions)}
            RETURNING id::text AS id
            """,
            *values,
        )
        return [str(row["id"]) for row in rows]

    async def expire_due_pins(self) -> int:
        """Deactivate timed banner pins past expiry."""
        result = await self.db_connection.execute(
            """
            UPDATE notice_pins
            SET is_active = false, unpinned_at = now()
            WHERE is_active = true
              AND expires_at IS NOT NULL
              AND expires_at <= now()
            """
        )
        return int(result.split()[-1]) if result else 0

    async def increment_view_count(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> None:
        """Increment denormalized view count."""
        await self.db_connection.execute(
            """
            UPDATE notices
            SET view_count = view_count + 1, updated_at = now()
            WHERE organization_id = $1::uuid AND id = $2::uuid
            """,
            organization_id,
            notice_id,
        )

    async def upsert_like(
        self,
        *,
        organization_id: str,
        notice_id: str,
        contact_id: str,
    ) -> bool:
        """Insert like if absent; return True if inserted."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO notice_likes (organization_id, notice_id, contact_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            ON CONFLICT (notice_id, contact_id) DO NOTHING
            RETURNING id
            """,
            organization_id,
            notice_id,
            contact_id,
        )
        if row is None:
            return False
        await self.db_connection.execute(
            """
            UPDATE notices
            SET like_count = like_count + 1, updated_at = now()
            WHERE organization_id = $1::uuid AND id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        return True

    async def delete_like(
        self,
        *,
        organization_id: str,
        notice_id: str,
        contact_id: str,
    ) -> bool:
        """Remove like if present; return True if deleted."""
        row = await self.db_connection.fetchrow(
            """
            DELETE FROM notice_likes
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
              AND contact_id = $3::uuid
            RETURNING id
            """,
            organization_id,
            notice_id,
            contact_id,
        )
        if row is None:
            return False
        await self.db_connection.execute(
            """
            UPDATE notices
            SET like_count = GREATEST(like_count - 1, 0), updated_at = now()
            WHERE organization_id = $1::uuid AND id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        return True

    async def contact_has_liked(
        self,
        *,
        organization_id: str,
        notice_id: str,
        contact_id: str,
    ) -> bool:
        """Return whether contact liked a notice."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM notice_likes
            WHERE organization_id = $1::uuid
              AND notice_id = $2::uuid
              AND contact_id = $3::uuid
            """,
            organization_id,
            notice_id,
            contact_id,
        )
        return row is not None

    async def list_live_notices_for_resident(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_ids: list[str] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List live notices optionally filtered to visible ids."""
        if notice_ids is not None and not notice_ids:
            return [], 0

        conditions = [
            "n.organization_id = $1::uuid",
            "n.project_id = $2::uuid",
            "n.status = 'live'",
        ]
        values: list[Any] = [organization_id, project_id]
        idx = 3
        if notice_ids is not None:
            conditions.append(f"n.id = ANY(${idx}::uuid[])")
            values.append(notice_ids)
            idx += 1

        where_sql = " AND ".join(conditions)
        total_row = await self.db_connection.fetchrow(
            f"SELECT COUNT(*)::int AS total FROM notices n WHERE {where_sql}",
            *values,
        )
        total = int(total_row["total"])

        rows = await self.db_connection.fetch(
            f"""
            SELECT
            {_NOTICE_SELECT_COLUMNS}
            FROM notices n
            LEFT JOIN notice_pins np
              ON np.notice_id = n.id
             AND np.is_active = true
            WHERE {where_sql}
            ORDER BY
              CASE WHEN np.id IS NOT NULL THEN 0 ELSE 1 END,
              n.published_at DESC NULLS LAST
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], total

    async def fetch_notice_for_resident(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> dict[str, Any] | None:
        """Fetch live notice by id for resident detail."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
            {_NOTICE_SELECT_COLUMNS},
            (
              SELECT string_agg(t.name, ', ' ORDER BY t.name)
              FROM notice_towers nt
              JOIN towers t ON t.id = nt.tower_id AND t.organization_id = nt.organization_id
              WHERE nt.notice_id = n.id AND nt.organization_id = n.organization_id
            ) AS scope_label
            FROM notices n
            LEFT JOIN notice_pins np
              ON np.notice_id = n.id
             AND np.is_active = true
            WHERE n.organization_id = $1::uuid
              AND n.id = $2::uuid
              AND n.status = 'live'
            """,
            organization_id,
            notice_id,
        )
        return dict(row) if row else None

    async def fetch_notice_visibility_context(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> dict[str, Any] | None:
        """Fetch notice fields needed for resident visibility checks."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              n.id::text AS id,
              n.project_id::text AS project_id,
              n.status::text AS status,
              n.scope_type::text AS scope_type,
              COALESCE(
                (
                  SELECT array_agg(nr.recipient_group::text)
                  FROM notice_recipients nr
                  WHERE nr.notice_id = n.id
                ),
                ARRAY[]::text[]
              ) AS recipient_groups,
              COALESCE(
                (
                  SELECT array_agg(nt.tower_id::text)
                  FROM notice_towers nt
                  WHERE nt.notice_id = n.id
                ),
                ARRAY[]::text[]
              ) AS tower_ids
            FROM notices n
            WHERE n.organization_id = $1::uuid
              AND n.id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        return dict(row) if row else None

    async def list_live_notice_ids_for_project(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> list[str]:
        """Return all live notice ids for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT id::text AS id
            FROM notices
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND status = 'live'
            """,
            organization_id,
            project_id,
        )
        return [str(row["id"]) for row in rows]

    async def fetch_notice_by_id_only(
        self,
        *,
        organization_id: str,
        notice_id: str,
    ) -> dict[str, Any] | None:
        """Fetch notice without project filter (for duplicate source)."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
            {_NOTICE_SELECT_COLUMNS}
            FROM notices n
            LEFT JOIN notice_pins np
              ON np.notice_id = n.id
             AND np.is_active = true
            WHERE n.organization_id = $1::uuid
              AND n.id = $2::uuid
            """,
            organization_id,
            notice_id,
        )
        return dict(row) if row else None

    async def soft_delete_notice(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_id: str,
        reason: str | None,
        updated_by_user_id: str | None,
    ) -> dict[str, Any] | None:
        """Soft delete notice and unpin."""
        await self.deactivate_pins_for_notice(
            organization_id=organization_id,
            notice_id=notice_id,
        )
        row = await self.db_connection.fetchrow(
            """
            UPDATE notices
            SET status = 'deleted',
                deleted_at = now(),
                deleted_reason = $4,
                updated_by_user_id = $5::uuid,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            notice_id,
            reason,
            updated_by_user_id,
        )
        if row is None:
            return None
        return await self.fetch_notice_by_id(
            organization_id=organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )

    async def list_notices_published_since(
        self,
        *,
        notice_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch notices by ids for push dispatch."""
        if not notice_ids:
            return []
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              title,
              category::text AS category
            FROM notices
            WHERE id = ANY($1::uuid[])
            """,
            notice_ids,
        )
        return [dict(row) for row in rows]
