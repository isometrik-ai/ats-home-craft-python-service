"""Tenant requests persistence."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import TenantRequestStatus

_TENANT_REQUEST_SELECT_SQL = """
SELECT
  tr.id::text AS id,
  tr.organization_id::text AS organization_id,
  tr.project_id::text AS project_id,
  tr.unit_id::text AS unit_id,
  tr.submitted_by_contact_id::text AS submitted_by_contact_id,
  tr.tenant_first_name,
  tr.tenant_last_name,
  tr.tenant_phones,
  tr.tenant_emails,
  tr.move_in_date,
  tr.status::text AS status,
  tr.portal_access,
  tr.tenant_contact_id::text AS tenant_contact_id,
  tr.contact_unit_id::text AS contact_unit_id,
  tr.approved_at,
  tr.approved_by_user_id::text AS approved_by_user_id,
  tr.superseded_at,
  tr.superseded_by_request_id::text AS superseded_by_request_id,
  tr.cancelled_at,
  tr.submitted_at,
  tr.admin_notes,
  tr.created_at,
  tr.updated_at,
  u.code AS unit_code,
  u.unit_label,
  owner.first_name AS owner_first_name,
  owner.last_name AS owner_last_name,
  owner.prefix AS owner_prefix
FROM tenant_requests tr
JOIN units u ON u.id = tr.unit_id
JOIN contacts owner ON owner.id = tr.submitted_by_contact_id
WHERE tr.organization_id = $1::uuid
"""


class TenantRequestsRepository(BaseRepository):
    """Database operations for tenant request workflow tables."""

    async def insert_request(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        submitted_by_contact_id: str,
        tenant_first_name: str,
        tenant_last_name: str | None,
        tenant_phones: list[dict[str, Any]],
        tenant_emails: list[dict[str, Any]],
        move_in_date: date | None,
        portal_access: bool,
        status: str,
        submitted_at: datetime | None,
    ) -> dict[str, Any]:
        """Insert a tenant_requests header row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO tenant_requests (
                organization_id,
                project_id,
                unit_id,
                submitted_by_contact_id,
                tenant_first_name,
                tenant_last_name,
                tenant_phones,
                tenant_emails,
                move_in_date,
                portal_access,
                status,
                submitted_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5, $6, $7::jsonb, $8::jsonb, $9::date,
                $10, $11::tenant_request_status, $12::timestamptz
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            unit_id,
            submitted_by_contact_id,
            tenant_first_name,
            tenant_last_name,
            json.dumps(tenant_phones),
            json.dumps(tenant_emails),
            move_in_date,
            portal_access,
            status,
            submitted_at,
        )
        return dict(row)

    async def insert_document(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        document_type: str,
        file_path: str,
        file_name: str | None,
    ) -> dict[str, Any]:
        """Insert one tenant_request_documents row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO tenant_request_documents (
                organization_id,
                tenant_request_id,
                document_type,
                file_path,
                file_name
            )
            VALUES ($1::uuid, $2::uuid, $3::tenant_request_document_type, $4, $5)
            RETURNING id::text AS id, document_type::text AS document_type
            """,
            organization_id,
            tenant_request_id,
            document_type,
            file_path,
            file_name,
        )
        return dict(row)

    async def insert_event(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        event_type: str,
        actor_contact_id: str | None = None,
        actor_user_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a tenant_request_events row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO tenant_request_events (
                organization_id,
                tenant_request_id,
                event_type,
                actor_contact_id,
                actor_user_id,
                payload
            )
            VALUES (
                $1::uuid, $2::uuid, $3::tenant_request_event_type,
                $4::uuid, $5::uuid, $6::jsonb
            )
            RETURNING id::text AS id, event_type::text AS event_type, occurred_at
            """,
            organization_id,
            tenant_request_id,
            event_type,
            actor_contact_id,
            actor_user_id,
            json.dumps(payload or {}),
        )
        return dict(row)

    async def get_request_by_id(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one tenant request header with unit and owner display fields."""
        row = await self.db_connection.fetchrow(
            f"""
            {_TENANT_REQUEST_SELECT_SQL}
              AND tr.id = $2::uuid
            LIMIT 1
            """,
            organization_id,
            tenant_request_id,
        )
        return dict(row) if row else None

    async def list_documents(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
    ) -> list[dict[str, Any]]:
        """List documents for a tenant request ordered by type."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              document_type::text AS document_type,
              file_path,
              file_name,
              status::text AS status,
              rejection_reason,
              verified_at,
              verified_by_user_id::text AS verified_by_user_id,
              uploaded_at,
              updated_at
            FROM tenant_request_documents
            WHERE organization_id = $1::uuid
              AND tenant_request_id = $2::uuid
            ORDER BY document_type
            """,
            organization_id,
            tenant_request_id,
        )
        return [dict(row) for row in rows]

    async def list_events(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
    ) -> list[dict[str, Any]]:
        """List timeline events oldest-first."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              event_type::text AS event_type,
              payload,
              occurred_at
            FROM tenant_request_events
            WHERE organization_id = $1::uuid
              AND tenant_request_id = $2::uuid
            ORDER BY occurred_at ASC, id ASC
            """,
            organization_id,
            tenant_request_id,
        )
        return [dict(row) for row in rows]

    async def get_document_by_id(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one document row."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              id::text AS id,
              document_type::text AS document_type,
              file_path,
              file_name,
              status::text AS status,
              rejection_reason,
              verified_at,
              uploaded_at
            FROM tenant_request_documents
            WHERE organization_id = $1::uuid
              AND tenant_request_id = $2::uuid
              AND id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            tenant_request_id,
            document_id,
        )
        return dict(row) if row else None

    async def update_document_reupload(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        document_id: str,
        file_path: str,
        file_name: str | None,
    ) -> dict[str, Any] | None:
        """Replace file on a document and reset to pending."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE tenant_request_documents
            SET file_path = $4,
                file_name = $5,
                status = 'pending'::tenant_request_document_status,
                rejection_reason = NULL,
                verified_at = NULL,
                verified_by_user_id = NULL,
                uploaded_at = now(),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND tenant_request_id = $2::uuid
              AND id = $3::uuid
            RETURNING id::text AS id, document_type::text AS document_type, status::text AS status
            """,
            organization_id,
            tenant_request_id,
            document_id,
            file_path,
            file_name,
        )
        return dict(row) if row else None

    async def verify_document(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        document_id: str,
        verified_by_user_id: str,
    ) -> dict[str, Any] | None:
        """Mark a document verified."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE tenant_request_documents
            SET status = 'verified'::tenant_request_document_status,
                rejection_reason = NULL,
                verified_at = now(),
                verified_by_user_id = $4::uuid,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND tenant_request_id = $2::uuid
              AND id = $3::uuid
            RETURNING id::text AS id, document_type::text AS document_type, status::text AS status
            """,
            organization_id,
            tenant_request_id,
            document_id,
            verified_by_user_id,
        )
        return dict(row) if row else None

    async def reject_document(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        document_id: str,
        verified_by_user_id: str,
        rejection_reason: str,
    ) -> dict[str, Any] | None:
        """Mark a document rejected."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE tenant_request_documents
            SET status = 'rejected'::tenant_request_document_status,
                rejection_reason = $5,
                verified_at = now(),
                verified_by_user_id = $4::uuid,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND tenant_request_id = $2::uuid
              AND id = $3::uuid
            RETURNING id::text AS id, document_type::text AS document_type, status::text AS status
            """,
            organization_id,
            tenant_request_id,
            document_id,
            verified_by_user_id,
            rejection_reason,
        )
        return dict(row) if row else None

    async def update_request_status(
        self,
        *,
        organization_id: str,
        tenant_request_id: str,
        status: str,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Patch tenant_requests status and optional metadata columns."""
        set_clauses = ["status = $3::tenant_request_status", "updated_at = now()"]
        args: list[Any] = [organization_id, tenant_request_id, status]
        allowed = {
            "tenant_contact_id": "uuid",
            "contact_unit_id": "uuid",
            "approved_at": "timestamptz",
            "approved_by_user_id": "uuid",
            "cancelled_at": "timestamptz",
            "superseded_at": "timestamptz",
            "superseded_by_request_id": "uuid",
            "admin_notes": "text",
        }
        for key, cast in allowed.items():
            if key not in fields:
                continue
            args.append(fields[key])
            set_clauses.append(f"{key} = ${len(args)}::{cast}")
        query = f"""
            UPDATE tenant_requests
            SET {", ".join(set_clauses)}
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, status::text AS status
        """
        row = await self.db_connection.fetchrow(query, *args)
        return dict(row) if row else None

    async def list_for_owner(
        self,
        *,
        organization_id: str,
        owner_contact_id: str,
        unit_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List tenant requests submitted by an owner."""
        filters = ["tr.submitted_by_contact_id = $2::uuid"]
        args: list[Any] = [organization_id, owner_contact_id]
        if unit_id:
            args.append(unit_id)
            filters.append(f"tr.unit_id = ${len(args)}::uuid")
        where_sql = " AND ".join(filters)
        count = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)
            FROM tenant_requests tr
            WHERE tr.organization_id = $1::uuid
              AND {where_sql}
            """,
            *args,
        )
        args.extend([limit, offset])
        rows = await self.db_connection.fetch(
            f"""
            {_TENANT_REQUEST_SELECT_SQL}
              AND {where_sql}
            ORDER BY tr.submitted_at DESC NULLS LAST, tr.created_at DESC
            LIMIT ${len(args) - 1}
            OFFSET ${len(args)}
            """,
            *args,
        )
        return [dict(row) for row in rows], int(count or 0)

    async def list_for_admin(
        self,
        *,
        organization_id: str,
        statuses: list[str] | None,
        search: str | None,
        unit_id: str | None,
        project_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated admin list with optional filters."""
        filters: list[str] = []
        args: list[Any] = [organization_id]
        if statuses:
            args.append(statuses)
            filters.append(f"tr.status = ANY(${len(args)}::tenant_request_status[])")
        if unit_id:
            args.append(unit_id)
            filters.append(f"tr.unit_id = ${len(args)}::uuid")
        if project_id:
            args.append(project_id)
            filters.append(f"tr.project_id = ${len(args)}::uuid")
        if search:
            args.append(f"%{search.strip()}%")
            idx = len(args)
            filters.append(
                f"(u.code ILIKE ${idx} OR u.unit_label ILIKE ${idx} "
                f"OR tr.tenant_first_name ILIKE ${idx} OR tr.tenant_last_name ILIKE ${idx})"
            )
        where_extra = f" AND {' AND '.join(filters)}" if filters else ""
        count = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)
            FROM tenant_requests tr
            JOIN units u ON u.id = tr.unit_id
            WHERE tr.organization_id = $1::uuid
            {where_extra}
            """,
            *args,
        )
        args.extend([limit, offset])
        rows = await self.db_connection.fetch(
            f"""
            {_TENANT_REQUEST_SELECT_SQL}
            {where_extra}
            ORDER BY tr.submitted_at DESC NULLS LAST, tr.created_at DESC
            LIMIT ${len(args) - 1}
            OFFSET ${len(args)}
            """,
            *args,
        )
        return [dict(row) for row in rows], int(count or 0)

    async def get_summary_counts(self, *, organization_id: str) -> dict[str, int]:
        """Dashboard summary card counts."""
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        row = await self.db_connection.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE status IN ('submitted', 'pending_review')
              ) AS pending_review,
              COUNT(*) FILTER (
                WHERE status = 'awaiting_resubmission'
              ) AS awaiting_resubmission,
              COUNT(*) FILTER (
                WHERE status = 'ready_to_approve'
              ) AS ready_to_approve,
              COUNT(*) FILTER (
                WHERE status = 'approved'
                  AND approved_at >= $2::timestamptz
                  AND superseded_at IS NULL
              ) AS approved_this_month
            FROM tenant_requests
            WHERE organization_id = $1::uuid
            """,
            organization_id,
            month_start,
        )
        if not row:
            return {
                "pending_review": 0,
                "awaiting_resubmission": 0,
                "ready_to_approve": 0,
                "approved_this_month": 0,
            }
        return {key: int(row[key] or 0) for key in row.keys()}

    async def find_active_approved_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Return the current approved (non-superseded) request for a unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT id::text AS id, tenant_contact_id::text AS tenant_contact_id,
                   contact_unit_id::text AS contact_unit_id
            FROM tenant_requests
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND status = $3::tenant_request_status
              AND superseded_at IS NULL
            LIMIT 1
            """,
            organization_id,
            unit_id,
            TenantRequestStatus.APPROVED.value,
        )
        return dict(row) if row else None
