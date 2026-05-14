"""Superadmin organization operations (platform scope, no org RBAC)."""

from __future__ import annotations

import math
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.schemas.enums import (
    PlanType,
    SuperadminOrganizationListSortField,
    SuperadminOrganizationListSortOrder,
    SuperadminOrganizationListStatus,
)
from apps.user_service.app.schemas.organizations import OrganizationInfo
from apps.user_service.app.schemas.superadmin_organizations import (
    SuperadminOrganizationListItem,
    SuperadminOrganizationListResult,
    SuperadminOrgOwnerAdmin,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import (
    format_iso_datetime,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class SuperadminOrganizationService:
    """Organization listing/detail for system super admins."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self._org_repo = OrganizationRepository(db_connection=db_connection)

    @staticmethod
    def _owner_full_name(row: dict[str, Any]) -> str | None:
        """Owner full name from member profile"""
        parts = [row.get("owner_first_name") or "", row.get("owner_last_name") or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or None

    @staticmethod
    def _map_list_row(row: dict[str, Any]) -> SuperadminOrganizationListItem:
        """Map database row to SuperadminOrganizationListItem"""
        return SuperadminOrganizationListItem(
            organization_id=str(row["id"]),
            name=row.get("name") or "",
            admin=SuperadminOrgOwnerAdmin(
                user_id=str(row["owner_user_id"]) if row.get("owner_user_id") else None,
                full_name=SuperadminOrganizationService._owner_full_name(row),
                email=row.get("owner_email"),
            ),
            member_count=int(row.get("member_count") or 0),
            plan_type=str(row.get("plan_type") or "trial"),
            status=SuperadminOrganizationListStatus(row["list_status"]),
            created_at=format_iso_datetime(row.get("created_at")),
        )

    async def list_organizations(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        plan: PlanType | None,
        status: SuperadminOrganizationListStatus | None,
        sort: SuperadminOrganizationListSortField,
        order: SuperadminOrganizationListSortOrder,
    ) -> SuperadminOrganizationListResult:
        """Retrieve paginated list of organizations for superadmin"""
        list_status_value = status.value if status else None
        plan_value = plan.value if plan else None
        offset = (page - 1) * page_size
        rows, total = await self._org_repo.get_superadmin_organizations_list(
            search=search,
            plan_type=plan_value,
            list_status=list_status_value,
            sort_field=sort.value,
            sort_order=order.value,
            limit=page_size,
            offset=offset,
        )
        items = [self._map_list_row(r) for r in rows]
        total_pages = math.ceil(total / page_size) if page_size else 0
        message = "success.no_data" if total == 0 else "success.retrieved"
        return SuperadminOrganizationListResult(
            items=items,
            total_count=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            message=message,
        )

    async def get_organization_detail(self, organization_id: str) -> OrganizationInfo:
        """Get organization detail for superadmin"""
        org = await self._org_repo.get_organization_by_id(organization_id)
        if not org:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return OrganizationService._map_to_organization_info(org)
