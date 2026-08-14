"""Resolve notice recipient counts and contact ids."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import ProjectMemberRole, ProjectMemberStatus


class NoticeRecipientResolutionService:
    """Audience sizing and recipient resolution for notices."""

    _STAFF_PROJECT_ROLES = tuple(
        role.value for role in ProjectMemberRole if role != ProjectMemberRole.SECURITY
    )

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

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
        user_ids: set[str] = set()

        for group in recipient_groups:
            if group in {"Owner", "Tenant"}:
                contact_ids = await self._owner_tenant_contact_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                    role_type=group,
                    scope_type=scope_type,
                    tower_ids=tower_ids,
                )
                breakdown[group] = len(contact_ids)
                user_ids.update(
                    await self._user_ids_for_contacts(
                        organization_id=organization_id,
                        contact_ids=contact_ids,
                    )
                )
            elif group == "Staff":
                staff_user_ids = await self._project_staff_user_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                )
                security_user_ids = await self._project_security_user_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                )
                staff_audience = staff_user_ids | security_user_ids
                breakdown[group] = len(staff_audience)
                user_ids.update(staff_audience)
            elif group == "Security":
                security_user_ids = await self._project_security_user_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                )
                breakdown[group] = len(security_user_ids)
                user_ids.update(security_user_ids)

        return len(user_ids), breakdown

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
        del notice_id
        user_ids: set[str] = set()

        for group in recipient_groups:
            if group in {"Owner", "Tenant"}:
                contact_ids = await self._owner_tenant_contact_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                    role_type=group,
                    scope_type=scope_type,
                    tower_ids=tower_ids,
                )
                user_ids.update(
                    await self._user_ids_for_contacts(
                        organization_id=organization_id,
                        contact_ids=contact_ids,
                    )
                )
            elif group == "Staff":
                user_ids.update(
                    await self._project_staff_user_ids(
                        organization_id=organization_id,
                        project_id=project_id,
                    )
                )
                user_ids.update(
                    await self._project_security_user_ids(
                        organization_id=organization_id,
                        project_id=project_id,
                    )
                )
            elif group == "Security":
                user_ids.update(
                    await self._project_security_user_ids(
                        organization_id=organization_id,
                        project_id=project_id,
                    )
                )

        return list(user_ids)

    async def _user_ids_for_contacts(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
    ) -> set[str]:
        """Map resident contact ids to portal user ids."""
        if not contact_ids:
            return set()

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
        return {str(row["user_id"]) for row in rows if row["user_id"]}

    async def _project_staff_user_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> set[str]:
        """Active project members assigned as staff (non-security roles)."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT pm.user_id::text AS user_id
            FROM project_members pm
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.status = $3
              AND pm.role = ANY($4::project_member_role[])
            """,
            organization_id,
            project_id,
            ProjectMemberStatus.ACTIVE.value,
            list(self._STAFF_PROJECT_ROLES),
        )
        return {str(row["user_id"]) for row in rows if row["user_id"]}

    async def _project_security_user_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> set[str]:
        """Active project members assigned with the security role."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT pm.user_id::text AS user_id
            FROM project_members pm
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.status = $3
              AND pm.role = $4::project_member_role
            """,
            organization_id,
            project_id,
            ProjectMemberStatus.ACTIVE.value,
            ProjectMemberRole.SECURITY.value,
        )
        return {str(row["user_id"]) for row in rows if row["user_id"]}

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

    async def _user_is_project_staff(
        self,
        *,
        organization_id: str,
        project_id: str,
        user_id: str,
    ) -> bool:
        """Return whether the user is an active non-security project member."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM project_members pm
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.user_id = $3::uuid
              AND pm.status = $4
              AND pm.role = ANY($5::project_member_role[])
            LIMIT 1
            """,
            organization_id,
            project_id,
            user_id,
            ProjectMemberStatus.ACTIVE.value,
            list(self._STAFF_PROJECT_ROLES),
        )
        return row is not None

    async def _user_is_project_security(
        self,
        *,
        organization_id: str,
        project_id: str,
        user_id: str,
    ) -> bool:
        """Return whether the user is an active security project member."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM project_members pm
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.user_id = $3::uuid
              AND pm.status = $4
              AND pm.role = $5::project_member_role
            LIMIT 1
            """,
            organization_id,
            project_id,
            user_id,
            ProjectMemberStatus.ACTIVE.value,
            ProjectMemberRole.SECURITY.value,
        )
        return row is not None

    async def is_visible_to_contact(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice: dict[str, Any],
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> bool:
        """Return whether a live notice is visible to the caller."""
        if notice.get("status") != "live":
            return False

        recipient_groups = list(notice.get("recipient_groups") or [])
        if not recipient_groups:
            return False

        scope_type = str(notice.get("scope_type") or "whole_society")
        tower_ids = list(notice.get("tower_ids") or [])

        for group in recipient_groups:
            if group in {"Owner", "Tenant"}:
                if contact_id and await self._contact_has_role_in_scope(
                    organization_id=organization_id,
                    project_id=project_id,
                    contact_id=contact_id,
                    role_type=group,
                    scope_type=scope_type,
                    tower_ids=tower_ids,
                ):
                    return True
            elif group == "Staff" and contact_user_id:
                if await self._user_is_project_staff(
                    organization_id=organization_id,
                    project_id=project_id,
                    user_id=contact_user_id,
                ) or await self._user_is_project_security(
                    organization_id=organization_id,
                    project_id=project_id,
                    user_id=contact_user_id,
                ):
                    return True
            elif group == "Security" and contact_user_id:
                if await self._user_is_project_security(
                    organization_id=organization_id,
                    project_id=project_id,
                    user_id=contact_user_id,
                ):
                    return True

        return False

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
        if not str(contact_id or "").strip():
            return False

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

    async def filter_visible_notice_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
        notice_contexts: list[dict[str, Any]],
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> list[str]:
        """Filter notice ids visible to a resident or project member viewer."""
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
