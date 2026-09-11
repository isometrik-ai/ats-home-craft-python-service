"""Assets repository."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import record_to_dict


class AssetsRepository(ScopedRepository):
    """Database access for assets."""

    table = "assets"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        category_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        filters = ""
        if search:
            params.append(f"%{search}%")
            filters += f" AND (name ILIKE ${len(params)} OR code ILIKE ${len(params)})"
        if category_id:
            params.append(category_id)
            filters += f" AND category_id = ${len(params)}::uuid"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.assets
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{filters}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.assets
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{filters}
            ORDER BY name
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.assets (
                organization_id, project_id, name, code, make, model, serial_number,
                description, category_id, status, facility_id, location_text,
                landmark_note, photo_paths, associated_parts, purchase_date,
                purchase_cost_minor, currency, supplier_name, company_id,
                purchase_order_number, invoice_ref, install_date, warranty_start,
                warranty_expiry, warranty_terms, document_paths, custom_fields, contract_id
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::uuid,
                COALESCE($10::work_order.work_order_asset_status, 'operational'),
                $11::uuid, $12, $13, COALESCE($14::text[], '{}'),
                COALESCE($15::jsonb, '[]'::jsonb), $16::date, $17, COALESCE($18, 'INR'),
                $19, $20::uuid, $21, $22, $23::date, $24::date, $25::date, $26,
                COALESCE($27::text[], '{}'), COALESCE($28::jsonb, '[]'::jsonb), $29::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["name"],
            data["code"],
            data.get("make"),
            data.get("model"),
            data.get("serial_number"),
            data.get("description"),
            data["category_id"],
            data.get("status"),
            data.get("facility_id"),
            data.get("location_text"),
            data.get("landmark_note"),
            data.get("photo_paths") or [],
            data.get("associated_parts") or [],
            data.get("purchase_date"),
            data.get("purchase_cost_minor"),
            data.get("currency"),
            data.get("supplier_name"),
            data.get("company_id"),
            data.get("purchase_order_number"),
            data.get("invoice_ref"),
            data.get("install_date"),
            data.get("warranty_start"),
            data.get("warranty_expiry"),
            data.get("warranty_terms"),
            data.get("document_paths") or [],
            data.get("custom_fields") or [],
            data.get("contract_id"),
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        custom_fields = data["custom_fields"] if "custom_fields" in data else None
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.assets
            SET name = COALESCE($4, name),
                code = COALESCE($5, code),
                make = COALESCE($6, make),
                model = COALESCE($7, model),
                serial_number = COALESCE($8, serial_number),
                description = COALESCE($9, description),
                category_id = COALESCE($10::uuid, category_id),
                status = COALESCE($11::work_order.work_order_asset_status, status),
                facility_id = COALESCE($12::uuid, facility_id),
                location_text = COALESCE($13, location_text),
                landmark_note = COALESCE($14, landmark_note),
                photo_paths = COALESCE($15::text[], photo_paths),
                associated_parts = COALESCE($16::jsonb, associated_parts),
                purchase_date = COALESCE($17::date, purchase_date),
                purchase_cost_minor = COALESCE($18, purchase_cost_minor),
                currency = COALESCE($19, currency),
                supplier_name = COALESCE($20, supplier_name),
                company_id = COALESCE($21::uuid, company_id),
                purchase_order_number = COALESCE($22, purchase_order_number),
                invoice_ref = COALESCE($23, invoice_ref),
                install_date = COALESCE($24::date, install_date),
                warranty_start = COALESCE($25::date, warranty_start),
                warranty_expiry = COALESCE($26::date, warranty_expiry),
                warranty_terms = COALESCE($27, warranty_terms),
                document_paths = COALESCE($28::text[], document_paths),
                custom_fields = COALESCE($29::jsonb, custom_fields),
                contract_id = COALESCE($30::uuid, contract_id),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("name"),
            data.get("code"),
            data.get("make"),
            data.get("model"),
            data.get("serial_number"),
            data.get("description"),
            data.get("category_id"),
            data.get("status"),
            data.get("facility_id"),
            data.get("location_text"),
            data.get("landmark_note"),
            data.get("photo_paths"),
            data["associated_parts"] if "associated_parts" in data else None,
            data.get("purchase_date"),
            data.get("purchase_cost_minor"),
            data.get("currency"),
            data.get("supplier_name"),
            data.get("company_id"),
            data.get("purchase_order_number"),
            data.get("invoice_ref"),
            data.get("install_date"),
            data.get("warranty_start"),
            data.get("warranty_expiry"),
            data.get("warranty_terms"),
            data.get("document_paths"),
            custom_fields,
            data.get("contract_id"),
        )
        return record_to_dict(row) if row else None
