"""Superadmin organization operations (platform scope, no org RBAC)."""

from __future__ import annotations

import logging
import math
from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories import (
    OrganizationMemberRepository,
    UserRepository,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.session_repository import SessionRepository
from apps.user_service.app.schemas.auth import SelectOrganizationResponse
from apps.user_service.app.schemas.enums import (
    OrganizationStatus,
    PlanType,
    SuperadminOrganizationListSortField,
    SuperadminOrganizationListSortOrder,
    SuperadminOrganizationListStatus,
)
from apps.user_service.app.schemas.organizations import (
    NewOrganizationBody,
    OrganizationInfo,
)
from apps.user_service.app.schemas.superadmin_organizations import (
    SuperadminImpersonationResponse,
    SuperadminOrganizationListItem,
    SuperadminOrganizationListResult,
    SuperadminOrgOwnerAdmin,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.services.session_management_service import (
    SessionManagementService,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    validate_uuid_format,
)
from apps.user_service.app.utils.user_utils import get_isometrik_details
from libs.shared_db.supabase_db.auth_repository import (
    generate_magiclink_and_exchange_for_session,
)
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.super_admin_utils import is_system_super_admin

logger = logging.getLogger(__name__)


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

    async def create_organization(
        self,
        *,
        owner_user_id: str,
        body: NewOrganizationBody,
    ) -> dict[str, Any]:
        """Create an organization owned by an existing auth user (no session linking)."""
        validate_uuid_format(owner_user_id, "owner_user_id")
        user_repo = UserRepository(db_connection=self._org_repo.db_connection)
        owner = await user_repo.get_user_details_by_id(
            owner_user_id, select_columns=["id", "email"]
        )
        email = (owner.get("email") or "").strip() if owner else ""
        if not owner or not email:
            raise NotFoundException(
                message_key="organizations.errors.owner_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        owner_context = UserContext(user_id=str(owner["id"]), email=email)
        org_service = OrganizationService(
            user_context=owner_context,
            db_connection=self._org_repo.db_connection,
        )
        return await org_service.create_organization_for_owner(body=body)

    async def suspend_organization(self, organization_id: str) -> None:
        """Set organization status to suspended (platform superadmin)."""
        validate_uuid_format(organization_id, "organization_id")
        updated = await self._org_repo.update_organization(
            organization_id,
            {"status": OrganizationStatus.SUSPENDED.value},
        )
        if not updated.get("id"):
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def reactivate_organization(self, organization_id: str) -> None:
        """Restore a suspended organization to active (platform superadmin).

        Trial billing lives on ``subscription.plan_type``; ``organizations.status``
        only allows values enforced by ``organizations_status_check`` (e.g. active).
        """
        validate_uuid_format(organization_id, "organization_id")
        org = await self._org_repo.get_organization_by_id(organization_id)
        if not org or str(org.get("status") or "") != OrganizationStatus.SUSPENDED.value:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        updated = await self._org_repo.update_organization(
            organization_id,
            {"status": OrganizationStatus.ACTIVE.value},
        )
        if not updated.get("id"):
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def permanently_delete_organization(
        self,
        organization_id: str,
        *,
        actor_user_id: str,
        actor_email: str,
    ) -> dict[str, Any]:
        """Direct org deletion: same cascade and member emails as delete-request approval."""
        org_service = OrganizationService(
            user_context=UserContext(user_id=actor_user_id, email=actor_email),
            db_connection=self._org_repo.db_connection,
        )
        return await org_service.permanently_delete_organization(organization_id)

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

        select_organization: SelectOrganizationResponse | None = None
        if impersonated_id:
            session_manager = SessionManagementService(db_connection=self._org_repo.db_connection)
            impersonation_session_id = await session_manager._extract_session_id(
                session, supabase_admin_client
            )
            await session_manager.update_session_organization_context(
                session_id=impersonation_session_id,
                user_id=impersonated_id,
                organization_id=organization_id,
            )
            select_organization = await self._build_select_organization_response(
                user_id=impersonated_id,
                organization_id=organization_id,
            )

        return SuperadminImpersonationResponse(
            access_token=session.access_token,
            refresh_token=getattr(session, "refresh_token", None),
            expires_in=getattr(session, "expires_in", None),
            expires_at=getattr(session, "expires_at", None),
            token_type=getattr(session, "token_type", None) or "bearer",
            organization_id=str(row["id"]),
            organization_name=row.get("name"),
            impersonated_user_id=impersonated_id or None,
            select_organization=select_organization,
        )

    async def _build_select_organization_response(
        self,
        *,
        user_id: str,
        organization_id: str,
    ) -> SelectOrganizationResponse | None:
        """Build select-org payload after linking the impersonation session."""
        try:
            org_member_repo = OrganizationMemberRepository(
                db_connection=self._org_repo.db_connection
            )
            isometrik_details = await get_isometrik_details(
                user_id=user_id,
                organization_id=organization_id,
                organization_repository=self._org_repo,
                organization_member_repository=org_member_repo,
            )
            return SelectOrganizationResponse(isometrik_details=isometrik_details)
        except Exception as exc:
            logger.warning(
                "Failed to build select-organization response for impersonation user %s org %s: %s",
                user_id,
                organization_id,
                str(exc),
            )
            return None

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
        revoked = await session_repo.delete_auth_session(
            session_id=str(session_id),
            user_id=str(user_id),
        )
        if not revoked:
            raise NotFoundException(
                message_key="organizations.errors.exit_impersonation_session_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return revoked
