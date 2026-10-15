"""Schemas for project workbook import."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

from apps.user_service.app.schemas.enums import (
    ContactsImportFileType,
    ContactsImportJobStatus,
    ContactsImportType,
)


def _as_iso(value: Any) -> str | None:
    """Serialize datetimes for import job responses."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class CreateProjectsImportJobRequest(BaseModel):
    """Create a project setup import from a Project_data_bulk workbook."""

    file_url: HttpUrl = Field(..., description="Reachable xlsx URL (typically presigned).")
    file_type: ContactsImportFileType = ContactsImportFileType.XLSX
    community_admin_user_id: str = Field(
        ...,
        min_length=36,
        max_length=36,
        description="Org member who becomes community admin on created projects.",
    )
    schema_version: int = Field(default=1, ge=1, le=1000)


class CreateProjectsImportJobResponse(BaseModel):
    """Response after creating a projects import job."""

    job_id: str
    status: ContactsImportJobStatus
    import_type: ContactsImportType = ContactsImportType.PROJECTS


class ProjectsImportProgress(BaseModel):
    """Progress counters for a projects import job."""

    total_rows: int = 0
    processed_rows: int = 0
    success_rows: int = 0
    error_rows: int = 0
    percent: float = 0.0

    @classmethod
    def from_import_job(cls, job: Mapping[str, Any]) -> ProjectsImportProgress:
        """Build progress from a raw import job row."""
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


class GetProjectsImportJobResponse(BaseModel):
    """Job details and progress for a projects import."""

    job_id: str
    organization_id: str
    import_type: ContactsImportType = ContactsImportType.PROJECTS
    status: ContactsImportJobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: ProjectsImportProgress
    errors_file_url: str | None = None
    rows: dict[str, Any] | None = None

    @classmethod
    def from_job_row(cls, job: Mapping[str, Any]) -> GetProjectsImportJobResponse:
        """Create a response model from a repository job row."""
        return cls(
            job_id=str(job["job_id"]),
            organization_id=str(job["organization_id"]),
            import_type=ContactsImportType(str(job.get("import_type") or "projects")),
            status=job["status"],
            created_at=_as_iso(job["created_at"]) or "",
            started_at=_as_iso(job.get("started_at")),
            finished_at=_as_iso(job.get("finished_at")),
            progress=ProjectsImportProgress.from_import_job(job),
            errors_file_url=job.get("errors_file_url"),
        )
