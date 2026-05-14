"""Superadmin organization operations (platform scope, no org RBAC)."""

from __future__ import annotations

import math
from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.session_repository import SessionRepository
from apps.user_service.app.schemas.enums import (
    PlanType,
    SuperadminOrganizationListSortField,
    SuperadminOrganizationListSortOrder,
    SuperadminOrganizationListStatus,
)
from apps.user_service.app.schemas.organizations import OrganizationInfo
from apps.user_service.app.schemas.superadmin_organizations import (
    SuperadminImpersonationResponse,
    SuperadminOrganizationListItem,
    SuperadminOrganizationListResult,
    SuperadminOrgOwnerAdmin,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import (
    format_iso_datetime,
)
from libs.shared_db.supabase_db.auth_repository import (
    generate_magiclink_and_exchange_for_session,
)
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.super_admin_utils import is_system_super_admin


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

    async def impersonate_organization_owner(
        self,
        *,
        organization_id: str,
        supabase_admin_client: AsyncClient,
    ) -> SuperadminImpersonationResponse:
        """Exchange a magic link for the org owner's session (superadmin-only caller)."""
        row = await self._org_repo.get_organization_with_owner_for_impersonation(organization_id)
        if not row:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        email = (row.get("owner_email") or "").strip()
        if not email:
            raise BadRequestException(
                message_key="organizations.errors.owner_not_available_for_impersonation",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        verify_response = await generate_magiclink_and_exchange_for_session(
            admin_client=supabase_admin_client,
            email=email,
        )
        session = getattr(verify_response, "session", None)
        user = getattr(verify_response, "user", None)
        if not session or not getattr(session, "access_token", None):
            raise BadRequestException(
                message_key="organizations.errors.impersonation_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        auth_user_id = getattr(user, "id", None) if user is not None else None
        impersonated_id = str(auth_user_id) if auth_user_id else str(row.get("owner_user_id") or "")

        return SuperadminImpersonationResponse(
            access_token=session.access_token,
            refresh_token=getattr(session, "refresh_token", None),
            expires_in=getattr(session, "expires_in", None),
            expires_at=getattr(session, "expires_at", None),
            token_type=getattr(session, "token_type", None) or "bearer",
            organization_id=str(row["id"]),
            organization_name=row.get("name"),
            impersonated_user_id=impersonated_id or None,
        )

    async def exit_impersonation_session(self, *, current_user: dict) -> dict[str, Any]:
        """Revoke the current Supabase auth session (impersonated user Bearer token).

        Call with the **owner / impersonated** access token, not the platform superadmin token.
        Revocation deletes the current row from ``auth.sessions`` (same table as session revoke).
        """
        if await is_system_super_admin(current_user):
            raise BadRequestException(
                message_key="organizations.errors.exit_impersonation_use_owner_token",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )
        user_id = current_user.get("sub")
        session_id = current_user.get("session_id")
        if not user_id or not session_id:
            raise BadRequestException(
                message_key="organizations.errors.exit_impersonation_invalid_token",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )
        session_repo = SessionRepository(db_connection=self._org_repo.db_connection)
        revoked_session_id = await session_repo.delete_auth_session(session_id=str(session_id))
        if not revoked_session_id:
            raise NotFoundException(
                message_key="organizations.errors.exit_impersonation_session_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"session_id": revoked_session_id}
