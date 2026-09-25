"""Persistence for contact booking wallets and wallet transactions."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class FacilityBookingWalletRepository(BaseRepository):
    """facility_booking_wallets + facility_booking_wallet_transactions."""

    async def get_or_create(
        self, *, organization_id: str, project_id: str, contact_id: str
    ) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO facility_booking_wallets (organization_id, project_id, contact_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            ON CONFLICT (project_id, contact_id) DO UPDATE
                SET updated_at = facility_booking_wallets.updated_at
            RETURNING *
            """,
            organization_id,
            project_id,
            contact_id,
        )
        return dict(row)

    async def list_project(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            """
            SELECT
                w.*,
                NULLIF(BTRIM(CONCAT_WS(' ', c.first_name, c.last_name)), '') AS contact_name
            FROM facility_booking_wallets w
            LEFT JOIN contacts c ON c.id = w.contact_id
            WHERE w.organization_id = $1::uuid AND w.project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def update_balance(
        self, *, wallet_id: str, balance: int, credit_limit: int | None | object = ...
    ) -> dict[str, Any]:
        if credit_limit is ...:
            row = await self.db_connection.fetchrow(
                """
                UPDATE facility_booking_wallets
                SET balance = $2, updated_at = NOW()
                WHERE id = $1::uuid
                RETURNING *
                """,
                wallet_id,
                balance,
            )
        else:
            row = await self.db_connection.fetchrow(
                """
                UPDATE facility_booking_wallets
                SET balance = $2, credit_limit = $3, updated_at = NOW()
                WHERE id = $1::uuid
                RETURNING *
                """,
                wallet_id,
                balance,
                credit_limit,
            )
        return dict(row)

    async def set_limit(self, *, wallet_id: str, credit_limit: int | None) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            UPDATE facility_booking_wallets
            SET credit_limit = $2, updated_at = NOW()
            WHERE id = $1::uuid
            RETURNING *
            """,
            wallet_id,
            credit_limit,
        )
        return dict(row)

    async def insert_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO facility_booking_wallet_transactions (
                organization_id, project_id, wallet_id, contact_id,
                entry_type, amount, method, description, created_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::facility_booking_wallet_txn_type, $6,
                $7::facility_booking_payment_method, $8, $9::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["wallet_id"],
            data["contact_id"],
            data["entry_type"],
            data["amount"],
            data.get("method"),
            data["description"],
            data.get("created_by_user_id"),
        )
        return dict(row)

    async def list_transactions(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM facility_booking_wallet_transactions
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND contact_id = $3::uuid
            ORDER BY posted_at DESC, created_at DESC
            OFFSET $4 LIMIT $5
            """,
            organization_id,
            project_id,
            contact_id,
            (page - 1) * page_size,
            page_size,
        )
        return [dict(row) for row in rows]
