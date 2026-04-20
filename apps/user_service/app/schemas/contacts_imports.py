"""Schemas for Contacts Import API (producer side).

Producer responsibilities:
- validate request payload
- create an import job record
- persist + publish a metadata-only Kafka payload for the consumer
- expose job status + retry endpoints
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

from apps.user_service.app.schemas.enums import (
    ContactsImportDedupeKey,
    ContactsImportEventAction,
    ContactsImportFileType,
    ContactsImportJobStatus,
    ContactsImportMode,
    ContactsImportType,
)


class ContactsImportOptions(BaseModel):
    """Options that influence how contacts imports behave."""

    mode: ContactsImportMode = ContactsImportMode.UPSERT
    dedupe_key: ContactsImportDedupeKey = ContactsImportDedupeKey.EMAIL
    has_header: bool = True


class CreateContactsImportJobRequest(BaseModel):
    """JWT-authenticated import where organization comes from the caller's session."""

    file_url: HttpUrl = Field(..., description="Reachable file URL (typically presigned).")
    file_type: ContactsImportFileType = ContactsImportFileType.CSV
    schema_version: int = Field(..., ge=1, le=1000)
    mapping: dict[str, str] | None = Field(
        default=None,
        description="Canonical field -> column header mapping (optional).",
    )
    options: ContactsImportOptions | None = None


class ExternalCreateContactsImportJobRequest(BaseModel):
    """External integrations where organization is resolved from Isometrik credentials."""

    file_url: HttpUrl = Field(..., description="Reachable file URL (typically presigned).")
    file_type: ContactsImportFileType = ContactsImportFileType.CSV
    schema_version: int = Field(..., ge=1, le=1000)
    mapping: dict[str, str] | None = None
    options: ContactsImportOptions | None = None


class CreateContactsImportJobResponse(BaseModel):
    """Response model returned after creating a contacts import job."""

    job_id: str
    status: ContactsImportJobStatus


class ContactsImportProgress(BaseModel):
    """Progress counters for a contacts import job."""

    total_rows: int = 0
    processed_rows: int = 0
    success_rows: int = 0
    error_rows: int = 0
    percent: float = 0.0

    @classmethod
    def from_import_job(cls, job: Mapping[str, Any]) -> ContactsImportProgress:
        """Build a progress view from a raw import job row."""
        total = int(job["total_rows"])
        processed = int(job["processed_rows"])
        success = int(job["success_rows"])
        errors = int(job["error_rows"])
        pct = (processed / total * 100.0) if total else 0.0
        return cls(
            total_rows=total,
            processed_rows=processed,
            success_rows=success,
            error_rows=errors,
            percent=pct,
        )


class GetContactsImportJobResponse(BaseModel):
    """Full details and progress for a contacts import job."""

    job_id: str
    organization_id: str
    import_type: ContactsImportType = ContactsImportType.CONTACTS
    status: ContactsImportJobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: ContactsImportProgress
    errors_file_url: str | None = None
    rows: dict[str, Any] | None = None

    @classmethod
    def from_job_row(cls, job: Mapping[str, Any]) -> GetContactsImportJobResponse:
        """Create a response model from a repository job row."""
        return cls(
            job_id=str(job["job_id"]),
            organization_id=str(job["organization_id"]),
            import_type=ContactsImportType(str(job["import_type"])),
            status=job["status"],
            created_at=str(job["created_at"]),
            started_at=str(job["started_at"]) if job.get("started_at") else None,
            finished_at=str(job["finished_at"]) if job.get("finished_at") else None,
            progress=ContactsImportProgress.from_import_job(job),
            errors_file_url=job.get("errors_file_url"),
        )


class RetryContactsImportJobResponse(BaseModel):
    """Response model returned after retrying a contacts import job."""

    job_id: str
    status: ContactsImportJobStatus


class ContactsImportJobLogItem(BaseModel):
    """Latest log payload for a contacts import job (one row per job)."""

    job_id: str
    job_status: ContactsImportJobStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class ContactsImportEventPayload(BaseModel):
    """Kafka payload contract (metadata only) for contacts imports."""

    event_id: str
    schema_version: int
    import_type: ContactsImportType = ContactsImportType.CONTACTS
    job_key: str
    organization_id: str
    file_url: str
    requested_by: str | None = None
    created_at: str
    action: ContactsImportEventAction = ContactsImportEventAction.CREATE
