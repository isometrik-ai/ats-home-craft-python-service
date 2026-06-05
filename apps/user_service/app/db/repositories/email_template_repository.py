"""Email template database repository."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.constants.default_email_layout import (
    DEFAULT_LAYOUT_HTML,
    DEFAULT_LAYOUT_NAME,
)
from apps.user_service.app.schemas.enums import EmailTemplateStatus, EmailTemplateType


class EmailTemplateRepository:
    """Persistence for the ``email_templates`` table."""

    TABLE_NAME = "email_templates"

    TEMPLATE_COLUMNS = """
        id,
        organization_id,
        name,
        template_type,
        status,
        html_content,
        variables,
        is_default,
        created_at,
        updated_at
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    @classmethod
    def _columns_expr(cls) -> str:
        """Return SELECT column list as a single-line SQL fragment."""
        return cls.TEMPLATE_COLUMNS.strip().replace("\n", " ")

    async def insert_default_layout(self, organization_id: str) -> dict[str, Any]:
        """Seed the org default LAYOUT template."""
        query = f"""
            INSERT INTO {self.TABLE_NAME} (
                organization_id,
                name,
                template_type,
                status,
                html_content,
                variables,
                is_default
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING {self._columns_expr()}
        """
        row = await self.db_connection.fetchrow(
            query,
            organization_id,
            DEFAULT_LAYOUT_NAME,
            EmailTemplateType.LAYOUT.value,
            EmailTemplateStatus.PUBLISHED.value,
            DEFAULT_LAYOUT_HTML,
            json.dumps([]),
            True,
        )
        return dict(row)

    async def create_template(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert a new email template."""
        query = f"""
            INSERT INTO {self.TABLE_NAME} (
                organization_id,
                name,
                template_type,
                status,
                html_content,
                variables,
                is_default
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING {self._columns_expr()}
        """
        created = await self.db_connection.fetchrow(
            query,
            row["organization_id"],
            row["name"],
            row["template_type"],
            row["status"],
            row["html_content"],
            json.dumps(row.get("variables", [])),
            row.get("is_default", False),
        )
        return dict(created)

    async def list_templates(
        self,
        organization_id: str,
        *,
        template_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List templates for an organization with optional filters."""
        conditions = ["organization_id = $1"]
        params: list[Any] = [organization_id]
        idx = 2

        if template_type is not None:
            conditions.append(f"template_type = ${idx}")
            params.append(template_type)
            idx += 1

        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT {self._columns_expr()}
            FROM {self.TABLE_NAME}
            WHERE {where_clause}
            ORDER BY updated_at DESC, created_at DESC
        """
        rows = await self.db_connection.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_default_layout(self, organization_id: str) -> dict[str, Any] | None:
        """Return the org default LAYOUT template row, if any."""
        query = f"""
            SELECT {self._columns_expr()}
            FROM {self.TABLE_NAME}
            WHERE organization_id = $1
              AND template_type = $2
              AND is_default = TRUE
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(
            query,
            organization_id,
            EmailTemplateType.LAYOUT.value,
        )
        return dict(row) if row else None

    async def get_template_by_id(
        self,
        organization_id: str,
        template_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one template scoped to the organization."""
        query = f"""
            SELECT {self._columns_expr()}
            FROM {self.TABLE_NAME}
            WHERE organization_id = $1
              AND id = $2::uuid
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, organization_id, template_id)
        return dict(row) if row else None

    async def update_template(
        self,
        organization_id: str,
        template_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch template fields; always bumps updated_at."""
        if not update_data:
            return await self.get_template_by_id(organization_id, template_id)

        set_clauses: list[str] = ["updated_at = NOW()"]
        values: list[Any] = []
        param_index = 1

        for field, value in update_data.items():
            if field == "variables":
                set_clauses.append(f"variables = ${param_index}::jsonb")
                values.append(json.dumps(value))
            else:
                set_clauses.append(f"{field} = ${param_index}")
                values.append(value)
            param_index += 1

        values.extend([organization_id, template_id])
        org_param = param_index
        id_param = param_index + 1

        query = f"""
            UPDATE {self.TABLE_NAME}
            SET {", ".join(set_clauses)}
            WHERE organization_id = ${org_param}
              AND id = ${id_param}::uuid
            RETURNING {self._columns_expr()}
        """
        row = await self.db_connection.fetchrow(query, *values)
        return dict(row) if row else None

    async def delete_template(
        self,
        organization_id: str,
        template_id: str,
    ) -> dict[str, Any] | None:
        """Hard-delete a template; returns removed row if any."""
        query = f"""
            DELETE FROM {self.TABLE_NAME}
            WHERE organization_id = $1
              AND id = $2::uuid
            RETURNING {self._columns_expr()}
        """
        row = await self.db_connection.fetchrow(query, organization_id, template_id)
        return dict(row) if row else None
