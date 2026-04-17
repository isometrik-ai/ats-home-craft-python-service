"""Bulk lead creation helper for imports.

Goal: create many leads with optional single company + single contact link per lead
in a small number of DB round trips.
"""

from __future__ import annotations

from typing import Any

import asyncpg

import json

from apps.user_service.app.db.repositories.lead_repository import LeadRepository


class BulkLeadCreator:
    """Bulk-create leads + associations with per-row mapping."""

    def __init__(self, *, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection
        self.repo = LeadRepository(db_connection=db_connection)

    async def create_lead_for_rows(
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
        stage_ids = {str(r.get("stage_id") or "") for r in rows if r.get("stage_id")}
        # If multiple stage_ids appear, we validate each by reusing repo query per stage.
        # This keeps correctness without needing schema changes.
        found_contacts: set[str] = set()
        found_companies: set[str] = set()
        stage_ok: dict[str, bool] = {}

        contact_ids = [str(r.get("contact_id")) for r in rows if r.get("contact_id")]
        company_ids = [str(r.get("company_id")) for r in rows if r.get("company_id")]

        for stage_id in stage_ids or {None}:
            ok, fc, fco = await self.repo.fetch_lead_reference_validation(
                organization_id,
                stage_id=stage_id,
                contact_ids=contact_ids or None,
                company_ids=company_ids or None,
            )
            if stage_id is not None and ok is not None:
                stage_ok[str(stage_id)] = bool(ok)
            found_contacts |= fc
            found_companies |= fco

        errors: list[tuple[int, str]] = []
        eligible: list[dict[str, Any]] = []
        for r in rows:
            rn = int(r["row_number"])
            sid = str(r.get("stage_id") or "")
            if sid and stage_ok.get(sid) is False:
                errors.append((rn, "lead_stages.errors.stage_not_found"))
                continue
            cid = str(r.get("contact_id") or "")
            if cid and cid not in found_contacts:
                errors.append((rn, "contacts.errors.contact_not_found"))
                continue
            coid = str(r.get("company_id") or "")
            if coid and coid not in found_companies:
                errors.append((rn, "companies.errors.company_not_found"))
                continue
            eligible.append(r)

        if not eligible:
            return {}, errors

        payload = [
            {
                "row_number": int(r["row_number"]),
                "name": str(r.get("name") or ""),
                "stage_id": str(r.get("stage_id") or ""),
                "lead_source": r.get("lead_source"),
                "lead_score": r.get("lead_score"),
                "owner_id": r.get("owner_id"),
                "contact_id": r.get("contact_id"),
                "company_id": r.get("company_id"),
            }
            for r in eligible
        ]

        # Insert leads in deterministic order and map returned ids by row order.
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

        # NOTE: join-by-name is best-effort; for strict mapping we'd need RETURNING row_number via CTE.
        # We'll treat unmapped rows as errors; this is rare unless names duplicate within payload.
        lead_ids_by_row: dict[int, str] = {}
        contact_pairs: list[tuple[str, str]] = []
        company_pairs: list[tuple[str, str]] = []
        lead_ids = [str(r["lead_id"] or "") for r in inserted]
        row_numbers_ordered = [int(r["row_number"]) for r in sorted(payload, key=lambda x: int(x["row_number"]))]
        if len(lead_ids) == len(row_numbers_ordered):
            for rn, lid in zip(row_numbers_ordered, lead_ids, strict=False):
                if lid:
                    lead_ids_by_row[rn] = lid
        else:
            errors.extend([(int(rn), "leads.errors.lead_creation_failed") for rn in row_numbers_ordered])
            return lead_ids_by_row, errors

        for r in eligible:
            rn = int(r["row_number"])
            lid = lead_ids_by_row.get(rn)
            if not lid:
                continue
            if r.get("contact_id"):
                contact_pairs.append((lid, str(r["contact_id"])))
            if r.get("company_id"):
                company_pairs.append((lid, str(r["company_id"])))

        if contact_pairs:
            lead_ids = [lid for lid, _ in contact_pairs]
            contact_ids2 = [cid for _, cid in contact_pairs]
            await self.db_connection.execute(
                """
                INSERT INTO lead_contacts (lead_id, organization_id, contact_id, label)
                SELECT u.lead_id::uuid, $1::uuid, u.contact_id::uuid, NULL::text
                FROM unnest($2::text[], $3::text[]) AS u(lead_id, contact_id)
                """,
                organization_id,
                lead_ids,
                contact_ids2,
            )
        if company_pairs:
            lead_ids = [lid for lid, _ in company_pairs]
            company_ids2 = [cid for _, cid in company_pairs]
            await self.db_connection.execute(
                """
                INSERT INTO lead_companies (lead_id, organization_id, company_id, label)
                SELECT u.lead_id::uuid, $1::uuid, u.company_id::uuid, NULL::text
                FROM unnest($2::text[], $3::text[]) AS u(lead_id, company_id)
                """,
                organization_id,
                lead_ids,
                company_ids2,
            )

        return lead_ids_by_row, errors

