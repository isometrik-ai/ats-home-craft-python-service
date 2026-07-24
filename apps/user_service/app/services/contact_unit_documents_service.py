"""Business logic for owner allotment documents on units."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_unit_documents_repository import (
    ContactUnitDocumentsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.project_inventory import CreateUnitDocumentRequest
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


def serialize_unit_document(row: dict[str, Any]) -> dict[str, Any]:
    """Map a contact_unit_documents row to API shape."""
    return {
        "id": str(row["id"]),
        "contact_unit_id": str(row["contact_unit_id"]),
        "document_type": row["document_type"],
        "file_path": row["file_path"],
        "file_name": row.get("file_name"),
        "uploaded_by_user_id": row.get("uploaded_by_user_id"),
        "created_at": format_iso_datetime(row.get("created_at")),
        "updated_at": format_iso_datetime(row.get("updated_at")),
    }


class ContactUnitDocumentsService:
    """Admin CRUD for documents tied to the current owner allotment on a unit."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.documents_repo = ContactUnitDocumentsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)

    @property
    def _org_id(self) -> str:
        """The organization ID from the user context."""
        return self.user_context.organization_id

    async def _ensure_project_unit(
        self,
        *,
        project_id: str,
        unit_id: str,
    ) -> None:
        """Raise when the unit is missing or outside the project."""
        unit = await self.units_repo.get_unit(
            organization_id=self._org_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="project_setup.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def _resolve_owner_contact_unit(
        self,
        *,
        project_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Return the current Owner contact_units row for a unit."""
        await self._ensure_project_unit(project_id=project_id, unit_id=unit_id)
        owner = await self.units_repo.get_unit_owner_contact(
            organization_id=self._org_id,
            unit_id=unit_id,
        )
        if not owner:
            raise NotFoundException(
                message_key="project_setup.errors.unit_owner_not_assigned",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return owner

    async def list_unit_documents(
        self,
        *,
        project_id: str,
        unit_id: str,
    ) -> list[dict[str, Any]]:
        """List documents for the current owner allotment on a unit."""
        owner = await self._resolve_owner_contact_unit(
            project_id=project_id,
            unit_id=unit_id,
        )
        rows = await self.documents_repo.list_by_contact_unit(
            organization_id=self._org_id,
            contact_unit_id=str(owner["contact_unit_id"]),
        )
        return [serialize_unit_document(row) for row in rows]

    async def add_unit_document(
        self,
        *,
        project_id: str,
        unit_id: str,
        body: CreateUnitDocumentRequest,
    ) -> dict[str, Any]:
        """Add a document to the current owner allotment on a unit."""
        owner = await self._resolve_owner_contact_unit(
            project_id=project_id,
            unit_id=unit_id,
        )
        row = await self.documents_repo.insert_document(
            organization_id=self._org_id,
            contact_unit_id=str(owner["contact_unit_id"]),
            document_type=body.document_type.value,
            file_path=body.file_path.strip(),
            file_name=(body.file_name or "").strip() or None,
            uploaded_by_user_id=self.user_context.user_id,
        )
        return serialize_unit_document(row)

    async def delete_unit_document(
        self,
        *,
        project_id: str,
        unit_id: str,
        document_id: str,
    ) -> None:
        """Delete one document from the current owner allotment."""
        owner = await self._resolve_owner_contact_unit(
            project_id=project_id,
            unit_id=unit_id,
        )
        deleted = await self.documents_repo.delete_document(
            organization_id=self._org_id,
            contact_unit_id=str(owner["contact_unit_id"]),
            document_id=document_id,
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.unit_document_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def list_documents_for_owner_contact_unit(
        self,
        *,
        contact_unit_id: str,
    ) -> list[dict[str, Any]]:
        """List documents when contact_unit_id is already known (unit detail)."""
        rows = await self.documents_repo.list_by_contact_unit(
            organization_id=self._org_id,
            contact_unit_id=contact_unit_id,
        )
        return [serialize_unit_document(row) for row in rows]
