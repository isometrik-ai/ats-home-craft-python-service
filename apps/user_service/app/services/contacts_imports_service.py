"""Contacts imports producer service.

Creates import jobs and builds metadata-only Kafka payloads for best-effort publish.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg

from apps.user_service.app.db.repositories.import_jobs_repository import (
    ImportJobsRepository,
)
from apps.user_service.app.schemas.contacts_imports import ContactsImportEventPayload
from apps.user_service.app.schemas.enums import (
    ContactsImportEventAction,
    ContactsImportJobStatus,
    ContactsImportKafkaStream,
    ContactsImportType,
)

CONTACTS_IMPORT_TOPIC = ContactsImportKafkaStream.CONTACTS_IMPORT_REQUESTED


class ContactsImportService:
    """Service layer for creating and retrying contacts import jobs."""

    def __init__(self, *, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    @staticmethod
    def _now_iso() -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _generate_job_id() -> str:
        """Generate a compact, URL-safe job identifier."""
        return f"imp_{uuid.uuid4().hex}"

    @staticmethod
    def _validate_file_url(file_url: str) -> None:
        """Reject obviously unsafe/unroutable URLs.

        We intentionally do not attempt DNS resolution here; the consumer will
        fetch the URL and should have its own network policies. This guards
        against trivial SSRF vectors such as localhost/private IP literals.
        """
        if not file_url or len(file_url) > 4096:
            raise ValueError("file_url is invalid")

        parsed = urlparse(file_url)
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("file_url must be http(s)")
        if not parsed.netloc:
            raise ValueError("file_url must include a host")

        host = parsed.hostname or ""
        if host in {"localhost"} or host.endswith(".localhost"):
            raise ValueError("file_url host is not allowed")

        # Reject direct IP literals in private/reserved ranges.
        try:
            ip = ipaddress.ip_address(host)
            if any(
                (
                    ip.is_private,
                    ip.is_loopback,
                    ip.is_link_local,
                    ip.is_multicast,
                    ip.is_reserved,
                    ip.is_unspecified,
                )
            ):
                raise ValueError("file_url host is not allowed")
        except ValueError:
            # Not an IP literal; allow (DNS name).
            pass

    async def create_job_and_enqueue(
        self,
        *,
        organization_id: str,
        requested_by: str | None,
        file_url: str,
        file_type: str,
        schema_version: int,
        mapping: dict[str, str] | None,
        options: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create a new import job and build the corresponding Kafka event payload."""
        self._validate_file_url(file_url)

        job_id = self._generate_job_id()
        job_repo = ImportJobsRepository(db_connection=self.db_connection)

        created_at = self._now_iso()
        job = await job_repo.create_job(
            job_id=job_id,
            organization_id=organization_id,
            status=ContactsImportJobStatus.QUEUED.value,
            file_url=file_url,
            file_type=file_type,
            schema_version=schema_version,
            mapping=mapping,
            options=options,
        )

        payload = ContactsImportEventPayload(
            event_id=str(uuid.uuid4()),
            schema_version=int(schema_version),
            import_type=ContactsImportType.CONTACTS,
            job_key=job_id,
            organization_id=organization_id,
            file_url=file_url,
            requested_by=requested_by,
            created_at=created_at,
            action=ContactsImportEventAction.CREATE,
        ).model_dump(mode="json")

        return job, payload

    async def get_job(self, *, job_id: str, organization_id: str) -> dict[str, Any] | None:
        """Fetch a single import job for the given organization."""
        repo = ImportJobsRepository(db_connection=self.db_connection)
        return await repo.get_job(job_id=job_id, organization_id=organization_id)

    async def retry_job_and_enqueue(
        self,
        *,
        job_id: str,
        organization_id: str,
        requested_by: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Move an existing job back to queued state and build a retry event payload."""
        repo = ImportJobsRepository(db_connection=self.db_connection)
        job = await repo.get_job(job_id=job_id, organization_id=organization_id)
        if job is None:
            return None

        # Producer-side contract: retry moves job back to queued. Consumer owns
        # any row-ledger/resume semantics.
        await repo.set_status(
            job_id=job_id,
            organization_id=organization_id,
            status=ContactsImportJobStatus.QUEUED.value,
        )

        created_at = self._now_iso()
        payload = ContactsImportEventPayload(
            event_id=str(uuid.uuid4()),
            schema_version=int(job.get("schema_version") or 1),
            import_type=ContactsImportType.CONTACTS,
            job_key=job_id,
            organization_id=organization_id,
            file_url=str(job.get("file_url") or ""),
            requested_by=requested_by,
            created_at=created_at,
            action=ContactsImportEventAction.RETRY,
        ).model_dump(mode="json")

        updated = await repo.get_job(job_id=job_id, organization_id=organization_id)
        return (updated, payload) if updated is not None else None
