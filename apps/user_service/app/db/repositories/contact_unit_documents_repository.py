"""Persistence for contact_unit_documents (owner allotment files)."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class ContactUnitDocumentsRepository(BaseRepository):
    """Database operations for public.contact_unit_documents."""

    async def list_by_contact_unit(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> list[dict[str, Any]]:
        """List documents for one contact_units row."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                id::text AS id,
                contact_unit_id::text AS contact_unit_id,
                document_type::text AS document_type,
                file_path,
                file_name,
                uploaded_by_user_id::text AS uploaded_by_user_id,
                created_at,
                updated_at
            FROM contact_unit_documents
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
            ORDER BY document_type, created_at, id
            """,
            organization_id,
            contact_unit_id,
        )
        return [dict(row) for row in rows]

    async def insert_document(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        document_type: str,
        file_path: str,
        file_name: str | None,
        uploaded_by_user_id: str | None,
    ) -> dict[str, Any]:
        """Insert an ownership document for a contact_units row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO contact_unit_documents (
                organization_id,
                contact_unit_id,
                document_type,
                file_path,
                file_name,
                uploaded_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6::uuid
            )
            RETURNING
                id::text AS id,
                contact_unit_id::text AS contact_unit_id,
                document_type::text AS document_type,
                file_path,
                file_name,
                uploaded_by_user_id::text AS uploaded_by_user_id,
                created_at,
                updated_at
            """,
            organization_id,
            contact_unit_id,
            document_type,
            file_path,
            file_name,
            uploaded_by_user_id,
        )
        return dict(row)

    async def delete_document(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        document_id: str,
    ) -> bool:
        """Delete one document belonging to a contact_units row."""
        result = await self.db_connection.execute(
            """
            DELETE FROM contact_unit_documents
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
              AND id = $3::uuid
            """,
            organization_id,
            contact_unit_id,
            document_id,
        )
        return result.upper().endswith("1")

    async def get_document(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one document row."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                id::text AS id,
                contact_unit_id::text AS contact_unit_id,
                document_type::text AS document_type,
                file_path,
                file_name,
                uploaded_by_user_id::text AS uploaded_by_user_id,
                created_at,
                updated_at
            FROM contact_unit_documents
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
              AND id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            contact_unit_id,
            document_id,
        )
        return dict(row) if row else None
