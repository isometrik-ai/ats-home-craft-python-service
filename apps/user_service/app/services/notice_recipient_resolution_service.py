"""Resolve notice recipient counts and contact ids."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import (
    ClientStatus,
    NoticeRecipientGroup,
    ProjectMemberRole,
    ProjectMemberStatus,
)


class NoticeRecipientResolutionService:
    """Audience sizing and recipient resolution for notices."""

    _STAFF_MANAGER_GROUP = NoticeRecipientGroup.STAFF_MANAGER.value
    _STAFF_MANAGER_ROLE_SLUGS = (
        "staff_manager",
        ProjectMemberRole.COMMUNITY_ADMIN.value,
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
        contact_ids: set[str] = set()
        staff_security_user_ids: set[str] = set()

        for group in recipient_groups:
            if group in {"Owner", "Tenant"}:
                group_contact_ids = await self._owner_tenant_contact_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                    role_type=group,
                    scope_type=scope_type,
                    tower_ids=tower_ids,
                )
                breakdown[group] = len(group_contact_ids)
                contact_ids.update(group_contact_ids)
            elif group == self._STAFF_MANAGER_GROUP:
                staff_manager_user_ids = await self._project_staff_manager_user_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                )
                breakdown[group] = len(staff_manager_user_ids)
                staff_security_user_ids.update(staff_manager_user_ids)
            elif group == "Security":
                security_user_ids = await self._project_security_user_ids(
                    organization_id=organization_id,
                    project_id=project_id,
                )
                breakdown[group] = len(security_user_ids)
                staff_security_user_ids.update(security_user_ids)

        total = await self._count_distinct_recipients(
            organization_id=organization_id,
            contact_ids=contact_ids,
            staff_security_user_ids=staff_security_user_ids,
        )
        return total, breakdown

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
            elif group == self._STAFF_MANAGER_GROUP:
                user_ids.update(
                    await self._project_staff_manager_user_ids(
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

    async def _count_distinct_recipients(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
        staff_security_user_ids: set[str],
    ) -> int:
        """Count unique people across resident contacts and staff/security users."""
        if not contact_ids and not staff_security_user_ids:
            return 0

        portal_contact_ids, contacts_without_portal = await self._partition_contacts_by_portal(
            organization_id=organization_id,
            contact_ids=contact_ids,
        )
        portal_user_ids = await self._user_ids_for_contacts(
            organization_id=organization_id,
            contact_ids=portal_contact_ids,
        )
        return len(portal_user_ids | staff_security_user_ids) + len(contacts_without_portal)

    async def _partition_contacts_by_portal(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
    ) -> tuple[set[str], set[str]]:
        """Split contact ids into those with and without linked portal user accounts."""
        if not contact_ids:
            return set(), set()

        rows = await self.db_connection.fetch(
            """
            SELECT id::text AS contact_id
            FROM contacts
            WHERE organization_id = $1::uuid
              AND id = ANY($2::uuid[])
              AND user_id IS NOT NULL
            """,
            organization_id,
            list(contact_ids),
        )
        portal_contact_ids = {str(row["contact_id"]) for row in rows if row["contact_id"]}
        contacts_without_portal = contact_ids - portal_contact_ids
        return portal_contact_ids, contacts_without_portal

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

    async def _project_staff_manager_user_ids(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> set[str]:
        """Active project members assigned with the Staff Manager role."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT pm.user_id::text AS user_id
            FROM project_members pm
            INNER JOIN project_roles pr
              ON pr.id = pm.project_role_id
             AND pr.project_id = pm.project_id
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.status = $3
              AND (
                pr.slug = ANY($4::text[])
                OR lower(trim(pr.name)) = lower($5)
              )
            """,
            organization_id,
            project_id,
            ProjectMemberStatus.ACTIVE.value,
            list(self._STAFF_MANAGER_ROLE_SLUGS),
            self._STAFF_MANAGER_GROUP,
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
            INNER JOIN project_roles pr
              ON pr.id = pm.project_role_id
             AND pr.project_id = pm.project_id
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.status = $3
              AND pr.slug = $4
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
        """Distinct owner/tenant contacts scoped like the contacts list API."""
        tower_filter = ""
        values: list[Any] = [
            organization_id,
            ClientStatus.DELETED.value,
            role_type,
            project_id,
        ]
        if scope_type == "by_tower" and tower_ids:
            tower_filter = """
              AND EXISTS (
                SELECT 1
                FROM units u
                WHERE u.id = cu.unit_id
                  AND u.organization_id = cu.organization_id
                  AND u.tower_id = ANY($5::uuid[])
              )
            """
            values.append(tower_ids)

        rows = await self.db_connection.fetch(
            f"""
            SELECT DISTINCT cr.contact_id::text AS contact_id
            FROM contacts ct
            INNER JOIN contact_roles cr
              ON cr.contact_id = ct.id
             AND cr.organization_id = ct.organization_id
            WHERE ct.organization_id = $1::uuid
              AND ct.status <> $2
              AND cr.role_type = $3::public.contact_role_type
              AND cr.status = 'active'::public.contact_role_status
              AND cr.ended_at IS NULL
              AND EXISTS (
                SELECT 1
                FROM contact_units cu
                WHERE cu.contact_id = ct.id
                  AND cu.organization_id = ct.organization_id
                  AND cu.project_id = $4::uuid
                  AND cu.status IN ('active', 'pending')
                  {tower_filter}
              )
            """,
            *values,
        )
        return {str(row["contact_id"]) for row in rows}

    async def _user_is_project_staff_manager(
        self,
        *,
        organization_id: str,
        project_id: str,
        user_id: str,
    ) -> bool:
        """Return whether the user is an active Staff Manager project member."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM project_members pm
            INNER JOIN project_roles pr
              ON pr.id = pm.project_role_id
             AND pr.project_id = pm.project_id
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.user_id = $3::uuid
              AND pm.status = $4
              AND (
                pr.slug = ANY($5::text[])
                OR lower(trim(pr.name)) = lower($6)
              )
            LIMIT 1
            """,
            organization_id,
            project_id,
            user_id,
            ProjectMemberStatus.ACTIVE.value,
            list(self._STAFF_MANAGER_ROLE_SLUGS),
            self._STAFF_MANAGER_GROUP,
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
            INNER JOIN project_roles pr
              ON pr.id = pm.project_role_id
             AND pr.project_id = pm.project_id
            WHERE pm.organization_id = $1::uuid
              AND pm.project_id = $2::uuid
              AND pm.user_id = $3::uuid
              AND pm.status = $4
              AND pr.slug = $5
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
            elif group == self._STAFF_MANAGER_GROUP and contact_user_id:
                if await self._user_is_project_staff_manager(
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
        values: list[Any] = [
            organization_id,
            ClientStatus.DELETED.value,
            contact_id,
            role_type,
            project_id,
        ]
        if scope_type == "by_tower" and tower_ids:
            tower_filter = """
              AND EXISTS (
                SELECT 1
                FROM units u
                WHERE u.id = cu.unit_id
                  AND u.organization_id = cu.organization_id
                  AND u.tower_id = ANY($6::uuid[])
              )
            """
            values.append(tower_ids)

        row = await self.db_connection.fetchrow(
            f"""
            SELECT 1
            FROM contacts ct
            INNER JOIN contact_roles cr
              ON cr.contact_id = ct.id
             AND cr.organization_id = ct.organization_id
            WHERE ct.organization_id = $1::uuid
              AND ct.status <> $2
              AND cr.contact_id = $3::uuid
              AND cr.role_type = $4::public.contact_role_type
              AND cr.status = 'active'::public.contact_role_status
              AND cr.ended_at IS NULL
              AND EXISTS (
                SELECT 1
                FROM contact_units cu
                WHERE cu.contact_id = ct.id
                  AND cu.organization_id = ct.organization_id
                  AND cu.project_id = $5::uuid
                  AND cu.status IN ('active', 'pending')
                  {tower_filter}
              )
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
