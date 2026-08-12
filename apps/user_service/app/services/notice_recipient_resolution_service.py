"""Resolve notice recipient counts and contact ids."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from libs.shared_utils.common_query import VISITOR_MANAGEMENT_VIEW


class NoticeRecipientResolutionService:
    """Audience sizing and recipient resolution for notices."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection
        self.org_members_repo = OrganizationMemberRepository(db_connection=db_connection)

    async def estimate_reach(
        self,
        *,
        organization_id: str,
        project_id: str,
        recipient_groups: list[str],
        scope_type: str,
        tower_ids: list[str],
    ) -> tuple[int, dict[str, int]]:
        """Return total distinct recipients and per-group breakdown."""
        breakdown: dict[str, int] = {}
        total_ids: set[str] = set()

        for group in recipient_groups:
            contact_ids = await self._resolve_contact_ids_for_group(
                organization_id=organization_id,
                project_id=project_id,
                recipient_group=group,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
            breakdown[group] = len(contact_ids)
            total_ids.update(contact_ids)

        return len(total_ids), breakdown

    async def resolve_recipient_user_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_id: str,
        recipient_groups: list[str],
        scope_type: str,
        tower_ids: list[str],
    ) -> list[str]:
        """Resolve distinct user ids to notify for a published notice."""
        contact_ids: set[str] = set()
        for group in recipient_groups:
            contact_ids.update(
                await self._resolve_contact_ids_for_group(
                    organization_id=organization_id,
                    project_id=project_id,
                    recipient_group=group,
                    scope_type=scope_type,
                    tower_ids=tower_ids,
                )
            )
        if not contact_ids:
            return []

        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT user_id::text AS user_id
            FROM contacts
            WHERE organization_id = $1::uuid
              AND id = ANY($2::uuid[])
              AND user_id IS NOT NULL
            """,
            organization_id,
            list(contact_ids),
        )
        user_ids = [str(row["user_id"]) for row in rows if row["user_id"]]

        if "Security" in recipient_groups:
            security_user_ids = await self._list_security_user_ids(organization_id=organization_id)
            user_ids.extend(security_user_ids)

        if "Staff" in recipient_groups:
            staff_user_ids = await self.org_members_repo.list_active_member_user_ids(
                organization_id=organization_id,
            )
            user_ids.extend(staff_user_ids)

        return list(dict.fromkeys(user_ids))

    async def _resolve_contact_ids_for_group(
        self,
        *,
        organization_id: str,
        project_id: str,
        recipient_group: str,
        scope_type: str,
        tower_ids: list[str],
    ) -> set[str]:
        """Return contact ids for one recipient group."""
        if recipient_group in {"Owner", "Tenant"}:
            return await self._owner_tenant_contact_ids(
                organization_id=organization_id,
                project_id=project_id,
                role_type=recipient_group,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
        if recipient_group == "Staff":
            return await self._staff_contact_ids(
                organization_id=organization_id,
                project_id=project_id,
            )
        if recipient_group == "Security":
            return set()
        return set()

    async def _owner_tenant_contact_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
        role_type: str,
        scope_type: str,
        tower_ids: list[str],
    ) -> set[str]:
        """Distinct owner/tenant contacts with active roles in scope."""
        tower_filter = ""
        values: list[Any] = [organization_id, project_id, role_type]
        if scope_type == "by_tower" and tower_ids:
            tower_filter = "AND u.tower_id = ANY($4::uuid[])"
            values.append(tower_ids)

        rows = await self.db_connection.fetch(
            f"""
            SELECT DISTINCT cr.contact_id::text AS contact_id
            FROM contact_roles cr
            JOIN units u
              ON u.id = cr.unit_id
             AND u.organization_id = cr.organization_id
            WHERE cr.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND cr.role_type = $3
              AND cr.status = 'active'
              {tower_filter}
            """,
            *values,
        )
        return {str(row["contact_id"]) for row in rows}

    async def _staff_contact_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> set[str]:
        """Distinct staff contacts for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT cr.contact_id::text AS contact_id
            FROM contact_roles cr
            JOIN units u
              ON u.id = cr.unit_id
             AND u.organization_id = cr.organization_id
            WHERE cr.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND cr.role_type = 'Staff'
              AND cr.status = 'active'
            """,
            organization_id,
            project_id,
        )
        return {str(row["contact_id"]) for row in rows}

    async def _list_security_user_ids(self, *, organization_id: str) -> list[str]:
        """Org members with visitor management view permission."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT om.user_id::text AS user_id
            FROM organization_members om
            INNER JOIN role_permissions rp ON om.role_id = rp.role_id
            INNER JOIN permissions p ON rp.permission_id = p.id
            WHERE om.organization_id = $1::uuid
              AND om.status = 'active'
              AND p.code = $2
            """,
            organization_id,
            VISITOR_MANAGEMENT_VIEW,
        )
        return [str(row["user_id"]) for row in rows if row["user_id"]]

    async def is_visible_to_contact(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice: dict[str, Any],
        contact_id: str,
        contact_user_id: str | None,
    ) -> bool:
        """Return whether a live notice is visible to a resident contact."""
        if notice.get("status") != "live":
            return False

        recipient_groups = list(notice.get("recipient_groups") or [])
        if not recipient_groups:
            return False

        scope_type = str(notice.get("scope_type") or "whole_society")
        tower_ids = list(notice.get("tower_ids") or [])

        matched = False
        for group in recipient_groups:
            if group in {"Owner", "Tenant"}:
                if await self._contact_has_role_in_scope(
                    organization_id=organization_id,
                    project_id=project_id,
                    contact_id=contact_id,
                    role_type=group,
                    scope_type=scope_type,
                    tower_ids=tower_ids,
                ):
                    matched = True
                    break
            elif group == "Staff":
                if await self._contact_is_staff(
                    organization_id=organization_id,
                    project_id=project_id,
                    contact_id=contact_id,
                ):
                    matched = True
                    break
            elif group == "Security":
                if contact_user_id and await self._user_is_security(
                    organization_id=organization_id,
                    user_id=contact_user_id,
                ):
                    matched = True
                    break

        return matched

    async def _contact_has_role_in_scope(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
        role_type: str,
        scope_type: str,
        tower_ids: list[str],
    ) -> bool:
        """Check active owner/tenant role for contact within notice scope."""
        tower_filter = ""
        values: list[Any] = [organization_id, project_id, contact_id, role_type]
        if scope_type == "by_tower" and tower_ids:
            tower_filter = "AND u.tower_id = ANY($5::uuid[])"
            values.append(tower_ids)

        row = await self.db_connection.fetchrow(
            f"""
            SELECT 1
            FROM contact_roles cr
            JOIN units u
              ON u.id = cr.unit_id
             AND u.organization_id = cr.organization_id
            WHERE cr.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND cr.contact_id = $3::uuid
              AND cr.role_type = $4
              AND cr.status = 'active'
              {tower_filter}
            LIMIT 1
            """,
            *values,
        )
        return row is not None

    async def _contact_is_staff(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
    ) -> bool:
        """Check whether contact has active staff role in project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM contact_roles cr
            JOIN units u
              ON u.id = cr.unit_id
             AND u.organization_id = cr.organization_id
            WHERE cr.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND cr.contact_id = $3::uuid
              AND cr.role_type = 'Staff'
              AND cr.status = 'active'
            LIMIT 1
            """,
            organization_id,
            project_id,
            contact_id,
        )
        return row is not None

    async def _user_is_security(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> bool:
        """Check whether user has security gate permissions."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM organization_members om
            INNER JOIN role_permissions rp ON om.role_id = rp.role_id
            INNER JOIN permissions p ON rp.permission_id = p.id
            WHERE om.organization_id = $1::uuid
              AND om.user_id = $2::uuid
              AND om.status = 'active'
              AND p.code = $3
            LIMIT 1
            """,
            organization_id,
            user_id,
            VISITOR_MANAGEMENT_VIEW,
        )
        return row is not None

    async def filter_visible_notice_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_contexts: list[dict[str, Any]],
        contact_id: str,
        contact_user_id: str | None,
    ) -> list[str]:
        """Filter notice ids visible to a resident."""
        visible: list[str] = []
        for notice in notice_contexts:
            if await self.is_visible_to_contact(
                organization_id=organization_id,
                project_id=project_id,
                notice=notice,
                contact_id=contact_id,
                contact_user_id=contact_user_id,
            ):
                visible.append(str(notice["id"]))
        return visible

    async def load_notice_contexts(
        self,
        *,
        organization_id: str,
        notice_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Load visibility context for multiple notices."""
        if not notice_ids:
            return []
        rows = await self.db_connection.fetch(
            """
            SELECT
              n.id::text AS id,
              n.project_id::text AS project_id,
              n.status::text AS status,
              n.scope_type::text AS scope_type,
              COALESCE(
                (
                  SELECT array_agg(nr.recipient_group::text)
                  FROM notice_recipients nr
                  WHERE nr.notice_id = n.id
                ),
                ARRAY[]::text[]
              ) AS recipient_groups,
              COALESCE(
                (
                  SELECT array_agg(nt.tower_id::text)
                  FROM notice_towers nt
                  WHERE nt.notice_id = n.id
                ),
                ARRAY[]::text[]
              ) AS tower_ids
            FROM notices n
            WHERE n.organization_id = $1::uuid
              AND n.id = ANY($2::uuid[])
            """,
            organization_id,
            notice_ids,
        )
        return [dict(row) for row in rows]
