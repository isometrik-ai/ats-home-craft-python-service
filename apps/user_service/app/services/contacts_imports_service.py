"""Contacts imports producer service.

Creates import jobs and builds metadata-only Kafka payloads for best-effort publish.
"""

from __future__ import annotations

import csv
import contextlib
import asyncio
import ipaddress
import json
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories.contacts_repository import (
    CONTACT_JSONB_COLUMNS,
    ContactsRepository,
)
from apps.user_service.app.db.repositories.import_job_logs_repository import ImportJobLogsRepository
from apps.user_service.app.db.repositories.import_job_rows_repository import ImportJobRowsRepository
from apps.user_service.app.db.repositories.import_jobs_repository import (
    ImportJobsRepository,
)
from apps.user_service.app.db.repositories.companies_repository import CompaniesRepository
from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.contacts_imports import ContactsImportEventPayload
from apps.user_service.app.schemas.enums import (
    ContactsImportEventAction,
    ContactsImportJobStatus,
    ContactsImportKafkaStream,
    ContactsImportType,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.bulk_leads_creator import BulkLeadCreator
from apps.user_service.app.utils.common_utils import (
    UserContext,
    coerce_json_list,
    parse_json_any,
    parse_json_field,
    serialize_jsonb_param,
)
from libs.shared_db.supabase_db.client import get_supabase_service_client

CONTACTS_IMPORT_TOPIC = ContactsImportKafkaStream.CONTACTS_IMPORT_REQUESTED


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

        job = await job_repo.get_job(job_id=event.job_key, organization_id=event.organization_id)
        if job is None:
            return

        if ContactsImportJobStatus(str(job["status"])) != ContactsImportJobStatus.QUEUED:
            return

        started_at_dt = datetime.now(UTC)
        started_at = started_at_dt.isoformat()
        job_internal_id = str(job.get("id") or "")

        mapping_raw = job.get("mapping")
        options_raw = job.get("options")
        try:
            mapping_parsed = parse_json_field(mapping_raw)  # jsonb dict or JSON string
        except Exception:
            mapping_parsed = {}
        try:
            options_parsed = parse_json_field(options_raw)  # jsonb dict or JSON string
        except Exception:
            options_parsed = {}

        mapping = mapping_parsed if isinstance(mapping_parsed, dict) else {}
        options = options_parsed if isinstance(options_parsed, dict) else {}

        processed_total = 0
        success_total = 0
        errors_total = 0
        last_log_ts = 0.0

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
            organization = await contacts_service.org_repo.get_organization_by_id(event.organization_id)
            org_name = str(
                (organization or {}).get("name")
                or getattr(getattr(__import__("apps.user_service.app.config.app_settings", fromlist=["shared_settings"]), "shared_settings"), "company_name", "")
                or ""
            )

            async for batch in self._iter_validated_rows_for_ledger(
                file_url=event.file_url,
                mapping=mapping,
                options=options,
                batch_size=batch_size,
            ):
                if not batch:
                    continue

                # Claim row ledger entries in bulk, skipping already-successful rows.
                claim_rows: list[tuple[int, dict[str, Any] | None]] = []
                for item in batch:
                    rn = int(item["row_number"])
                    claim_rows.append((rn, item.get("raw_row") if item.get("error") else None))

                statuses = await rows_repo.claim_rows_processing(
                    organization_id=event.organization_id,
                    job_id=job_internal_id,
                    rows=claim_rows,
                )

                claimed: list[dict[str, Any]] = [
                    item for item in batch if statuses.get(int(item["row_number"])) != "success"
                ]

                if not claimed:
                    continue

                # Bulk email uniqueness check (parity with ContactsService._assert_contact_email_unique).
                emails_to_check: list[str] = []
                email_by_row: dict[int, str] = {}
                for item in claimed:
                    if item.get("error") or item.get("contact_model") is None:
                        continue
                    email_norm = str(item["contact_model"].email or "").strip().lower()
                    if email_norm:
                        email_by_row[int(item["row_number"])] = email_norm
                        emails_to_check.append(email_norm)

                if emails_to_check:
                    contacts_repo = ContactsRepository(db_connection=self.db_connection)
                    existing = await contacts_repo.get_contact_ids_by_emails(
                        organization_id=event.organization_id,
                        emails=emails_to_check,
                    )
                    if existing:
                        duplicate_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []
                        for row_number, email_norm in email_by_row.items():
                            if email_norm in existing:
                                duplicate_errors.append(
                                    (
                                        int(row_number),
                                        "email_already_exists",
                                        "contacts.errors.email_already_exists",
                                        {"email": email_norm, "client_id": existing[email_norm]},
                                    )
                                )
                        if duplicate_errors:
                            await rows_repo.mark_errors_bulk(
                                organization_id=event.organization_id,
                                job_id=job_internal_id,
                                errors=duplicate_errors,
                            )
                            processed_total += len(duplicate_errors)
                            errors_total += len(duplicate_errors)
                            duplicate_row_numbers = {rn for rn, _, _, _ in duplicate_errors}
                            claimed = [it for it in claimed if int(it["row_number"]) not in duplicate_row_numbers]

                # Record validation errors immediately (bulk).
                valid_rows: list[dict[str, Any]] = []
                valid_row_numbers: list[int] = []
                validation_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []
                for item in claimed:
                    if item.get("error"):
                        err = item["error"]
                        validation_errors.append(
                            (
                                int(item["row_number"]),
                                str(err.get("code") or "validation_error"),
                                str(err.get("message") or "row validation failed"),
                                item.get("raw_row"),
                            )
                        )
                        continue
                    valid_rows.append(item)
                    valid_row_numbers.append(int(item["row_number"]))

                if validation_errors:
                    await rows_repo.mark_errors_bulk(
                        organization_id=event.organization_id,
                        job_id=job_internal_id,
                        errors=validation_errors,
                    )
                    processed_total += len(validation_errors)
                    errors_total += len(validation_errors)

                # Identity provisioning in parallel (parity with ContactsService._provision_contact_auth_identity).
                identity_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []
                identity_results: dict[int, tuple[str, str, str | None]] = {}

                async def _provision_for_item(item: dict[str, Any]) -> None:
                    row_number = int(item["row_number"])
                    model: CreateContactRequest = item["contact_model"]
                    try:
                        user_id, isometrik_user_id, created_password = (
                            await contacts_service._provision_contact_auth_identity(  # noqa: SLF001
                                email=str(model.email or "").strip().lower(),
                                first_name=model.first_name,
                                last_name=model.last_name,
                                prefix=model.prefix,
                            )
                        )
                        identity_results[row_number] = (user_id, isometrik_user_id, created_password)
                    except Exception as exc:  # noqa: BLE001
                        identity_errors.append(
                            (
                                row_number,
                                "external_service_error",
                                str(exc)[:2000],
                                None,
                            )
                        )

                # Important: asyncpg connections do not support concurrent operations.
                # Provisioning touches DB, so keep it sequential on this single connection.
                for it in claimed:
                    if it.get("contact_model"):
                        await _provision_for_item(it)

                if identity_errors:
                    await rows_repo.mark_errors_bulk(
                        organization_id=event.organization_id,
                        job_id=job_internal_id,
                        errors=identity_errors,
                    )
                    processed_total += len(identity_errors)
                    errors_total += len(identity_errors)

                # Keep only rows with identity provisioned.
                provisioned_claimed = [
                    it for it in claimed if int(it["row_number"]) in identity_results and it.get("contact_model")
                ]

                # Company reuse by name (best-effort): create once per normalized name and reuse id.
                # Cache is per job execution.
                if "company_cache" not in locals():
                    company_cache: dict[str, str] = {}

                company_repo = CompaniesRepository(db_connection=self.db_connection)
                desired_names = []
                for it in provisioned_claimed:
                    name = (it.get("company_name") or "").strip()
                    if name:
                        desired_names.append(name)

                missing_norms: list[str] = []
                for name in desired_names:
                    norm = name.strip().lower()
                    if norm and norm not in company_cache:
                        missing_norms.append(norm)

                if missing_norms:
                    existing_companies = await company_repo.get_company_ids_by_names(
                        organization_id=event.organization_id,
                        names=missing_norms,
                    )
                    company_cache.update(existing_companies)

                to_create: list[str] = []
                for name in desired_names:
                    norm = name.strip().lower()
                    if norm and norm not in company_cache and norm not in to_create:
                        to_create.append(norm)

                if to_create:
                    created = await company_repo.create_companies(
                        [
                            {
                                "organization_id": event.organization_id,
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
                        if name_norm and cid and name_norm not in company_cache:
                            company_cache[name_norm] = cid

                valid_rows: list[dict[str, Any]] = []
                valid_row_numbers: list[int] = []
                for it in provisioned_claimed:
                    rn = int(it["row_number"])
                    it["identity"] = identity_results[rn]
                    cname = (it.get("company_name") or "").strip()
                    if cname:
                        it["company_id"] = company_cache.get(cname.lower())
                    valid_rows.append(it)
                    valid_row_numbers.append(rn)

                if valid_rows:
                    try:
                        contacts_repo = ContactsRepository(db_connection=self.db_connection)
                        jobs_repo = ImportJobsRepository(db_connection=self.db_connection)

                        # Build DB rows matching ContactsService.create_contact behavior.
                        cf_service = CustomFieldService(db_connection=self.db_connection, user_context=user_context)
                        cf_sem = asyncio.Semaphore(10)
                        custom_fields_by_row: dict[int, list[dict[str, Any]]] = {}
                        cf_errors: list[tuple[int, str, str, dict[str, Any] | None]] = []

                        async def _validate_cf(item: dict[str, Any]) -> None:
                            rn = int(item["row_number"])
                            model: CreateContactRequest = item["contact_model"]
                            try:
                                async with cf_sem:
                                    validated = await cf_service.validate_for_create(
                                        model.custom_fields,
                                        ContactsImportType.CONTACTS,
                                    )
                                custom_fields_by_row[rn] = validated
                            except Exception as exc:  # noqa: BLE001
                                cf_errors.append((rn, "validation_error", str(exc)[:2000], None))

                        # Important: validate_for_create performs DB reads; keep sequential on this connection.
                        for it in valid_rows:
                            await _validate_cf(it)
                        if cf_errors:
                            await rows_repo.mark_errors_bulk(
                                organization_id=event.organization_id,
                                job_id=job_internal_id,
                                errors=cf_errors,
                            )
                            processed_total += len(cf_errors)
                            errors_total += len(cf_errors)
                            bad_rows = {rn for rn, _, _, _ in cf_errors}
                            valid_rows = [it for it in valid_rows if int(it["row_number"]) not in bad_rows]
                            valid_row_numbers = [rn for rn in valid_row_numbers if rn not in bad_rows]

                        if not valid_rows:
                            continue

                        rows_to_insert: list[dict[str, Any]] = []
                        row_payload_by_row_number: dict[int, dict[str, Any]] = {}
                        user_id_by_row: dict[int, str] = {}
                        password_by_row: dict[int, str | None] = {}
                        portal_by_row: dict[int, bool] = {}
                        email_by_row: dict[int, str] = {}
                        company_id_by_row: dict[int, str | None] = {}

                        for it in valid_rows:
                            rn = int(it["row_number"])
                            model: CreateContactRequest = it["contact_model"]
                            user_id, isometrik_user_id, created_password = it["identity"]

                            # Mirror ContactsService list id behavior
                            phones_payload = ContactsService._ensure_list_item_ids(  # noqa: SLF001
                                [p.model_dump(mode="json", exclude_none=True) for p in model.phones]
                            )
                            social_pages_payload = ContactsService._ensure_list_item_ids(  # noqa: SLF001
                                [p.model_dump(mode="json", exclude_none=True) for p in model.social_pages]
                            )
                            websites_payload = ContactsService._ensure_list_item_ids(  # noqa: SLF001
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
                                "custom_fields": custom_fields_by_row.get(rn, []),
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
                            row_payload_by_row_number[rn] = row_payload
                            user_id_by_row[rn] = user_id
                            password_by_row[rn] = created_password
                            portal_by_row[rn] = bool(model.portal_access)
                            email_by_row[rn] = str(model.email or "").strip().lower()
                            company_id_by_row[rn] = it.get("company_id")

                        inserted_rows = await contacts_repo.create_contacts(rows_to_insert)

                        # For retries / duplicates: inserts may be skipped due to uq_contacts_user_org.
                        # Always fetch contact ids for all user_ids so downstream steps can proceed.
                        contact_ids_by_user = await contacts_repo.get_contact_ids_by_user_ids(
                            organization_id=event.organization_id,
                            user_ids=list(user_id_by_row.values()),
                        )

                        # Addresses bulk insert (parity with ContactsService._create_addresses_if_any)
                        address_rows: list[dict[str, Any]] = []
                        for it in valid_rows:
                            rn = int(it["row_number"])
                            uid = user_id_by_row.get(rn)
                            contact_id = str(contact_ids_by_user.get(uid or "") or "")
                            if not uid or not contact_id:
                                continue
                            model: CreateContactRequest = it["contact_model"]
                            for addr in (model.addresses or []):
                                address_rows.append(
                                    {"contact_id": contact_id, **addr.model_dump(exclude_none=True)}
                                )
                            it["contact_id"] = contact_id

                        if address_rows:
                            await contacts_repo.create_contact_addresses(address_rows)

                        # Counters: processed = attempted rows, success = contacts persisted (created or already existed).
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
                        processed_total += processed_count
                        success_total += success_count
                        errors_total += error_count

                        # Portal email: best-effort, parity with ContactsService._maybe_send_contact_creation_email.
                        for rn in valid_row_numbers:
                            if portal_by_row.get(rn) and email_by_row.get(rn):
                                contacts_service._maybe_send_contact_creation_email(  # noqa: SLF001
                                    portal_access=True,
                                    email=email_by_row[rn],
                                    organization_name=org_name,
                                    password=password_by_row.get(rn),
                                )

                        # Bulk lead creation (parity with ContactsService lead behavior).
                        owner_id = None

                        lead_items = []
                        for it in valid_rows:
                            model: CreateContactRequest = it["contact_model"]
                            lead_payload = getattr(model, "lead", None)
                            contact_id = it.get("contact_id")
                            if lead_payload is None or not contact_id:
                                continue
                            full_name = " ".join(
                                [p for p in [(model.first_name or "").strip(), (model.last_name or "").strip()] if p]
                            ).strip()
                            lead_name = full_name or (str(model.email or "").strip().lower())
                            lead_items.append(
                                {
                                    "row_number": int(it["row_number"]),
                                    "name": lead_name,
                                    "stage_id": str(getattr(lead_payload, "stage_id", "") or ""),
                                    "lead_source": (getattr(lead_payload, "intake_stage", None) or None),
                                    "lead_score": (getattr(lead_payload, "lead_score", None) or None),
                                    "owner_id": owner_id,
                                    "contact_id": str(contact_id),
                                    "company_id": str(it.get("company_id") or "") or None,
                                }
                            )

                        lead_ids_by_row: dict[int, str] = {}
                        lead_errors: list[tuple[int, str]] = []
                        if lead_items:
                            bulk_leads = BulkLeadCreator(db_connection=self.db_connection)
                            lead_ids_by_row, lead_errors = await bulk_leads.create_leads_for_rows(
                                organization_id=event.organization_id,
                                rows=lead_items,
                            )

                        if lead_errors:
                            # Mark lead errors and compensate: soft-delete contact + remove addresses.
                            error_rows_payload: list[tuple[int, str, str, dict[str, Any] | None]] = []
                            for rn, msg in lead_errors:
                                error_rows_payload.append((int(rn), "lead_error", str(msg), None))
                                # compensation best-effort
                                bad = next((it for it in valid_rows if int(it["row_number"]) == int(rn)), None)
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
                            processed_total += len(lead_errors)
                            errors_total += len(lead_errors)
                            # Fix counters: these rows were counted as success earlier.
                            await jobs_repo.increment_counters(
                                job_id=event.job_key,
                                organization_id=event.organization_id,
                                total_rows_delta=0,
                                processed_rows_delta=0,
                                success_rows_delta=-len(lead_errors),
                                error_rows_delta=len(lead_errors),
                            )
                            success_total -= len(lead_errors)

                        # Mark ledger success only when contact insert + (lead if requested) succeeded.
                        success_row_numbers: list[int] = []
                        for it in valid_rows:
                            rn = int(it["row_number"])
                            model: CreateContactRequest = it["contact_model"]
                            wants_lead = getattr(model, "lead", None) is not None
                            if wants_lead and rn not in lead_ids_by_row:
                                continue
                            if it.get("contact_id"):
                                success_row_numbers.append(rn)
                        if success_row_numbers:
                            await rows_repo.mark_success_bulk(
                                organization_id=event.organization_id,
                                job_id=job_internal_id,
                                row_numbers=success_row_numbers,
                            )
                    except Exception as exc:  # noqa: BLE001
                        # One consumer event = one attempt. If the batch fails, mark row statuses and move on.
                        err_msg = str(exc)[:2000]
                        await rows_repo.mark_errors_bulk(
                            organization_id=event.organization_id,
                            job_id=job_internal_id,
                            errors=[
                                (int(rn), "db_error", err_msg, None)
                                for rn in valid_row_numbers
                            ],
                        )
                        processed_total += len(valid_row_numbers)
                        errors_total += len(valid_row_numbers)
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
                                    "processed": processed_total,
                                    "success": success_total,
                                    "errors": errors_total,
                                },
                            },
                        )

                now_ts = time.time()
                if (now_ts - last_log_ts) >= 2.0:
                    await logs_repo.upsert_payload(
                        organization_id=event.organization_id,
                        job_id=job_internal_id,
                        payload={
                            "phase": "running",
                            "action": str(event.action).lower(),
                            "stats": {
                                "processed": processed_total,
                                "success": success_total,
                                "errors": errors_total,
                            },
                        },
                    )
                    last_log_ts = now_ts

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
                        "processed": processed_total,
                        "success": success_total,
                        "errors": errors_total,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
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
                            "processed": processed_total,
                            "success": success_total,
                            "errors": errors_total,
                        },
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            raise

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
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="contacts_import_", suffix=".csv", delete=False) as tmp:
                tmp_path = tmp.name

            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                async with client.stream("GET", file_url) as response:
                    response.raise_for_status()
                    with open(tmp_path, "wb") as out:
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                out.write(chunk)

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                return

            with open(tmp_path, newline="", encoding="utf-8", errors="replace") as f:
                if has_header:
                    reader = csv.DictReader(f)
                else:
                    fieldnames = list(mapping.values()) or None
                    reader = csv.DictReader(f, fieldnames=fieldnames)

                reverse_mapping: dict[str, str] = {}
                for canonical, header in (mapping or {}).items():
                    if header:
                        reverse_mapping[str(header)] = str(canonical)

                batch: list[dict[str, Any]] = []
                async for row_item in self._iter_row_items(reader=reader, reverse_mapping=reverse_mapping):
                    batch.append(row_item)
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []

                if batch:
                    yield batch
        finally:
            if tmp_path:
                with contextlib.suppress(Exception):
                    os.remove(tmp_path)

    async def _iter_row_items(self, *, reader: Any, reverse_mapping: dict[str, str]):
        """Yield row work items: row_number + raw_row + validated model or error."""
        for row_number, row in enumerate(reader, start=1):
            if not isinstance(row, dict):
                continue
            canonical: dict[str, Any] = {}
            for header, value in row.items():
                canonical_key = reverse_mapping.get(header, header)
                canonical[canonical_key] = value

            email_raw = (canonical.get("email") or "").strip()
            if not email_raw:
                yield {
                    "row_number": row_number,
                    "raw_row": canonical,
                    "error": {"code": "missing_email", "message": "email is required"},
                    "contact_model": None,
                }
                continue

            body_dict: dict[str, Any] = {
                "email": email_raw,
                "portal_access": str(canonical.get("portal_access") or "").strip().lower()
                in {"true", "1", "yes"},
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

            def _parse_json_array(raw: Any) -> list[Any]:
                return coerce_json_list(raw)

            def _parse_json_object(raw: Any) -> dict[str, Any]:
                parsed = parse_json_any(raw, default={})
                return parsed if isinstance(parsed, dict) else {}

            body_dict["phones"] = _parse_json_array(canonical.get("phones_json"))
            body_dict["tags"] = _parse_json_array(canonical.get("tags_json"))
            body_dict["social_pages"] = _parse_json_array(canonical.get("social_pages_json"))
            websites = _parse_json_array(canonical.get("websites_json"))
            additional_data_from_row = _parse_json_object(canonical.get("additional_data_json"))
            if websites:
                additional_data_from_row.setdefault("websites", websites)
            body_dict["additional_data"] = additional_data_from_row
            body_dict["custom_fields"] = _parse_json_array(canonical.get("custom_fields_json"))
            body_dict["addresses"] = _parse_json_array(canonical.get("addresses_json"))

            lead_obj = _parse_json_object(canonical.get("lead_json"))
            if lead_obj:
                body_dict["lead"] = lead_obj

            company_name = (canonical.get("company_name") or "").strip() or None

            try:
                model = CreateContactRequest.model_validate(body_dict)
                yield {
                    "row_number": row_number,
                    "raw_row": canonical,
                    "error": None,
                    "contact_model": model,
                    "company_name": company_name,
                }
            except Exception as exc:  # noqa: BLE001
                yield {
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
        phones_payload = [phone.model_dump(mode="json", exclude_none=True) for phone in row_model.phones]
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
            ContactsImportType.CONTACTS,
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

