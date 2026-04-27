"""Contacts imports producer service.

Creates import jobs and builds metadata-only Kafka payloads for best-effort publish.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import ipaddress
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
from supabase import AsyncClient

from apps.user_service.app.db.repositories.companies_repository import (
    CompaniesRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import (
    CONTACT_JSONB_COLUMNS,
    ContactsRepository,
)
from apps.user_service.app.db.repositories.import_job_logs_repository import (
    ImportJobLogsRepository,
)
from apps.user_service.app.db.repositories.import_job_rows_repository import (
    ImportJobRowsRepository,
)
from apps.user_service.app.db.repositories.import_jobs_repository import (
    ImportJobsRepository,
)
from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.contacts_imports import ContactsImportEventPayload
from apps.user_service.app.schemas.enums import (
    ContactsImportEventAction,
    ContactsImportJobStatus,
    ContactsImportKafkaStream,
    ContactsImportType,
    EntityType,
)
from apps.user_service.app.services.bulk_leads_creator import BulkLeadCreator
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    coerce_json_list,
    parse_json_any,
    parse_json_field,
    serialize_jsonb_param,
)
from libs.shared_db.supabase_db.client import get_supabase_service_client

CONTACTS_IMPORT_TOPIC = ContactsImportKafkaStream.CONTACTS_IMPORT_REQUESTED.value

logger = logging.getLogger(__name__)


@dataclass
class _ContactsImportTotals:
    """Mutable per-job counters and caches for consumer-side processing."""

    processed_total: int = 0
    success_total: int = 0
    errors_total: int = 0
    last_log_ts: float = 0.0
    company_cache: dict[str, str] = field(default_factory=dict)


class ContactsImportService:
    """Service layer for creating, retrying, and processing contacts import jobs."""

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

    async def list_job_rows(
        self,
        *,
        job_id: str,
        organization_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List all row-ledger entries for a job (paginated)."""
        rows_repo = ImportJobRowsRepository(db_connection=self.db_connection)
        job = await self.get_job(job_id=job_id, organization_id=organization_id)
        if job is None:
            return ([], 0)
        return await rows_repo.list_rows(
            organization_id=organization_id,
            job_id=str(job.get("id") or ""),
            page=page,
            page_size=page_size,
        )

    async def list_job_logs(
        self,
        *,
        organization_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List job logs for contacts imports (org-scoped; paginated)."""
        repo = ImportJobLogsRepository(db_connection=self.db_connection)
        return await repo.list_logs(
            organization_id=organization_id,
            page=page,
            page_size=page_size,
        )

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

    # ------------------------------------------------------------------
    # Consumer-side processing
    # ------------------------------------------------------------------
    async def process_job_event(
        self,
        *,
        event: ContactsImportEventPayload,
        batch_size: int = 1000,
    ) -> None:
        """Process a contacts import job end-to-end for a single Kafka event.

        Responsibilities:
        - Verify job exists and is queued
        - Mark job as running
        - Download and parse the source file
        - Validate each row using ``CreateContactRequestStandalone`` (same schema as single create)
        - Bulk-insert contacts in batches (single DB round trip per batch)
        - Increment job progress counters
        """
        job_repo = ImportJobsRepository(db_connection=self.db_connection)
        rows_repo = ImportJobRowsRepository(db_connection=self.db_connection)
        logs_repo = ImportJobLogsRepository(db_connection=self.db_connection)

        logger.info(
            "contacts_import_service_event_received job_key=%s organization_id=%s action=%s",
            str(event.job_key),
            str(event.organization_id),
            getattr(event.action, "value", event.action),
        )

        job = await job_repo.get_job(job_id=event.job_key, organization_id=event.organization_id)
        if job is None:
            logger.info(
                "contacts_import_service_job_not_found job_key=%s organization_id=%s",
                str(event.job_key),
                str(event.organization_id),
            )
            return

        if ContactsImportJobStatus(str(job["status"])) != ContactsImportJobStatus.QUEUED:
            logger.info(
                "contacts_import_service_job_skipped_not_queued",
                extra={
                    "job_key": str(event.job_key),
                    "organization_id": str(event.organization_id),
                    "status": str(job.get("status")),
                },
            )
            return

        started_at_dt = datetime.now(UTC)
        started_at = started_at_dt.isoformat()
        job_internal_id = str(job.get("id") or "")

        mapping = self._safe_parse_json_dict(job.get("mapping"))
        options = self._safe_parse_json_dict(job.get("options"))

        totals = _ContactsImportTotals()

        try:
            await job_repo.set_status_and_timestamps(
                job_id=event.job_key,
                organization_id=event.organization_id,
                status=ContactsImportJobStatus.RUNNING.value,
                started_at=started_at_dt,
            )
            await logs_repo.upsert_payload(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                payload={
                    "phase": "started",
                    "action": str(event.action).lower(),
                    "started_at": started_at,
                    "stats": {"processed": 0, "success": 0, "errors": 0},
                },
            )

            logger.info(
                "contacts_import_service_job_started",
                extra={
                    "job_key": str(event.job_key),
                    "job_id": job_internal_id,
                    "organization_id": str(event.organization_id),
                    "file_type": str(job.get("file_type") or ""),
                },
            )

            supabase_client: AsyncClient = await get_supabase_service_client()
            user_context = UserContext(
                user_id=str(event.requested_by or "system"),
                email="",
                organization_id=event.organization_id,
            )
            contacts_service = ContactsService(
                db_connection=self.db_connection,
                user_context=user_context,
                supabase_client=supabase_client,
            )
            organization = await contacts_service.org_repo.get_organization_by_id(
                event.organization_id
            )
            org_name = self._resolve_org_name(organization=organization)

            await self._process_event_batches(
                event=event,
                batch_size=batch_size,
                mapping=mapping,
                options=options,
                job_internal_id=job_internal_id,
                rows_repo=rows_repo,
                logs_repo=logs_repo,
                contacts_service=contacts_service,
                user_context=user_context,
                org_name=org_name,
                totals=totals,
            )

            finished_at_dt = datetime.now(UTC)
            finished_at = finished_at_dt.isoformat()
            await job_repo.set_status_and_timestamps(
                job_id=event.job_key,
                organization_id=event.organization_id,
                status=ContactsImportJobStatus.COMPLETED.value,
                finished_at=finished_at_dt,
            )
            await logs_repo.upsert_payload(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                payload={
                    "phase": "finished",
                    "action": str(event.action).lower(),
                    "finished_at": finished_at,
                    "stats": {
                        "processed": totals.processed_total,
                        "success": totals.success_total,
                        "errors": totals.errors_total,
                    },
                },
            )

            elapsed_s = max(0.0, (finished_at_dt - started_at_dt).total_seconds())
            logger.info(
                "contacts_import_service_job_completed",
                extra={
                    "job_key": str(event.job_key),
                    "job_id": job_internal_id,
                    "organization_id": str(event.organization_id),
                    "elapsed_ms": int(elapsed_s * 1000),
                    "processed": totals.processed_total,
                    "success": totals.success_total,
                    "errors": totals.errors_total,
                },
            )
        except Exception as exc:
            finished_at_dt = datetime.now(UTC)
            finished_at = finished_at_dt.isoformat()
            # Best-effort: do not let status/log persistence errors mask the original failure.
            try:
                await job_repo.set_status_and_timestamps(
                    job_id=event.job_key,
                    organization_id=event.organization_id,
                    status=ContactsImportJobStatus.FAILED.value,
                    finished_at=finished_at_dt,
                )
            except Exception:
                pass
            try:
                await logs_repo.upsert_payload(
                    organization_id=event.organization_id,
                    job_id=job_internal_id,
                    payload={
                        "phase": "failed",
                        "action": str(event.action).lower(),
                        "finished_at": finished_at,
                        "error": {"message": str(exc)[:2000]},
                        "stats": {
                            "processed": totals.processed_total,
                            "success": totals.success_total,
                            "errors": totals.errors_total,
                        },
                    },
                )
            except Exception:
                pass
            elapsed_s = max(0.0, (finished_at_dt - started_at_dt).total_seconds())
            logger.exception(
                "contacts_import_service_job_failed",
                exc_info=exc,
                extra={
                    "job_key": str(event.job_key),
                    "job_id": job_internal_id,
                    "organization_id": str(event.organization_id),
                    "elapsed_ms": int(elapsed_s * 1000),
                    "processed": totals.processed_total,
                    "success": totals.success_total,
                    "errors": totals.errors_total,
                },
            )
            raise

    @staticmethod
    def _safe_parse_json_dict(raw: Any) -> dict[str, Any]:
        """Parse a JSON-ish DB field into a dict, returning `{}` on errors."""
        try:
            parsed = parse_json_field(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _resolve_org_name(*, organization: dict[str, Any] | None) -> str:
        """Resolve organization name with shared settings fallback."""
        return str(
            (organization or {}).get("name")
            or getattr(
                __import__(
                    "apps.user_service.app.config.app_settings",
                    fromlist=["shared_settings"],
                ).shared_settings,
                "company_name",
                "",
            )
            or ""
        )

    @staticmethod
    def _build_mark_error_tuple(
        *,
        row_number: int,
        code: str,
        message: str,
        raw_row: dict[str, Any] | None,
    ) -> tuple[int, str, str, dict[str, Any] | None]:
        """Build a row-ledger error tuple in the repository contract format."""
        return (int(row_number), str(code), str(message), raw_row)

    @staticmethod
    def _extract_claim_rows(
        *, batch: list[dict[str, Any]]
    ) -> list[tuple[int, dict[str, Any] | None]]:
        """Build the `claim_rows_processing` input list from a validated batch."""
        claim_rows: list[tuple[int, dict[str, Any] | None]] = []
        for item in batch:
            row_number = int(item["row_number"])
            claim_rows.append((row_number, item.get("raw_row") if item.get("error") else None))
        return claim_rows

    @staticmethod
    def _filter_claimed_by_status(
        *,
        batch: list[dict[str, Any]],
        statuses: dict[int, str],
    ) -> list[dict[str, Any]]:
        """Filter out rows already marked as success by the ledger."""
        return [item for item in batch if statuses.get(int(item["row_number"])) != "success"]

    @staticmethod
    def _collect_emails_for_uniqueness_check(
        *, claimed: list[dict[str, Any]]
    ) -> tuple[list[str], dict[int, str]]:
        """Collect normalized emails for DB uniqueness checks (row_number -> email)."""
        emails_to_check: list[str] = []
        email_by_row: dict[int, str] = {}
        for item in claimed:
            if item.get("error") or item.get("contact_model") is None:
                continue
            email_norm = str(item["contact_model"].email or "").strip().lower()
            if email_norm:
                email_by_row[int(item["row_number"])] = email_norm
                emails_to_check.append(email_norm)
        return emails_to_check, email_by_row

    async def _apply_duplicate_email_errors(
        self,
        *,
        organization_id: str,
        job_internal_id: str,
        rows_repo: ImportJobRowsRepository,
        claimed: list[dict[str, Any]],
        totals: _ContactsImportTotals,
    ) -> list[dict[str, Any]]:
        """Mark duplicate-email rows as errors and return remaining claimed rows."""
        emails_to_check, email_by_row = self._collect_emails_for_uniqueness_check(claimed=claimed)
        if not emails_to_check:
            return claimed

        contacts_repo = ContactsRepository(db_connection=self.db_connection)
        existing = await contacts_repo.get_contact_ids_by_emails(
            organization_id=organization_id,
            emails=emails_to_check,
        )
        if not existing:
            return claimed

        duplicate_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []
        for row_number, email_norm in email_by_row.items():
            if email_norm in existing:
                duplicate_errors.append(
                    self._build_mark_error_tuple(
                        row_number=int(row_number),
                        code="email_already_exists",
                        message="contacts.errors.email_already_exists",
                        raw_row={
                            "email": email_norm,
                            "client_id": existing[email_norm],
                        },
                    )
                )
        if not duplicate_errors:
            return claimed

        await rows_repo.mark_errors_bulk(
            organization_id=organization_id,
            job_id=job_internal_id,
            errors=duplicate_errors,
        )
        totals.processed_total += len(duplicate_errors)
        totals.errors_total += len(duplicate_errors)

        duplicate_row_numbers = {row_number for row_number, _, _, _ in duplicate_errors}
        return [item for item in claimed if int(item["row_number"]) not in duplicate_row_numbers]

    async def _mark_validation_errors_and_collect_valid(
        self,
        *,
        organization_id: str,
        job_internal_id: str,
        rows_repo: ImportJobRowsRepository,
        claimed: list[dict[str, Any]],
        totals: _ContactsImportTotals,
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """Persist row-level validation errors and return only valid rows."""
        valid_rows: list[dict[str, Any]] = []
        valid_row_numbers: list[int] = []
        validation_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []
        for item in claimed:
            if item.get("error"):
                err = item["error"]
                validation_errors.append(
                    self._build_mark_error_tuple(
                        row_number=int(item["row_number"]),
                        code=str(err.get("code") or "validation_error"),
                        message=str(err.get("message") or "row validation failed"),
                        raw_row=item.get("raw_row"),
                    )
                )
                continue
            valid_rows.append(item)
            valid_row_numbers.append(int(item["row_number"]))

        if validation_errors:
            await rows_repo.mark_errors_bulk(
                organization_id=organization_id,
                job_id=job_internal_id,
                errors=validation_errors,
            )
            totals.processed_total += len(validation_errors)
            totals.errors_total += len(validation_errors)

        return valid_rows, valid_row_numbers

    async def _provision_identities_sequential(
        self,
        *,
        organization_id: str,
        job_internal_id: str,
        rows_repo: ImportJobRowsRepository,
        contacts_service: ContactsService,
        claimed: list[dict[str, Any]],
        totals: _ContactsImportTotals,
    ) -> dict[int, tuple[str, str, str | None]]:
        """Provision auth identities for claimed rows (sequential on a single DB connection)."""
        identity_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []
        identity_results: dict[int, tuple[str, str, str | None]] = {}

        for item in claimed:
            if not item.get("contact_model"):
                continue
            row_number = int(item["row_number"])
            model: CreateContactRequest = item["contact_model"]
            try:
                (
                    user_id,
                    isometrik_user_id,
                    created_password,
                ) = await contacts_service._provision_contact_auth_identity(
                    email=str(model.email or "").strip().lower(),
                    first_name=model.first_name,
                    last_name=model.last_name,
                    prefix=model.prefix,
                )
                identity_results[row_number] = (user_id, isometrik_user_id, created_password)
            except Exception as exc:
                identity_errors.append(
                    self._build_mark_error_tuple(
                        row_number=row_number,
                        code="external_service_error",
                        message=str(exc)[:2000],
                        raw_row=None,
                    )
                )

        if identity_errors:
            await rows_repo.mark_errors_bulk(
                organization_id=organization_id,
                job_id=job_internal_id,
                errors=identity_errors,
            )
            totals.processed_total += len(identity_errors)
            totals.errors_total += len(identity_errors)

        return identity_results

    async def _ensure_companies_cached(
        self,
        *,
        organization_id: str,
        company_repo: CompaniesRepository,
        desired_names: list[str],
        totals: _ContactsImportTotals,
    ) -> None:
        """Ensure `totals.company_cache` contains ids for all desired company names."""
        missing_norms: list[str] = []
        for name in desired_names:
            norm = name.strip().lower()
            if norm and norm not in totals.company_cache:
                missing_norms.append(norm)

        if missing_norms:
            existing_companies = await company_repo.get_company_ids_by_names(
                organization_id=organization_id,
                names=missing_norms,
            )
            totals.company_cache.update(existing_companies)

        to_create: list[str] = []
        for name in desired_names:
            norm = name.strip().lower()
            if norm and norm not in totals.company_cache and norm not in to_create:
                to_create.append(norm)

        if not to_create:
            return

        created = await company_repo.create_companies(
            [
                {
                    "organization_id": organization_id,
                    "name": n,
                    "status": "active",
                    "portal_access": False,
                    "phones": [],
                    "websites": [],
                    "billing_preferences": {},
                    "social_pages": [],
                    "custom_fields": [],
                    "additional_data": {},
                }
                for n in to_create
            ]
        )
        for row in created:
            name_norm = str(row.get("name") or "").strip().lower()
            cid = str(row.get("id") or "")
            if name_norm and cid and name_norm not in totals.company_cache:
                totals.company_cache[name_norm] = cid

    @staticmethod
    def _attach_identity_and_company_to_rows(
        *,
        provisioned_claimed: list[dict[str, Any]],
        identity_results: dict[int, tuple[str, str, str | None]],
        company_cache: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """Attach identity+company_id fields and return (valid_rows, valid_row_numbers)."""
        valid_rows: list[dict[str, Any]] = []
        valid_row_numbers: list[int] = []
        for item in provisioned_claimed:
            row_number = int(item["row_number"])
            item["identity"] = identity_results[row_number]
            cname = (item.get("company_name") or "").strip()
            if cname:
                item["company_id"] = company_cache.get(cname.lower())
            valid_rows.append(item)
            valid_row_numbers.append(row_number)
        return valid_rows, valid_row_numbers

    async def _persist_contacts_for_rows(
        self,
        *,
        event: ContactsImportEventPayload,
        job_internal_id: str,
        rows_repo: ImportJobRowsRepository,
        logs_repo: ImportJobLogsRepository,
        contacts_service: ContactsService,
        user_context: UserContext,
        org_name: str,
        valid_rows: list[dict[str, Any]],
        valid_row_numbers: list[int],
        totals: _ContactsImportTotals,
    ) -> None:
        """Persist contacts+addresses+leads for valid rows, mirroring existing behavior."""
        try:
            await self._persist_contacts_for_rows_impl(
                event=event,
                job_internal_id=job_internal_id,
                rows_repo=rows_repo,
                contacts_service=contacts_service,
                user_context=user_context,
                org_name=org_name,
                valid_rows=valid_rows,
                valid_row_numbers=valid_row_numbers,
                totals=totals,
            )
        except Exception as exc:
            err_msg = str(exc)[:2000]
            jobs_repo = ImportJobsRepository(db_connection=self.db_connection)
            await rows_repo.mark_errors_bulk(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                errors=[(int(rn), "db_error", err_msg, None) for rn in valid_row_numbers],
            )
            totals.processed_total += len(valid_row_numbers)
            totals.errors_total += len(valid_row_numbers)
            await jobs_repo.increment_counters(
                job_id=event.job_key,
                organization_id=event.organization_id,
                total_rows_delta=len(valid_row_numbers),
                processed_rows_delta=len(valid_row_numbers),
                success_rows_delta=0,
                error_rows_delta=len(valid_row_numbers),
            )
            await logs_repo.upsert_payload(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                payload={
                    "phase": "warning",
                    "message": "batch_failed_marked_rows_error_no_fallback",
                    "error": {"message": err_msg},
                    "stats": {
                        "processed": totals.processed_total,
                        "success": totals.success_total,
                        "errors": totals.errors_total,
                    },
                },
            )

    async def _validate_custom_fields_for_rows(
        self,
        *,
        valid_rows: list[dict[str, Any]],
        user_context: UserContext,
    ) -> tuple[dict[int, list[dict[str, Any]]], list[tuple[int, str, str, dict[str, Any] | None]]]:
        """Validate custom fields for each row, returning (by_row, errors)."""
        cf_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=user_context,
        )
        cf_sem = asyncio.Semaphore(10)
        custom_fields_by_row: dict[int, list[dict[str, Any]]] = {}
        cf_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []

        # Important: validate_for_create performs DB reads; keep sequential on this connection.
        for item in valid_rows:
            row_number = int(item["row_number"])
            model: CreateContactRequest = item["contact_model"]
            try:
                async with cf_sem:
                    validated = await cf_service.validate_for_create(
                        model.custom_fields,
                        EntityType.CONTACT,
                    )
                custom_fields_by_row[row_number] = validated
            except Exception as exc:
                cf_errors.append(
                    self._build_mark_error_tuple(
                        row_number=row_number,
                        code="validation_error",
                        message=str(exc)[:2000],
                        raw_row=None,
                    )
                )
        return custom_fields_by_row, cf_errors

    @staticmethod
    def _remove_rows_by_row_numbers(
        *,
        valid_rows: list[dict[str, Any]],
        valid_row_numbers: list[int],
        bad_rows: set[int],
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """Remove rows by row_number from both lists."""
        filtered_rows = [item for item in valid_rows if int(item["row_number"]) not in bad_rows]
        filtered_numbers = [
            row_number for row_number in valid_row_numbers if row_number not in bad_rows
        ]
        return filtered_rows, filtered_numbers

    async def _build_contacts_insert_payloads(
        self,
        *,
        event: ContactsImportEventPayload,
        valid_rows: list[dict[str, Any]],
        custom_fields_by_row: dict[int, list[dict[str, Any]]],
    ) -> tuple[
        list[dict[str, Any]],
        dict[int, str],
        dict[int, str | None],
        dict[int, bool],
        dict[int, str],
    ]:
        """Build contact insert rows and per-row bookkeeping dicts."""
        rows_to_insert: list[dict[str, Any]] = []
        user_id_by_row: dict[int, str] = {}
        password_by_row: dict[int, str | None] = {}
        portal_by_row: dict[int, bool] = {}
        email_by_row: dict[int, str] = {}

        for item in valid_rows:
            row_number = int(item["row_number"])
            model = item["contact_model"]
            user_id, isometrik_user_id, created_password = item["identity"]

            phones_payload = ContactsService._ensure_list_item_ids(
                [p.model_dump(mode="json", exclude_none=True) for p in model.phones]
            )
            social_pages_payload = ContactsService._ensure_list_item_ids(
                [p.model_dump(mode="json", exclude_none=True) for p in model.social_pages]
            )
            websites_payload = ContactsService._ensure_list_item_ids(
                [w.model_dump(mode="json", exclude_none=True) for w in model.websites]
            )

            additional_data_payload = dict(model.additional_data or {})
            lead_payload = getattr(model, "lead", None)
            if lead_payload is not None and getattr(lead_payload, "intake_stage", None) is not None:
                intake_stage = (getattr(lead_payload, "intake_stage", None) or "").strip()
                if intake_stage:
                    additional_data_payload["intake_stage"] = intake_stage
            if websites_payload:
                additional_data_payload["websites"] = websites_payload

            jsonb_inputs: dict[str, Any] = {
                "phones": phones_payload,
                "custom_fields": custom_fields_by_row.get(row_number, []),
                "additional_data": additional_data_payload,
                "social_pages": social_pages_payload,
            }
            jsonb_params: dict[str, Any] = {}
            for field_name, field_value in jsonb_inputs.items():
                jsonb_params[field_name] = serialize_jsonb_param(
                    field_name,
                    field_value,
                    CONTACT_JSONB_COLUMNS,
                )

            row_payload = {
                "organization_id": event.organization_id,
                "user_id": user_id,
                "isometrik_user_id": isometrik_user_id,
                "status": "active",
                "prefix": model.prefix,
                "first_name": model.first_name,
                "middle_name": model.middle_name,
                "last_name": model.last_name,
                "title": model.title,
                "date_of_birth": model.date_of_birth,
                "profile_photo_url": model.profile_photo_url,
                "phones": jsonb_params["phones"],
                "tags": model.tags,
                "custom_fields": jsonb_params["custom_fields"],
                "additional_data": jsonb_params["additional_data"],
                "social_pages": jsonb_params["social_pages"],
            }
            rows_to_insert.append(row_payload)
            user_id_by_row[row_number] = user_id
            password_by_row[row_number] = created_password
            portal_by_row[row_number] = bool(model.portal_access)
            email_by_row[row_number] = str(model.email or "").strip().lower()

        return rows_to_insert, user_id_by_row, password_by_row, portal_by_row, email_by_row

    async def _insert_addresses_for_valid_rows(
        self,
        *,
        contacts_repo: ContactsRepository,
        valid_rows: list[dict[str, Any]],
        user_id_by_row: dict[int, str],
        contact_ids_by_user: dict[str, str],
    ) -> None:
        """Insert contact addresses and annotate `valid_rows` with `contact_id`."""
        address_rows: list[dict[str, Any]] = []
        for item in valid_rows:
            row_number = int(item["row_number"])
            uid = user_id_by_row.get(row_number)
            contact_id = str(contact_ids_by_user.get(uid or "") or "")
            if not uid or not contact_id:
                continue
            model = item["contact_model"]
            for addr in model.addresses or []:
                address_rows.append(
                    {"contact_id": contact_id, **addr.model_dump(exclude_none=True)}
                )
            item["contact_id"] = contact_id

        if address_rows:
            await contacts_repo.create_contact_addresses(address_rows)

    @staticmethod
    def _build_lead_items_for_rows(*, valid_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build lead items list for `BulkLeadCreator`."""
        owner_id = None
        lead_items: list[dict[str, Any]] = []
        for item in valid_rows:
            model = item["contact_model"]
            lead_payload = getattr(model, "lead", None)
            contact_id = item.get("contact_id")
            if lead_payload is None or not contact_id:
                continue
            full_name = " ".join(
                [
                    p
                    for p in [
                        (model.first_name or "").strip(),
                        (model.last_name or "").strip(),
                    ]
                    if p
                ]
            ).strip()
            lead_name = full_name or (str(model.email or "").strip().lower())
            lead_items.append(
                {
                    "row_number": int(item["row_number"]),
                    "name": lead_name,
                    "stage_id": str(getattr(lead_payload, "stage_id", "") or ""),
                    "lead_source": (getattr(lead_payload, "intake_stage", None) or None),
                    "lead_score": (getattr(lead_payload, "lead_score", None) or None),
                    "owner_id": owner_id,
                    "contact_id": str(contact_id),
                    "company_id": str(item.get("company_id") or "") or None,
                }
            )
        return lead_items

    @staticmethod
    def _success_row_numbers_for_rows(
        *, valid_rows: list[dict[str, Any]], lead_ids_by_row: dict[int, str]
    ) -> list[int]:
        """Compute row_numbers to mark as success (respecting lead presence)."""
        success_row_numbers: list[int] = []
        for item in valid_rows:
            row_number = int(item["row_number"])
            model = item["contact_model"]
            wants_lead = getattr(model, "lead", None) is not None
            if wants_lead and row_number not in lead_ids_by_row:
                continue
            if item.get("contact_id"):
                success_row_numbers.append(row_number)
        return success_row_numbers

    async def _persist_contacts_for_rows_impl(
        self,
        *,
        event: ContactsImportEventPayload,
        job_internal_id: str,
        rows_repo: ImportJobRowsRepository,
        contacts_service: ContactsService,
        user_context: UserContext,
        org_name: str,
        valid_rows: list[dict[str, Any]],
        valid_row_numbers: list[int],
        totals: _ContactsImportTotals,
    ) -> None:
        """Internal implementation for persisting a batch; raises on failure."""
        contacts_repo = ContactsRepository(db_connection=self.db_connection)
        jobs_repo = ImportJobsRepository(db_connection=self.db_connection)

        custom_fields_by_row, cf_errors = await self._validate_custom_fields_for_rows(
            valid_rows=valid_rows,
            user_context=user_context,
        )
        if cf_errors:
            await rows_repo.mark_errors_bulk(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                errors=cf_errors,
            )
            totals.processed_total += len(cf_errors)
            totals.errors_total += len(cf_errors)
            bad_rows = {row_number for row_number, _, _, _ in cf_errors}
            valid_rows, valid_row_numbers = self._remove_rows_by_row_numbers(
                valid_rows=valid_rows,
                valid_row_numbers=valid_row_numbers,
                bad_rows=bad_rows,
            )

            if not valid_rows:
                return

        (
            rows_to_insert,
            user_id_by_row,
            password_by_row,
            portal_by_row,
            email_by_row,
        ) = await self._build_contacts_insert_payloads(
            event=event,
            valid_rows=valid_rows,
            custom_fields_by_row=custom_fields_by_row,
        )

        await contacts_repo.create_contacts(rows_to_insert)

        contact_ids_by_user = await contacts_repo.get_contact_ids_by_user_ids(
            organization_id=event.organization_id,
            user_ids=list(user_id_by_row.values()),
        )

        await self._insert_addresses_for_valid_rows(
            contacts_repo=contacts_repo,
            valid_rows=valid_rows,
            user_id_by_row=user_id_by_row,
            contact_ids_by_user=contact_ids_by_user,
        )

        processed_count = len(rows_to_insert)
        success_count = sum(
            1
            for rn in valid_row_numbers
            if contact_ids_by_user.get(user_id_by_row.get(int(rn), "") or "")
        )
        error_count = max(processed_count - success_count, 0)
        await jobs_repo.increment_counters(
            job_id=event.job_key,
            organization_id=event.organization_id,
            total_rows_delta=processed_count,
            processed_rows_delta=processed_count,
            success_rows_delta=success_count,
            error_rows_delta=error_count,
        )
        totals.processed_total += processed_count
        totals.success_total += success_count
        totals.errors_total += error_count

        for row_number in valid_row_numbers:
            if portal_by_row.get(row_number) and email_by_row.get(row_number):
                contacts_service._maybe_send_contact_creation_email(
                    portal_access=True,
                    email=email_by_row[row_number],
                    organization_name=org_name,
                    password=password_by_row.get(row_number),
                )

        lead_items = self._build_lead_items_for_rows(valid_rows=valid_rows)

        lead_ids_by_row: dict[int, str] = {}
        lead_errors: list[tuple[int, str]] = []
        if lead_items:
            bulk_leads = BulkLeadCreator(db_connection=self.db_connection)
            lead_ids_by_row, lead_errors = await bulk_leads.create_leads_for_rows(
                organization_id=event.organization_id,
                rows=lead_items,
            )

        if lead_errors:
            error_rows_payload: list[tuple[int, str, str, dict[str, Any] | None]] = []
            for row_number, msg in lead_errors:
                error_rows_payload.append(
                    self._build_mark_error_tuple(
                        row_number=int(row_number),
                        code="lead_error",
                        message=str(msg),
                        raw_row=None,
                    )
                )
                bad = next(
                    (item for item in valid_rows if int(item["row_number"]) == int(row_number)),
                    None,
                )
                if bad and bad.get("contact_id"):
                    with contextlib.suppress(Exception):
                        await contacts_repo.delete_all_contact_addresses(
                            contact_id=str(bad["contact_id"]),
                        )
                    with contextlib.suppress(Exception):
                        await contacts_repo.soft_delete_contact(
                            contact_id=str(bad["contact_id"]),
                            organization_id=event.organization_id,
                        )
            await rows_repo.mark_errors_bulk(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                errors=error_rows_payload,
            )
            totals.processed_total += len(lead_errors)
            totals.errors_total += len(lead_errors)
            await jobs_repo.increment_counters(
                job_id=event.job_key,
                organization_id=event.organization_id,
                total_rows_delta=0,
                processed_rows_delta=0,
                success_rows_delta=-len(lead_errors),
                error_rows_delta=len(lead_errors),
            )
            totals.success_total -= len(lead_errors)

        success_row_numbers = self._success_row_numbers_for_rows(
            valid_rows=valid_rows,
            lead_ids_by_row=lead_ids_by_row,
        )
        if success_row_numbers:
            await rows_repo.mark_success_bulk(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                row_numbers=success_row_numbers,
            )

    async def _process_event_batches(
        self,
        *,
        event: ContactsImportEventPayload,
        batch_size: int,
        mapping: dict[str, Any],
        options: dict[str, Any],
        job_internal_id: str,
        rows_repo: ImportJobRowsRepository,
        logs_repo: ImportJobLogsRepository,
        contacts_service: ContactsService,
        user_context: UserContext,
        org_name: str,
        totals: _ContactsImportTotals,
    ) -> None:
        """Iterate CSV batches and apply row-ledger import side effects."""
        async for batch in self._iter_validated_rows_for_ledger(
            file_url=event.file_url,
            mapping=mapping,
            options=options,
            batch_size=batch_size,
        ):
            if not batch:
                continue

            # Claim row ledger entries in bulk, skipping already-successful rows.
            claim_rows = self._extract_claim_rows(batch=batch)
            statuses = await rows_repo.claim_rows_processing(
                organization_id=event.organization_id,
                job_id=job_internal_id,
                rows=claim_rows,
            )

            claimed = self._filter_claimed_by_status(batch=batch, statuses=statuses)

            if not claimed:
                continue

            # Bulk email uniqueness check
            # (parity with ContactsService._assert_contact_email_unique).
            claimed = await self._apply_duplicate_email_errors(
                organization_id=event.organization_id,
                job_internal_id=job_internal_id,
                rows_repo=rows_repo,
                claimed=claimed,
                totals=totals,
            )

            # Record validation errors immediately (bulk).
            valid_rows, valid_row_numbers = await self._mark_validation_errors_and_collect_valid(
                organization_id=event.organization_id,
                job_internal_id=job_internal_id,
                rows_repo=rows_repo,
                claimed=claimed,
                totals=totals,
            )

            # Identity provisioning in parallel
            # (parity with ContactsService._provision_contact_auth_identity).
            # Important: asyncpg connections do not support concurrent operations.
            # Provisioning touches DB, so keep it sequential on this single connection.
            identity_results = await self._provision_identities_sequential(
                organization_id=event.organization_id,
                job_internal_id=job_internal_id,
                rows_repo=rows_repo,
                contacts_service=contacts_service,
                claimed=claimed,
                totals=totals,
            )

            # Keep only rows with identity provisioned.
            provisioned_claimed = [
                item
                for item in claimed
                if int(item["row_number"]) in identity_results and item.get("contact_model")
            ]

            # Company reuse by name (best-effort): create once per normalized name and reuse id.
            # Cache is per job execution.
            company_repo = CompaniesRepository(db_connection=self.db_connection)
            desired_names = []
            for item in provisioned_claimed:
                name = (item.get("company_name") or "").strip()
                if name:
                    desired_names.append(name)
            await self._ensure_companies_cached(
                organization_id=event.organization_id,
                company_repo=company_repo,
                desired_names=desired_names,
                totals=totals,
            )

            valid_rows, valid_row_numbers = self._attach_identity_and_company_to_rows(
                provisioned_claimed=provisioned_claimed,
                identity_results=identity_results,
                company_cache=totals.company_cache,
            )

            if valid_rows:
                await self._persist_contacts_for_rows(
                    event=event,
                    job_internal_id=job_internal_id,
                    rows_repo=rows_repo,
                    logs_repo=logs_repo,
                    contacts_service=contacts_service,
                    user_context=user_context,
                    org_name=org_name,
                    valid_rows=valid_rows,
                    valid_row_numbers=valid_row_numbers,
                    totals=totals,
                )

            now_ts = time.time()
            if (now_ts - totals.last_log_ts) >= 2.0:
                await logs_repo.upsert_payload(
                    organization_id=event.organization_id,
                    job_id=job_internal_id,
                    payload={
                        "phase": "running",
                        "action": str(event.action).lower(),
                        "stats": {
                            "processed": totals.processed_total,
                            "success": totals.success_total,
                            "errors": totals.errors_total,
                        },
                    },
                )
                totals.last_log_ts = now_ts

    async def _iter_validated_rows_for_ledger(
        self,
        *,
        file_url: str,
        mapping: dict[str, Any],
        options: dict[str, Any],
        batch_size: int,
    ):
        """Yield batches of row work items for row-ledger processing.

        This function:
        - Downloads the CSV
        - Applies mapping (canonical field -> column header)
        - Uses ``CreateContactRequest`` to enforce the same schema-level validation
          as single-contact creation
        - Yields per-row metadata (row_number, raw_row, contact_model OR validation error)
        """
        if not file_url:
            return

        has_header = bool((options or {}).get("has_header", True))

        # Stream download to a temp file to avoid loading large CSVs into memory.
        tmp_path = await self._download_csv_to_tmp(file_url=file_url)
        if not tmp_path:
            return
        try:
            with open(tmp_path, newline="", encoding="utf-8", errors="replace") as f:
                reader = self._build_csv_reader(
                    f=f,
                    has_header=has_header,
                    mapping=mapping,
                )
                reverse_mapping = self._build_reverse_mapping(mapping=mapping)
                async for batch in self._iter_row_batches(
                    reader=reader,
                    reverse_mapping=reverse_mapping,
                    batch_size=batch_size,
                ):
                    yield batch
        finally:
            with contextlib.suppress(Exception):
                os.remove(tmp_path)

    async def _download_csv_to_tmp(self, *, file_url: str) -> str | None:
        """Download CSV from `file_url` to a temporary file path."""
        tmp_path: str | None = None
        with tempfile.NamedTemporaryFile(
            prefix="contacts_import_",
            suffix=".csv",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name

        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", file_url) as response:
                response.raise_for_status()
                with open(tmp_path, "wb") as out:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            out.write(chunk)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return None
        return tmp_path

    @staticmethod
    def _build_csv_reader(
        *,
        f: Any,
        has_header: bool,
        mapping: dict[str, Any],
    ) -> csv.DictReader:
        """Build a `csv.DictReader` matching header/mapping expectations."""
        if has_header:
            return csv.DictReader(f)
        fieldnames = list(mapping.values()) or None
        return csv.DictReader(f, fieldnames=fieldnames)

    @staticmethod
    def _build_reverse_mapping(*, mapping: dict[str, Any]) -> dict[str, str]:
        """Invert canonical->header mapping into header->canonical mapping."""
        reverse_mapping: dict[str, str] = {}
        for canonical, header in (mapping or {}).items():
            if header:
                reverse_mapping[str(header)] = str(canonical)
        return reverse_mapping

    async def _iter_row_batches(
        self,
        *,
        reader: Any,
        reverse_mapping: dict[str, str],
        batch_size: int,
    ):
        """Yield batches of row items sized by `batch_size`."""
        batch: list[dict[str, Any]] = []
        async for row_item in self._iter_row_items(
            reader=reader,
            reverse_mapping=reverse_mapping,
        ):
            batch.append(row_item)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    async def _iter_row_items(self, *, reader: Any, reverse_mapping: dict[str, str]):
        """Yield row work items: row_number + raw_row + validated model or error."""
        for row_number, row in enumerate(reader, start=1):
            if not isinstance(row, dict):
                continue
            canonical = self._canonicalize_row(row=row, reverse_mapping=reverse_mapping)
            email_raw = (canonical.get("email") or "").strip()
            if not email_raw:
                yield self._row_item_missing_email(row_number=row_number, raw_row=canonical)
                continue

            company_name = (canonical.get("company_name") or "").strip() or None
            body_dict = self._build_contact_body_dict(canonical=canonical, email_raw=email_raw)
            yield self._validate_row_body(
                row_number=row_number,
                canonical=canonical,
                company_name=company_name,
                body_dict=body_dict,
            )

    @staticmethod
    def _canonicalize_row(
        *, row: dict[str, Any], reverse_mapping: dict[str, str]
    ) -> dict[str, Any]:
        """Map CSV header keys to canonical schema keys."""
        canonical: dict[str, Any] = {}
        for header, value in row.items():
            canonical_key = reverse_mapping.get(header, header)
            canonical[canonical_key] = value
        return canonical

    @staticmethod
    def _row_item_missing_email(*, row_number: int, raw_row: dict[str, Any]) -> dict[str, Any]:
        """Build a row work item for missing-email failures."""
        return {
            "row_number": row_number,
            "raw_row": raw_row,
            "error": {"code": "missing_email", "message": "email is required"},
            "contact_model": None,
        }

    def _build_contact_body_dict(
        self,
        *,
        canonical: dict[str, Any],
        email_raw: str,
    ) -> dict[str, Any]:
        """Build the `CreateContactRequest` body dict from canonicalized row data."""
        body_dict: dict[str, Any] = {
            "email": email_raw,
            "portal_access": self._parse_portal_access(canonical.get("portal_access")),
            "prefix": (canonical.get("prefix") or "").strip() or None,
            "first_name": (canonical.get("first_name") or "").strip() or None,
            "middle_name": (canonical.get("middle_name") or "").strip() or None,
            "last_name": (canonical.get("last_name") or "").strip() or None,
            "title": (canonical.get("title") or "").strip() or None,
            "profile_photo_url": (canonical.get("profile_photo_url") or "").strip() or None,
        }

        dob_raw = (canonical.get("date_of_birth") or "").strip()
        if dob_raw:
            body_dict["date_of_birth"] = dob_raw

        body_dict["phones"] = coerce_json_list(canonical.get("phones_json"))
        body_dict["tags"] = coerce_json_list(canonical.get("tags_json"))
        body_dict["social_pages"] = coerce_json_list(canonical.get("social_pages_json"))

        websites = coerce_json_list(canonical.get("websites_json"))
        additional_data_from_row = self._parse_json_object(canonical.get("additional_data_json"))
        if websites:
            additional_data_from_row.setdefault("websites", websites)
        body_dict["additional_data"] = additional_data_from_row
        body_dict["custom_fields"] = coerce_json_list(canonical.get("custom_fields_json"))
        body_dict["addresses"] = coerce_json_list(canonical.get("addresses_json"))

        lead_obj = self._parse_json_object(canonical.get("lead_json"))
        if lead_obj:
            body_dict["lead"] = lead_obj

        return body_dict

    @staticmethod
    def _parse_portal_access(raw: Any) -> bool:
        """Parse truthy values for `portal_access` from CSV cell values."""
        return str(raw or "").strip().lower() in {"true", "1", "yes"}

    @staticmethod
    def _parse_json_object(raw: Any) -> dict[str, Any]:
        """Parse a JSON value into a dict, returning `{}` if not a dict."""
        parsed = parse_json_any(raw, default={})
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _validate_row_body(
        *,
        row_number: int,
        canonical: dict[str, Any],
        company_name: str | None,
        body_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate the row body via `CreateContactRequest`, returning a work item."""
        try:
            model = CreateContactRequest.model_validate(body_dict)
            return {
                "row_number": row_number,
                "raw_row": canonical,
                "error": None,
                "contact_model": model,
                "company_name": company_name,
            }
        except Exception as exc:
            return {
                "row_number": row_number,
                "raw_row": canonical,
                "error": {"code": "validation_error", "message": str(exc)[:2000]},
                "contact_model": None,
                "company_name": company_name,
            }

    async def _build_contact_row_from_model(
        self,
        *,
        row_model: CreateContactRequest,
    ) -> dict[str, Any]:
        """Convert a validated contact schema into a DB row payload.

        This mirrors the field mapping behavior of ``ContactsService.create_contact`` for
        scalar and JSONB fields, but omits auth/Isometrik/lead/company side effects.
        """
        phones_payload = [
            phone.model_dump(mode="json", exclude_none=True) for phone in row_model.phones
        ]
        social_pages_payload = [
            page.model_dump(mode="json", exclude_none=True) for page in row_model.social_pages
        ]
        websites_payload = [
            website.model_dump(mode="json", exclude_none=True) for website in row_model.websites
        ]

        additional_data_payload = dict(row_model.additional_data or {})
        if websites_payload:
            additional_data_payload["websites"] = websites_payload

        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=None,
        )
        validated_custom_fields = await custom_field_service.validate_for_create(
            row_model.custom_fields,
            EntityType.CONTACT,
        )

        jsonb_inputs: dict[str, Any] = {
            "phones": phones_payload,
            "custom_fields": validated_custom_fields,
            "additional_data": additional_data_payload,
            "social_pages": social_pages_payload,
        }
        jsonb_params: dict[str, Any] = {}
        for field_name, field_value in jsonb_inputs.items():
            jsonb_params[field_name] = serialize_jsonb_param(
                field_name,
                field_value,
                CONTACT_JSONB_COLUMNS,
            )

        return {
            "status": "active",
            "prefix": row_model.prefix,
            "first_name": row_model.first_name,
            "middle_name": row_model.middle_name,
            "last_name": row_model.last_name,
            "title": row_model.title,
            "date_of_birth": row_model.date_of_birth,
            "profile_photo_url": row_model.profile_photo_url,
            "phones": jsonb_params["phones"],
            "tags": row_model.tags,
            "custom_fields": jsonb_params["custom_fields"],
            "additional_data": jsonb_params["additional_data"],
            "social_pages": jsonb_params["social_pages"],
            "email": (row_model.email or "").strip().lower(),
        }

    async def process_contacts_batch(
        self,
        *,
        job_id: str,
        organization_id: str,
        contacts: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Persist a batch of contacts and update job progress counters.

        This method is consumer-facing: it assumes that *contacts* already
        contain normalized payloads compatible with ``ContactsRepository``
        and ensures that:
        - contacts are inserted in bulk to minimize DB round trips
        - ``import_jobs`` progress counters are incremented atomically
        """
        if not contacts:
            return {"total": 0, "processed": 0, "success": 0, "errors": 0}

        # Ensure organization_id is set on every contact row.
        rows: list[dict[str, Any]] = []
        for contact in contacts:
            row = dict(contact)
            row.setdefault("organization_id", organization_id)
            rows.append(row)

        contacts_repo = ContactsRepository(db_connection=self.db_connection)
        inserted_rows = await contacts_repo.create_contacts(rows)

        total = len(rows)
        success = len(inserted_rows)
        errors = max(total - success, 0)

        jobs_repo = ImportJobsRepository(db_connection=self.db_connection)
        await jobs_repo.increment_counters(
            job_id=job_id,
            organization_id=organization_id,
            total_rows_delta=total,
            processed_rows_delta=total,
            success_rows_delta=success,
            error_rows_delta=errors,
        )

        return {
            "total": total,
            "processed": total,
            "success": success,
            "errors": errors,
        }
