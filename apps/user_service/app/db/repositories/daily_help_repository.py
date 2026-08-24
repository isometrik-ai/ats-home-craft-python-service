"""Daily help profile persistence."""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_PASSCODE_MAX_ATTEMPTS = 50

_PROFILE_SELECT_COLUMNS = """
  p.id::text AS id,
  p.organization_id::text AS organization_id,
  p.project_id::text AS project_id,
  p.initials,
  p.first_name,
  p.middle_name,
  p.last_name,
  p.display_name,
  p.phone_isd_code,
  p.phone_number,
  p.alternate_phone_isd_code,
  p.alternate_phone_number,
  p.category_id::text AS category_id,
  c.name AS category_name,
  p.gender,
  p.date_of_birth,
  p.photo_path,
  p.gate_passcode,
  p.status::text AS status,
  p.open_to_work,
  p.linked_pass_id::text AS linked_pass_id,
  p.created_by_user_id::text AS created_by_user_id,
  p.submitted_by_user_id::text AS submitted_by_user_id,
  p.reviewed_by_user_id::text AS reviewed_by_user_id,
  p.reviewed_at,
  p.rejection_reason,
  p.updated_by_user_id::text AS updated_by_user_id,
  p.deleted_at,
  p.created_at,
  p.updated_at
"""

_PROFILE_FROM_SQL = """
FROM daily_help_profiles p
JOIN daily_help_categories c
  ON c.id = p.category_id
 AND c.organization_id = p.organization_id
 AND c.project_id = p.project_id
"""

_PROFILE_LIST_DOC_COUNT = """
  (SELECT COUNT(*)::int
     FROM daily_help_documents d
    WHERE d.organization_id = p.organization_id
      AND d.daily_help_profile_id = p.id
  ) AS document_count
"""

_PROFILE_LIST_HOUSE_COUNT = """
  (SELECT COUNT(*)::int
     FROM daily_help_household_links hl
    WHERE hl.organization_id = p.organization_id
      AND hl.daily_help_profile_id = p.id
      AND hl.status = 'active'::daily_help_household_link_status
  ) AS household_link_count
"""


class DailyHelpRepository(BaseRepository):
    """Database operations for daily help registry tables."""

    @staticmethod
    def _random_passcode() -> str:
        """Generate a 4-digit numeric gate passcode."""
        return f"{secrets.randbelow(10_000):04d}"

    async def generate_unique_passcode(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> str:
        """Generate a gate passcode unique within the project."""
        for _ in range(_PASSCODE_MAX_ATTEMPTS):
            code = self._random_passcode()
            exists = await self.db_connection.fetchval(
                """
                SELECT 1
                FROM daily_help_profiles
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                  AND gate_passcode = $3
                LIMIT 1
                """,
                organization_id,
                project_id,
                code,
            )
            if not exists:
                return code
        raise RuntimeError("daily help passcode generation exhausted retries")

    async def link_pass_id(
        self,
        *,
        organization_id: str,
        project_id: str,
        profile_id: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Set linked_pass_id on a profile."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE daily_help_profiles
            SET linked_pass_id = $4::uuid,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            RETURNING id::text AS id, linked_pass_id::text AS linked_pass_id
            """,
            organization_id,
            project_id,
            profile_id,
            pass_id,
        )
        return dict(row) if row else None

    async def insert_profile(
        self,
        *,
        organization_id: str,
        project_id: str,
        initials: str | None,
        first_name: str,
        middle_name: str | None,
        last_name: str,
        display_name: str,
        phone_isd_code: str,
        phone_number: str,
        alternate_phone_isd_code: str | None,
        alternate_phone_number: str | None,
        category_id: str,
        gender: str | None,
        date_of_birth: date | None,
        photo_path: str | None,
        gate_passcode: str | None,
        status: str,
        open_to_work: bool = False,
        created_by_user_id: str | None = None,
        submitted_by_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a daily_help_profiles row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_profiles (
                organization_id,
                project_id,
                initials,
                first_name,
                middle_name,
                last_name,
                display_name,
                phone_isd_code,
                phone_number,
                alternate_phone_isd_code,
                alternate_phone_number,
                category_id,
                gender,
                date_of_birth,
                photo_path,
                gate_passcode,
                status,
                open_to_work,
                created_by_user_id,
                submitted_by_user_id,
                updated_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12::uuid, $13, $14::date, $15, $16,
                $17::daily_help_status, $18, $19::uuid, $20::uuid, $19::uuid
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            initials,
            first_name,
            middle_name,
            last_name,
            display_name,
            phone_isd_code,
            phone_number,
            alternate_phone_isd_code,
            alternate_phone_number,
            category_id,
            gender,
            date_of_birth,
            photo_path,
            gate_passcode,
            status,
            open_to_work,
            created_by_user_id,
            submitted_by_user_id,
        )
        return await self.get_profile(
            organization_id=organization_id,
            project_id=project_id,
            profile_id=str(row["id"]),
        )

    async def update_profile(
        self,
        *,
        organization_id: str,
        project_id: str,
        profile_id: str,
        fields: dict[str, Any],
        updated_by_user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch profile columns."""
        if not fields and updated_by_user_id is None:
            return await self.get_profile(
                organization_id=organization_id,
                project_id=project_id,
                profile_id=profile_id,
            )

        set_parts: list[str] = []
        values: list[Any] = [organization_id, project_id, profile_id]
        idx = 4
        casts = {
            "category_id": "::uuid",
            "date_of_birth": "::date",
            "status": "::daily_help_status",
            "deleted_at": "::timestamptz",
            "linked_pass_id": "::uuid",
            "reviewed_by_user_id": "::uuid",
            "reviewed_at": "::timestamptz",
            "submitted_by_user_id": "::uuid",
        }
        for key, value in fields.items():
            cast = casts.get(key, "")
            set_parts.append(f"{key} = ${idx}{cast}")
            values.append(value)
            idx += 1
        if updated_by_user_id is not None:
            set_parts.append(f"updated_by_user_id = ${idx}::uuid")
            values.append(updated_by_user_id)
            idx += 1
        set_parts.append("updated_at = now()")

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE daily_help_profiles p
            SET {", ".join(set_parts)}
            WHERE p.organization_id = $1::uuid
              AND p.project_id = $2::uuid
              AND p.id = $3::uuid
            RETURNING p.id::text AS id
            """,
            *values,
        )
        if row is None:
            return None
        return await self.get_profile(
            organization_id=organization_id,
            project_id=project_id,
            profile_id=profile_id,
        )

    async def get_profile(
        self,
        *,
        organization_id: str,
        project_id: str,
        profile_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one profile with category name."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
            {_PROFILE_SELECT_COLUMNS},
            {_PROFILE_LIST_DOC_COUNT},
            {_PROFILE_LIST_HOUSE_COUNT}
            {_PROFILE_FROM_SQL}
            WHERE p.organization_id = $1::uuid
              AND p.project_id = $2::uuid
              AND p.id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            project_id,
            profile_id,
        )
        return dict(row) if row else None

    @staticmethod
    def _profile_list_where(
        *,
        organization_id: str,
        project_id: str,
        status: str | None = None,
        category_id: str | None = None,
        search: str | None = None,
        submitted_by_user_id: str | None = None,
    ) -> tuple[str, list[Any]]:
        """Build WHERE clause and args shared by profile list and link aggregates."""
        filters = ["p.organization_id = $1::uuid", "p.project_id = $2::uuid"]
        args: list[Any] = [organization_id, project_id]
        if submitted_by_user_id:
            args.append(submitted_by_user_id)
            filters.append(f"p.submitted_by_user_id = ${len(args)}::uuid")
        if status:
            args.append(status)
            filters.append(f"p.status = ${len(args)}::daily_help_status")
        if category_id:
            args.append(category_id)
            filters.append(f"p.category_id = ${len(args)}::uuid")
        if search:
            args.append(f"%{search.strip()}%")
            idx = len(args)
            filters.append(
                f"(p.display_name ILIKE ${idx} OR p.phone_number ILIKE ${idx} "
                f"OR p.gate_passcode ILIKE ${idx})"
            )
        return " AND ".join(filters), args

    async def list_profiles(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: str | None = None,
        category_id: str | None = None,
        search: str | None = None,
        submitted_by_user_id: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated admin/resident list with optional filters."""
        where_sql, args = self._profile_list_where(
            organization_id=organization_id,
            project_id=project_id,
            status=status,
            category_id=category_id,
            search=search,
            submitted_by_user_id=submitted_by_user_id,
        )

        count = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)
            {_PROFILE_FROM_SQL}
            WHERE {where_sql}
            """,
            *args,
        )
        list_args = [*args, limit, offset]
        rows = await self.db_connection.fetch(
            f"""
            SELECT
            {_PROFILE_SELECT_COLUMNS},
            {_PROFILE_LIST_DOC_COUNT},
            {_PROFILE_LIST_HOUSE_COUNT}
            {_PROFILE_FROM_SQL}
            WHERE {where_sql}
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ${len(list_args) - 1}
            OFFSET ${len(list_args)}
            """,
            *list_args,
        )
        return [dict(row) for row in rows], int(count or 0)

    async def get_summary(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> dict[str, int]:
        """Dashboard summary card counts for one project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE status = 'pending_approval') AS pending_approval,
              COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
              COUNT(*) FILTER (WHERE status = 'active') AS active,
              COUNT(*) FILTER (WHERE status = 'inactive') AS inactive,
              COUNT(*) FILTER (WHERE status = 'deleted') AS deleted
            FROM daily_help_profiles
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        if not row:
            return {
                "total": 0,
                "pending_approval": 0,
                "rejected": 0,
                "active": 0,
                "inactive": 0,
                "deleted": 0,
            }
        return {key: int(row[key] or 0) for key in row.keys()}

    async def insert_document(
        self,
        *,
        organization_id: str,
        profile_id: str,
        document_type: str,
        label: str | None,
        file_path: str,
        file_name: str | None,
        mime_type: str | None = None,
        file_size_bytes: int | None = None,
        sort_order: int = 0,
        uploaded_by_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert one daily_help_documents row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_documents (
                organization_id,
                daily_help_profile_id,
                document_type,
                label,
                file_path,
                file_name,
                mime_type,
                file_size_bytes,
                sort_order,
                uploaded_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3::daily_help_document_type,
                $4, $5, $6, $7, $8, $9, $10::uuid
            )
            RETURNING
              id::text AS id,
              document_type::text AS document_type,
              label,
              file_path,
              file_name,
              mime_type,
              file_size_bytes,
              sort_order,
              created_at,
              updated_at
            """,
            organization_id,
            profile_id,
            document_type,
            label,
            file_path,
            file_name,
            mime_type,
            file_size_bytes,
            sort_order,
            uploaded_by_user_id,
        )
        return dict(row)

    async def list_documents(
        self,
        *,
        organization_id: str,
        profile_id: str,
    ) -> list[dict[str, Any]]:
        """List documents for a profile ordered by sort_order."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              document_type::text AS document_type,
              label,
              file_path,
              file_name,
              mime_type,
              file_size_bytes,
              sort_order,
              uploaded_by_user_id::text AS uploaded_by_user_id,
              created_at,
              updated_at
            FROM daily_help_documents
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
            ORDER BY sort_order ASC, created_at ASC
            """,
            organization_id,
            profile_id,
        )
        return [dict(row) for row in rows]

    async def delete_document(
        self,
        *,
        organization_id: str,
        profile_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Delete one document row."""
        row = await self.db_connection.fetchrow(
            """
            DELETE FROM daily_help_documents
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
              AND id = $3::uuid
            RETURNING id::text AS id, document_type::text AS document_type
            """,
            organization_id,
            profile_id,
            document_id,
        )
        return dict(row) if row else None

    async def insert_event(
        self,
        *,
        organization_id: str,
        profile_id: str,
        event_type: str,
        actor_type: str = "staff",
        actor_user_id: str | None = None,
        actor_contact_id: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a daily_help_events audit row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_events (
                organization_id,
                daily_help_profile_id,
                event_type,
                actor_type,
                actor_user_id,
                actor_contact_id,
                payload,
                occurred_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::daily_help_event_type,
                $4::daily_help_actor_type, $5::uuid, $6::uuid,
                $7::jsonb, COALESCE($8::timestamptz, now())
            )
            RETURNING
              id::text AS id,
              event_type::text AS event_type,
              actor_type::text AS actor_type,
              payload,
              occurred_at
            """,
            organization_id,
            profile_id,
            event_type,
            actor_type,
            actor_user_id,
            actor_contact_id,
            json.dumps(payload or {}),
            occurred_at,
        )
        return dict(row)

    async def list_events(
        self,
        *,
        organization_id: str,
        profile_id: str,
    ) -> list[dict[str, Any]]:
        """List profile timeline events oldest-first."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              event_type::text AS event_type,
              actor_type::text AS actor_type,
              actor_user_id::text AS actor_user_id,
              actor_contact_id::text AS actor_contact_id,
              payload,
              occurred_at
            FROM daily_help_events
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
            ORDER BY occurred_at ASC, id ASC
            """,
            organization_id,
            profile_id,
        )
        return [dict(row) for row in rows]

    async def insert_link(
        self,
        *,
        organization_id: str,
        project_id: str,
        profile_id: str,
        unit_id: str,
        linked_by_contact_id: str | None = None,
        started_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert an active daily_help_household_links row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_household_links (
                organization_id,
                project_id,
                daily_help_profile_id,
                unit_id,
                linked_by_contact_id,
                status,
                started_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid,
                'active'::daily_help_household_link_status,
                COALESCE($6::timestamptz, now())
            )
            RETURNING
              id::text AS id,
              unit_id::text AS unit_id,
              linked_by_contact_id::text AS linked_by_contact_id,
              status::text AS status,
              started_at,
              created_at
            """,
            organization_id,
            project_id,
            profile_id,
            unit_id,
            linked_by_contact_id,
            started_at,
        )
        return dict(row)

    async def remove_link(
        self,
        *,
        organization_id: str,
        profile_id: str,
        link_id: str,
        removed_at: datetime | None = None,
        removal_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Soft-remove a household link."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE daily_help_household_links
            SET status = 'removed'::daily_help_household_link_status,
                removed_at = COALESCE($4::timestamptz, now()),
                removal_reason = $5,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
              AND id = $3::uuid
              AND status = 'active'::daily_help_household_link_status
            RETURNING
              id::text AS id,
              unit_id::text AS unit_id,
              status::text AS status,
              removed_at,
              removal_reason
            """,
            organization_id,
            profile_id,
            link_id,
            removed_at,
            removal_reason,
        )
        return dict(row) if row else None

    async def list_active_links_for_profile(
        self,
        *,
        organization_id: str,
        profile_id: str,
    ) -> list[dict[str, Any]]:
        """List active household links for a profile."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              hl.id::text AS id,
              hl.unit_id::text AS unit_id,
              hl.linked_by_contact_id::text AS linked_by_contact_id,
              hl.status::text AS status,
              hl.started_at,
              hl.created_at,
              u.code AS unit_code,
              u.unit_label
            FROM daily_help_household_links hl
            JOIN units u
              ON u.id = hl.unit_id
             AND u.organization_id = hl.organization_id
            WHERE hl.organization_id = $1::uuid
              AND hl.daily_help_profile_id = $2::uuid
              AND hl.status = 'active'::daily_help_household_link_status
            ORDER BY hl.started_at ASC
            """,
            organization_id,
            profile_id,
        )
        return [dict(row) for row in rows]

    async def list_active_links_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> list[dict[str, Any]]:
        """List active household links for a unit with profile summary fields."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              hl.id::text AS id,
              hl.unit_id::text AS unit_id,
              hl.linked_by_contact_id::text AS linked_by_contact_id,
              hl.status::text AS status,
              hl.started_at,
              p.id::text AS profile_id,
              p.display_name,
              p.initials,
              p.photo_path,
              p.phone_isd_code,
              p.phone_number,
              p.gate_passcode,
              p.open_to_work,
              p.linked_pass_id::text AS linked_pass_id,
              p.category_id::text AS category_id,
              c.name AS category_name
            FROM daily_help_household_links hl
            JOIN daily_help_profiles p
              ON p.id = hl.daily_help_profile_id
             AND p.organization_id = hl.organization_id
            JOIN daily_help_categories c
              ON c.id = p.category_id
             AND c.organization_id = p.organization_id
             AND c.project_id = p.project_id
            WHERE hl.organization_id = $1::uuid
              AND hl.unit_id = $2::uuid
              AND hl.status = 'active'::daily_help_household_link_status
              AND p.status = 'active'::daily_help_status
            ORDER BY hl.started_at DESC, hl.id DESC
            """,
            organization_id,
            unit_id,
        )
        return [dict(row) for row in rows]

    async def count_active_links_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> int:
        """Count active household links on a unit with active daily help profiles."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM daily_help_household_links hl
            JOIN daily_help_profiles p
              ON p.id = hl.daily_help_profile_id
             AND p.organization_id = hl.organization_id
            WHERE hl.organization_id = $1::uuid
              AND hl.unit_id = $2::uuid
              AND hl.status = 'active'::daily_help_household_link_status
              AND p.status = 'active'::daily_help_status
            """,
            organization_id,
            unit_id,
        )
        return int(count or 0)

    async def list_links_for_units(
        self,
        *,
        organization_id: str,
        unit_ids: list[str],
        profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active household links for one or more units."""
        if not unit_ids:
            return []
        filters = [
            "hl.organization_id = $1::uuid",
            "hl.unit_id = ANY($2::uuid[])",
            "hl.status = 'active'::daily_help_household_link_status",
        ]
        args: list[Any] = [organization_id, unit_ids]
        if profile_id:
            args.append(profile_id)
            filters.append(f"hl.daily_help_profile_id = ${len(args)}::uuid")
        where_sql = " AND ".join(filters)
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              hl.id::text AS id,
              hl.daily_help_profile_id::text AS daily_help_profile_id,
              hl.unit_id::text AS unit_id,
              hl.linked_by_contact_id::text AS linked_by_contact_id,
              hl.started_at
            FROM daily_help_household_links hl
            WHERE {where_sql}
            ORDER BY hl.started_at DESC
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def has_active_link(
        self,
        *,
        organization_id: str,
        profile_id: str,
        unit_id: str,
    ) -> bool:
        """Return whether an active household link exists for profile + unit."""
        exists = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM daily_help_household_links
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
              AND unit_id = $3::uuid
              AND status = 'active'::daily_help_household_link_status
            LIMIT 1
            """,
            organization_id,
            profile_id,
            unit_id,
        )
        return bool(exists)

    async def insert_rating(
        self,
        *,
        organization_id: str,
        project_id: str,
        profile_id: str,
        unit_id: str,
        rated_by_contact_id: str,
        stars: Decimal,
        comment: str | None,
        traits: list[str] | None = None,
    ) -> dict[str, Any]:
        """Insert a rating and optional trait tags."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_ratings (
                organization_id,
                project_id,
                daily_help_profile_id,
                unit_id,
                rated_by_contact_id,
                stars,
                comment
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7
            )
            RETURNING id::text AS id, stars, comment, created_at
            """,
            organization_id,
            project_id,
            profile_id,
            unit_id,
            rated_by_contact_id,
            stars,
            comment,
        )
        rating = dict(row)
        if traits:
            await self.db_connection.executemany(
                """
                INSERT INTO daily_help_rating_traits (
                    organization_id,
                    daily_help_rating_id,
                    trait
                )
                VALUES ($1::uuid, $2::uuid, $3::daily_help_rating_trait)
                ON CONFLICT (daily_help_rating_id, trait) DO NOTHING
                """,
                [(organization_id, rating["id"], trait) for trait in traits],
            )
        rating["traits"] = await self.list_rating_traits(
            organization_id=organization_id,
            rating_id=str(rating["id"]),
        )
        return rating

    async def get_rating_by_rater(
        self,
        *,
        organization_id: str,
        profile_id: str,
        unit_id: str,
        rated_by_contact_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one resident's rating for a profile and unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                id::text AS id,
                stars,
                comment,
                created_at,
                updated_at
            FROM daily_help_ratings
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
              AND unit_id = $3::uuid
              AND rated_by_contact_id = $4::uuid
            LIMIT 1
            """,
            organization_id,
            profile_id,
            unit_id,
            rated_by_contact_id,
        )
        if not row:
            return None
        rating = dict(row)
        rating["traits"] = await self.list_rating_traits(
            organization_id=organization_id,
            rating_id=str(rating["id"]),
        )
        return rating

    async def update_rating(
        self,
        *,
        organization_id: str,
        profile_id: str,
        unit_id: str,
        rated_by_contact_id: str,
        stars: Decimal,
        comment: str | None,
        traits: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Update stars, comment, and trait tags for an existing rating."""
        async with self.db_connection.transaction():
            row = await self.db_connection.fetchrow(
                """
                UPDATE daily_help_ratings
                SET stars = $5,
                    comment = $6,
                    updated_at = now()
                WHERE organization_id = $1::uuid
                  AND daily_help_profile_id = $2::uuid
                  AND unit_id = $3::uuid
                  AND rated_by_contact_id = $4::uuid
                RETURNING id::text AS id, stars, comment, created_at, updated_at
                """,
                organization_id,
                profile_id,
                unit_id,
                rated_by_contact_id,
                stars,
                comment,
            )
            if not row:
                return None
            rating = dict(row)
            await self.db_connection.execute(
                """
                DELETE FROM daily_help_rating_traits
                WHERE organization_id = $1::uuid
                  AND daily_help_rating_id = $2::uuid
                """,
                organization_id,
                rating["id"],
            )
            if traits:
                await self.db_connection.executemany(
                    """
                    INSERT INTO daily_help_rating_traits (
                        organization_id,
                        daily_help_rating_id,
                        trait
                    )
                    VALUES ($1::uuid, $2::uuid, $3::daily_help_rating_trait)
                    ON CONFLICT (daily_help_rating_id, trait) DO NOTHING
                    """,
                    [(organization_id, rating["id"], trait) for trait in traits],
                )
            rating["traits"] = await self.list_rating_traits(
                organization_id=organization_id,
                rating_id=str(rating["id"]),
            )
            return rating

    async def list_rating_traits(
        self,
        *,
        organization_id: str,
        rating_id: str,
    ) -> list[str]:
        """List trait enum values recorded on a rating."""
        rows = await self.db_connection.fetch(
            """
            SELECT trait::text AS trait
            FROM daily_help_rating_traits
            WHERE organization_id = $1::uuid
              AND daily_help_rating_id = $2::uuid
            ORDER BY trait
            """,
            organization_id,
            rating_id,
        )
        return [str(row["trait"]) for row in rows]

    async def get_rating_summary(
        self,
        *,
        organization_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        """Aggregate star rating and trait counts for a profile."""
        summaries = await self.get_rating_summaries_batch(
            organization_id=organization_id,
            profile_ids=[profile_id],
        )
        summary = summaries.get(profile_id)
        if summary is None:
            return {
                "rating_count": 0,
                "average_stars": 0.0,
                "trait_counts": {},
            }
        trait_rows = await self.db_connection.fetch(
            """
            SELECT
              rt.trait::text AS trait,
              COUNT(*)::int AS count
            FROM daily_help_rating_traits rt
            JOIN daily_help_ratings r
              ON r.id = rt.daily_help_rating_id
             AND r.organization_id = rt.organization_id
            WHERE rt.organization_id = $1::uuid
              AND r.daily_help_profile_id = $2::uuid
            GROUP BY rt.trait
            ORDER BY count DESC, rt.trait
            """,
            organization_id,
            profile_id,
        )
        return {
            **summary,
            "trait_counts": {str(row["trait"]): int(row["count"]) for row in trait_rows},
        }

    async def get_rating_summaries_batch(
        self,
        *,
        organization_id: str,
        profile_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Aggregate star averages for many profiles (preview/list enrichment)."""
        unique_ids = [profile_id for profile_id in dict.fromkeys(profile_ids) if profile_id]
        if not unique_ids:
            return {}
        rows = await self.db_connection.fetch(
            """
            SELECT
              daily_help_profile_id::text AS profile_id,
              COUNT(*)::int AS rating_count,
              COALESCE(AVG(stars), 0)::numeric(3, 2) AS average_stars
            FROM daily_help_ratings
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = ANY($2::uuid[])
            GROUP BY daily_help_profile_id
            """,
            organization_id,
            unique_ids,
        )
        return {
            str(row["profile_id"]): {
                "rating_count": int(row["rating_count"] or 0),
                "average_stars": float(row["average_stars"] or 0),
            }
            for row in rows
        }

    async def list_slots(
        self,
        *,
        organization_id: str,
        profile_id: str,
    ) -> list[dict[str, Any]]:
        """List availability slots for a profile."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              period::text AS period,
              start_time,
              end_time,
              sort_order,
              created_at,
              updated_at
            FROM daily_help_availability_slots
            WHERE organization_id = $1::uuid
              AND daily_help_profile_id = $2::uuid
            ORDER BY sort_order ASC, start_time ASC
            """,
            organization_id,
            profile_id,
        )
        return [dict(row) for row in rows]

    async def replace_slots(
        self,
        *,
        organization_id: str,
        profile_id: str,
        slots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Replace all availability slots for a profile."""
        async with self.db_connection.transaction():
            await self.db_connection.execute(
                """
                DELETE FROM daily_help_availability_slots
                WHERE organization_id = $1::uuid
                  AND daily_help_profile_id = $2::uuid
                """,
                organization_id,
                profile_id,
            )
            if not slots:
                return []
            records = await self.bulk_insert_returning(
                table="daily_help_availability_slots",
                required_columns=[
                    "organization_id",
                    "daily_help_profile_id",
                    "period",
                    "start_time",
                    "end_time",
                ],
                optional_columns=["sort_order"],
                rows=[
                    {
                        "organization_id": organization_id,
                        "daily_help_profile_id": profile_id,
                        **slot,
                    }
                    for slot in slots
                ],
            )
            return records

    async def list_attendance_absence_dates_for_month(
        self,
        *,
        organization_id: str,
        profile_id: str,
        unit_id: str | None = None,
        year: int,
        month: int,
    ) -> list[Any]:
        """Return calendar dates marked absent for a month (optionally scoped to one unit)."""
        if unit_id:
            rows = await self.db_connection.fetch(
                """
                SELECT attendance_date
                FROM daily_help_attendance_absences
                WHERE organization_id = $1::uuid
                  AND daily_help_profile_id = $2::uuid
                  AND unit_id = $3::uuid
                  AND EXTRACT(YEAR FROM attendance_date) = $4
                  AND EXTRACT(MONTH FROM attendance_date) = $5
                ORDER BY attendance_date
                """,
                organization_id,
                profile_id,
                unit_id,
                year,
                month,
            )
        else:
            rows = await self.db_connection.fetch(
                """
                SELECT DISTINCT attendance_date
                FROM daily_help_attendance_absences
                WHERE organization_id = $1::uuid
                  AND daily_help_profile_id = $2::uuid
                  AND EXTRACT(YEAR FROM attendance_date) = $3
                  AND EXTRACT(MONTH FROM attendance_date) = $4
                ORDER BY attendance_date
                """,
                organization_id,
                profile_id,
                year,
                month,
            )
        return [row["attendance_date"] for row in rows]

    async def resolve_absence_marked_by_contact(
        self,
        *,
        organization_id: str,
        unit_id: str,
        preferred_contact_id: str | None = None,
    ) -> str | None:
        """Resolve a contact id to store as the absence reporter for a unit."""
        if preferred_contact_id:
            return preferred_contact_id
        row = await self.db_connection.fetchrow(
            """
            SELECT contact_id::text AS contact_id
            FROM contact_roles
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND status = 'active'::public.contact_role_status
              AND ended_at IS NULL
              AND role_type IN (
                  'tenant'::public.contact_role_type,
                  'owner'::public.contact_role_type
              )
            ORDER BY CASE role_type
                WHEN 'tenant'::public.contact_role_type THEN 0
                ELSE 1
            END
            LIMIT 1
            """,
            organization_id,
            unit_id,
        )
        return str(row["contact_id"]) if row else None

    async def upsert_attendance_absence(
        self,
        *,
        organization_id: str,
        project_id: str,
        profile_id: str,
        unit_id: str,
        marked_by_contact_id: str,
        attendance_date: Any,
    ) -> dict[str, Any]:
        """Record or refresh a resident-reported absence for one calendar day."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_attendance_absences (
                organization_id,
                project_id,
                daily_help_profile_id,
                unit_id,
                marked_by_contact_id,
                attendance_date
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::date
            )
            ON CONFLICT (daily_help_profile_id, unit_id, attendance_date)
            DO UPDATE SET
                marked_by_contact_id = EXCLUDED.marked_by_contact_id,
                created_at = now()
            RETURNING
                id::text AS id,
                attendance_date,
                created_at
            """,
            organization_id,
            project_id,
            profile_id,
            unit_id,
            marked_by_contact_id,
            attendance_date,
        )
        return dict(row)
