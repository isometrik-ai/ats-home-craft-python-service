"""Bulk lead creation helper for imports.

Goal: create many leads with optional single company + single contact link per lead
in a small number of DB round trips.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.lead_repository import LeadRepository


class BulkLeadCreator:
    """Bulk-create leads + associations with per-row mapping."""

    def __init__(self, *, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection
        self.repo = LeadRepository(db_connection=db_connection)

    async def _fetch_reference_validation(
        self,
        *,
        organization_id: str,
        stage_ids: set[str],
        contact_ids: list[str],
        company_ids: list[str],
    ) -> tuple[dict[str, bool], set[str], set[str]]:
        """Validate referenced stage/contact/company ids in bulk."""
        stage_ok: dict[str, bool] = {}
        found_contacts: set[str] = set()
        found_companies: set[str] = set()

        for stage_id in stage_ids or {""}:
            (
                ok,
                found_contacts_chunk,
                found_companies_chunk,
            ) = await self.repo.fetch_lead_reference_validation(
                organization_id,
                stage_id=stage_id or None,
                contact_ids=contact_ids or None,
                company_ids=company_ids or None,
            )
            if stage_id and ok is not None:
                stage_ok[stage_id] = bool(ok)
            found_contacts |= found_contacts_chunk
            found_companies |= found_companies_chunk

        return stage_ok, found_contacts, found_companies

    @staticmethod
    def _filter_eligible_rows(
        *,
        rows: list[dict[str, Any]],
        stage_ok: dict[str, bool],
        found_contacts: set[str],
        found_companies: set[str],
    ) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
        """Return eligible rows and per-row error tuples."""
        errors: list[tuple[int, str]] = []
        eligible: list[dict[str, Any]] = []

        for row_item in rows:
            row_number = int(row_item["row_number"])

            stage_id = str(row_item.get("stage_id") or "")
            if stage_id and stage_ok.get(stage_id) is False:
                errors.append((row_number, "lead_stages.errors.stage_not_found"))
                continue

            contact_id = str(row_item.get("contact_id") or "")
            if contact_id and contact_id not in found_contacts:
                errors.append((row_number, "contacts.errors.contact_not_found"))
                continue

            company_id = str(row_item.get("company_id") or "")
            if company_id and company_id not in found_companies:
                errors.append((row_number, "companies.errors.company_not_found"))
                continue

            eligible.append(row_item)

        return eligible, errors

    async def _insert_leads(
        self,
        *,
        organization_id: str,
        payload: list[dict[str, Any]],
    ) -> list[str]:
        """Insert leads from payload and return lead ids in row_number order."""
        inserted = await self.db_connection.fetch(
            """
            WITH input AS (
              SELECT *
              FROM jsonb_to_recordset($2::jsonb) AS x(
                row_number int,
                name text,
                stage_id uuid,
                lead_source text,
                lead_score text,
                owner_id uuid,
                contact_id uuid,
                company_id uuid
              )
            ),
            ins AS (
              INSERT INTO leads (
                organization_id,
                name,
                stage_id,
                lead_source,
                lead_score,
                owner_id,
                custom_fields
              )
              SELECT
                $1::uuid,
                i.name,
                i.stage_id,
                i.lead_source,
                i.lead_score,
                i.owner_id,
                '[]'::jsonb
              FROM input i
              ORDER BY i.row_number ASC
              RETURNING id::text AS lead_id
            )
            SELECT lead_id FROM ins
            """,
            organization_id,
            json.dumps(payload),
        )
        return [str(row["lead_id"] or "") for row in inserted]

    async def _insert_contact_pairs(
        self,
        *,
        organization_id: str,
        contact_pairs: list[tuple[str, str]],
    ) -> None:
        """Insert lead-contact associations in bulk."""
        if not contact_pairs:
            return
        lead_ids = [lead_id for lead_id, _ in contact_pairs]
        contact_ids = [contact_id for _, contact_id in contact_pairs]
        await self.db_connection.execute(
            """
            INSERT INTO lead_contacts (lead_id, organization_id, contact_id, label)
            SELECT u.lead_id::uuid, $1::uuid, u.contact_id::uuid, NULL::text
            FROM unnest($2::text[], $3::text[]) AS u(lead_id, contact_id)
            """,
            organization_id,
            lead_ids,
            contact_ids,
        )

    async def _insert_company_pairs(
        self,
        *,
        organization_id: str,
        company_pairs: list[tuple[str, str]],
    ) -> None:
        """Insert lead-company associations in bulk."""
        if not company_pairs:
            return
        lead_ids = [lead_id for lead_id, _ in company_pairs]
        company_ids = [company_id for _, company_id in company_pairs]
        await self.db_connection.execute(
            """
            INSERT INTO lead_companies (lead_id, organization_id, company_id, label)
            SELECT u.lead_id::uuid, $1::uuid, u.company_id::uuid, NULL::text
            FROM unnest($2::text[], $3::text[]) AS u(lead_id, company_id)
            """,
            organization_id,
            lead_ids,
            company_ids,
        )

    async def create_leads_for_rows(
        self,
        *,
        organization_id: str,
        rows: list[dict[str, Any]],
    ) -> tuple[dict[int, str], list[tuple[int, str]]]:
        """Create leads for import rows.

        Input rows must include:
        - row_number (int)
        - name (str)
        - stage_id (str UUID)
        - lead_source (str|None)
        - lead_score (str|None)
        - owner_id (str|None)
        - contact_id (str|None)
        - company_id (str|None)

        Returns:
        - mapping row_number -> lead_id
        - errors list of (row_number, message)
        """
        if not rows:
            return {}, []

        # Validate references in bulk (stage + contact/company existence).
        stage_ids = {
            str(row_item.get("stage_id") or "") for row_item in rows if row_item.get("stage_id")
        }
        # If multiple stage_ids appear, we validate each by reusing repo query per stage.
        # This keeps correctness without needing schema changes.
        contact_ids = [
            str(row_item.get("contact_id")) for row_item in rows if row_item.get("contact_id")
        ]
        company_ids = [
            str(row_item.get("company_id")) for row_item in rows if row_item.get("company_id")
        ]

        stage_ok, found_contacts, found_companies = await self._fetch_reference_validation(
            organization_id=organization_id,
            stage_ids=stage_ids,
            contact_ids=contact_ids,
            company_ids=company_ids,
        )

        eligible, errors = self._filter_eligible_rows(
            rows=rows,
            stage_ok=stage_ok,
            found_contacts=found_contacts,
            found_companies=found_companies,
        )

        if not eligible:
            return {}, errors

        payload = [
            {
                "row_number": int(row_item["row_number"]),
                "name": str(row_item.get("name") or ""),
                "stage_id": str(row_item.get("stage_id") or ""),
                "lead_source": row_item.get("lead_source"),
                "lead_score": row_item.get("lead_score"),
                "owner_id": row_item.get("owner_id"),
                "contact_id": row_item.get("contact_id"),
                "company_id": row_item.get("company_id"),
            }
            for row_item in eligible
        ]

        # Insert leads in deterministic order and map returned ids by row order.
        lead_ids = await self._insert_leads(organization_id=organization_id, payload=payload)

        # NOTE: join-by-name is best-effort; for strict mapping we'd need
        # RETURNING row_number via CTE.
        # We'll treat unmapped rows as errors; this is rare unless names duplicate within payload.
        lead_ids_by_row: dict[int, str] = {}
        contact_pairs: list[tuple[str, str]] = []
        company_pairs: list[tuple[str, str]] = []
        row_numbers_ordered = [
            int(row["row_number"]) for row in sorted(payload, key=lambda x: int(x["row_number"]))
        ]
        if len(lead_ids) == len(row_numbers_ordered):
            for row_number, lead_id in zip(row_numbers_ordered, lead_ids, strict=False):
                if lead_id:
                    lead_ids_by_row[row_number] = lead_id
        else:
            errors.extend(
                [
                    (int(row_number), "leads.errors.lead_creation_failed")
                    for row_number in row_numbers_ordered
                ]
            )
            return lead_ids_by_row, errors

        for row_item in eligible:
            row_number = int(row_item["row_number"])
            lead_id = lead_ids_by_row.get(row_number)
            if not lead_id:
                continue
            if row_item.get("contact_id"):
                contact_pairs.append((lead_id, str(row_item["contact_id"])))
            if row_item.get("company_id"):
                company_pairs.append((lead_id, str(row_item["company_id"])))

        await self._insert_contact_pairs(
            organization_id=organization_id,
            contact_pairs=contact_pairs,
        )
        await self._insert_company_pairs(
            organization_id=organization_id,
            company_pairs=company_pairs,
        )

        return lead_ids_by_row, errors
