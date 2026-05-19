"""Service for user business logic

This service handles all business logic related to users, including
validation, formatting, and orchestration of user operations.
"""

from datetime import datetime
from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.role_repository import RoleRepository
from apps.user_service.app.db.repositories.session_repository import SessionRepository
from apps.user_service.app.schemas.auth import IsometrikDetails
from apps.user_service.app.schemas.common import OrganizationBasicDetails
from apps.user_service.app.schemas.enums import (
    OrganizationMemberRole,
    OrganizationMemberStatus,
)
from apps.user_service.app.schemas.users import (
    PatchUserRequest,
    PermissionInfo,
    RoleInfo,
    RoleInfoWithDescription,
    UpdateUserProfileRequest,
    UserListItem,
    UserProfileData,
    VerificationPreference,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from apps.user_service.app.utils.email_utils import (
    send_org_member_banned_email,
    send_org_member_unbanned_email,
)
from apps.user_service.app.utils.user_utils import (
    build_full_name,
    get_isometrik_details,
    update_supabase_user_email,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_db.supabase_db.auth_repository import (
    get_user_by_id,
    update_metadata,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.isometrik_service import (
    get_isometrik_data_from_settings,
    login_to_isometrik,
    update_isometrik_user,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.super_admin_utils import is_system_super_admin

logger = get_logger("user_service")


def _role_name_is_builtin_admin(role_name: str | None) -> bool:
    """True if the role name is the org built-in full-access role (seeded as ``admin``)."""
    return bool(role_name and role_name.strip().lower() == "admin")


def _member_role_change_context_or_raise(
    ctx: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Validate fetch_context_for_member_role_change result; return ctx and role name or raise."""
    if ctx is None:
        raise NotFoundException(
            message_key="organizations.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    if ctx.get("requester_user_id") is None:
        raise NotFoundException(
            message_key="auth.errors.user_not_member_of_organization",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    if ctx.get("target_user_id") is None:
        raise NotFoundException(
            message_key="users.errors.organization_user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    if ctx.get("new_role_id") is None:
        raise NotFoundException(
            message_key="users.errors.role_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    new_role_name = ctx.get("new_role_name")
    if not new_role_name:
        raise NotFoundException(
            message_key="users.errors.role_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    return ctx, str(new_role_name)


def _assert_requester_may_assign_member_role(
    ctx: dict[str, Any],
    *,
    requester_user_id: str,
    target_user_id: str,
) -> None:
    """Raise ForbiddenException if the requester may not assign the target member's role."""
    created_by_id = ctx.get("created_by_id")
    requester_is_creator = bool(
        created_by_id is not None and str(created_by_id) == str(requester_user_id)
    )
    target_is_creator = bool(
        created_by_id is not None and str(created_by_id) == str(target_user_id)
    )
    if requester_is_creator:
        return
    if target_is_creator:
        raise ForbiddenException(
            message_key="users.errors.cannot_change_organization_creator_role",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    if not _role_name_is_builtin_admin(ctx.get("requester_role_name")):
        raise ForbiddenException(
            message_key="users.errors.cannot_change_member_role",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    if _role_name_is_builtin_admin(ctx.get("target_role_name")):
        raise ForbiddenException(
            message_key="users.errors.cannot_change_admin_user_role",
            custom_code=CustomStatusCode.FORBIDDEN,
        )


def _current_user_data_from_role_change_ctx(
    ctx: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """Build the pre-update member snapshot used for audit from role-change context."""
    return {
        "user_id": str(ctx["target_user_id"]),
        "email": ctx["target_email"],
        "first_name": ctx.get("target_first_name"),
        "last_name": ctx.get("target_last_name"),
        "phone_number": ctx.get("target_phone_number"),
        "phone_isd_code": ctx.get("target_phone_isd_code"),
        "timezone": ctx.get("target_timezone"),
        "avatar_url": ctx.get("target_avatar_url"),
        "status": ctx.get("target_status"),
        "role_id": str(ctx["target_role_id"]) if ctx.get("target_role_id") else "",
        "organization_id": str(ctx.get("target_organization_id") or organization_id),
        "joined_at": ctx.get("target_joined_at"),
        "last_active_at": ctx.get("target_last_active_at"),
    }


def _audit_payload_for_member_role_change(
    *,
    target_user_id: str,
    current_user_data: dict[str, Any],
    organization_id: str,
    new_role_id: str,
    new_role_name: str,
    ctx: dict[str, Any],
    changed_by_user_id: str,
    changed_by_email: str | None,
) -> dict[str, Any]:
    """Build audit payload after a successful member role update."""
    return {
        "user_id": str(target_user_id),
        "email": current_user_data.get("email", ""),
        "first_name": current_user_data.get("first_name") or "",
        "last_name": current_user_data.get("last_name") or "",
        "organization_id": organization_id,
        "role_id": str(new_role_id),
        "role_name": new_role_name,
        "previous_role_id": str(ctx["target_role_id"]) if ctx.get("target_role_id") else "",
        "previous_role_name": ctx.get("target_role_name") or "",
        "changed_by_user_id": changed_by_user_id,
        "changed_by_email": changed_by_email,
    }


class UserService:  # pylint: disable=too-many-public-methods
    """Service for user business logic.

    Handles all business logic related to users, including validation,
    formatting, and orchestration of user operations.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
        sb_client: AsyncClient | None = None,
    ) -> None:
        """Initialize UserService with user context and database connection.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
            sb_client: Supabase client
        """
        self.user_context = user_context
        # Initialize repositories with database connection
        self.organization_member_repository = OrganizationMemberRepository(
            db_connection=db_connection
        )
        self.organization_repository = OrganizationRepository(db_connection=db_connection)
        self.role_repository = RoleRepository(db_connection=db_connection)
        self.supabase_client = sb_client

    async def get_user_profile_by_id(
        self, user_id: str, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        """Get user profile by user ID and optionally organization ID.

        Fetches user from organization_members and enriches with role information.

        Args:
            user_id: User ID
            organization_id: Optional organization ID

        Returns:
            dict containing the user profile with role information or None if not found
        """
        if not user_id or not user_id.strip():
            return None

        if not organization_id:
            return None

        # Get user from organization_members
        user_profile = await self.organization_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not user_profile:
            return None

        # Enrich with role information if role_id exists
        role_id = user_profile.get("role_id")
        if role_id and organization_id:
            role = await self.role_repository.get_role_by_id(role_id, organization_id)
            if role:
                user_profile["roles"] = {
                    "id": str(role["id"]),
                    "name": role["name"],
                    "description": role.get("description", ""),
                }

        return user_profile

    async def get_user_permissions(
        self, user_id: str, organization_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get user permissions through their role.

        Args:
            user_id: User ID
            organization_id: Optional organization ID

        Returns:
            list of permissions
        """
        if not user_id or not user_id.strip():
            return []

        # Get user's role_id from organization_members
        role_id = await self.organization_member_repository.get_user_role_id(
            user_id=user_id, organization_id=organization_id
        )

        if not role_id or not organization_id:
            return []

        # Get permissions for that role using RoleRepository
        permissions = await self.role_repository.get_role_permissions(
            role_id=role_id, organization_id=organization_id
        )

        return [dict(perm) for perm in permissions]

    async def create_new_user(self, user_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new user in the organization.

        Args:
            user_data: User data dictionary

        Returns:
            dict containing the new user
        """
        organization_id = user_data.get("organization_id")
        if not organization_id:
            raise BadRequestException(
                message_key="users.errors.organization_id_required",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        member_data = {
            "user_id": user_data["user_id"],
            "email": user_data["email"],
            "first_name": user_data.get("first_name"),
            "last_name": user_data.get("last_name"),
            "salutation": user_data.get("salutation"),
            "phone_number": user_data.get("phone_number"),
            "phone_isd_code": user_data.get("phone_isd_code"),
            "timezone": user_data.get("timezone", "UTC"),
            "role_id": user_data.get("role_id"),
            "role": user_data.get("role"),
            "member_role": user_data.get("member_role", OrganizationMemberRole.MEMBER.value),
            "status": user_data.get("status", OrganizationMemberStatus.ACTIVE.value),
            "isometrik_user_id": user_data.get("isometrik_user_id"),
        }

        return await self.organization_member_repository.add_member(
            organization_id=organization_id, member_data=member_data
        )

    async def update_user_info(
        self, user_id: str, organization_id: str, update_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update user information.

        Args:
            user_id: User ID
            organization_id: Organization ID
            update_data: Update data dictionary

        Returns:
            dict containing the updated user or None if not found
        """
        return await self.organization_member_repository.update_user_info(
            user_id=user_id, organization_id=organization_id, update_data=update_data
        )

    async def update_organization_member_role(
        self, target_user_id: str, new_role_id: str
    ) -> dict[str, Any]:
        """Assign a new RBAC role to an organization member (authorization: creator or admin).

        Uses a single read (org + members + roles) then one update.
        """
        organization_id = self.user_context.organization_id
        if not organization_id:
            raise ValidationException(
                message_key="organizations.errors.user_not_a_member_of_any_organization",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

        if target_user_id == self.user_context.user_id:
            raise ForbiddenException(
                message_key="users.errors.self_action",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        ctx_raw = await self.organization_member_repository.fetch_context_for_member_role_change(
            organization_id=organization_id,
            requester_user_id=self.user_context.user_id,
            target_user_id=target_user_id,
            new_role_id=new_role_id,
        )
        ctx, new_role_name = _member_role_change_context_or_raise(ctx_raw)

        _assert_requester_may_assign_member_role(
            ctx,
            requester_user_id=self.user_context.user_id,
            target_user_id=target_user_id,
        )

        current_user_data = _current_user_data_from_role_change_ctx(ctx, organization_id)

        updated = await self.organization_member_repository.update_user_info(
            target_user_id,
            organization_id,
            {"role_id": new_role_id, "role": new_role_name},
        )
        if not updated:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        audit_data = _audit_payload_for_member_role_change(
            target_user_id=target_user_id,
            current_user_data=current_user_data,
            organization_id=organization_id,
            new_role_id=new_role_id,
            new_role_name=new_role_name,
            ctx=ctx,
            changed_by_user_id=self.user_context.user_id,
            changed_by_email=self.user_context.email,
        )

        return {
            "audit_data": audit_data,
            "current_user_data": current_user_data,
        }

    async def patch_organization_member(
        self, target_user_id: str, patch: PatchUserRequest
    ) -> dict[str, Any]:
        """Apply PATCH fields that are set on ``patch`` (extend with new branches over time)."""
        if patch.role_id is not None:
            return await self.update_organization_member_role(target_user_id, patch.role_id)
        raise ValidationException(
            message_key="users.errors.no_fields_provided_for_update",
            custom_code=CustomStatusCode.INVALID_DATA,
        )

    async def check_user_exists(self, email: str, organization_id: str) -> bool:
        """Check if user exists in organization.

        Args:
            email: Email address
            organization_id: Organization ID

        Returns:
            bool: True if user exists, False otherwise
        """
        return await self.organization_member_repository.check_user_exists(
            email=email, organization_id=organization_id
        )

    async def check_phone_exists_for_other_user(
        self,
        phone_number: str,
        phone_isd_code: str,
        organization_id: str,
        user_id: str | None = None,
    ) -> bool:
        """Check if phone number with ISD code exists for another user.

        Args:
            phone_number: Phone number (without ISD code)
            phone_isd_code: Phone ISD code (e.g., '+91')
            organization_id: Organization ID
            user_id: Optional user ID to exclude from check

        Returns:
            bool: True if phone number with ISD code exists for another user, False otherwise
        """
        return await self.organization_member_repository.check_phone_exists_for_other_user(
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
            organization_id=organization_id,
            user_id=user_id,
        )

    async def get_users_list(
        self,
        organization_id: str,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get paginated list of users with optional search.

        This method handles the complete business logic for getting users list,
        including fetching data, counting, and transforming to UserListItem.

        Args:
            organization_id: Organization ID
            search: Search query
            limit: Limit
            offset: Offset

        Returns:
            dict with 'users' (list of UserListItem) and 'total_count' (int)
        """
        # Get users list from repository
        users_data = await self.organization_member_repository.get_users_details_list(
            organization_id=organization_id, search=search, limit=limit, offset=offset
        )

        # Get total count
        total_count = await self.organization_member_repository.get_users_total_count(
            organization_id=organization_id, search=search
        )

        # Transform to UserListItem
        users = await self.transform_users(users_data, organization_id)

        return {"users": users, "total_count": total_count}

    async def get_users_total_count(self, organization_id: str, search: str | None = None) -> int:
        """Get total count of users matching search criteria.

        Args:
            organization_id: Organization ID
            search: Search query

        Returns:
            int: Total count of users
        """
        return await self.organization_member_repository.get_users_total_count(
            organization_id=organization_id, search=search
        )

    async def update_user_activity(self, user_id: str, organization_id: str) -> None:
        """Update user's last active timestamp.

        Args:
            user_id: User ID
            organization_id: Organization ID
        """
        await self.organization_member_repository.update_user_activity(
            user_id=user_id, organization_id=organization_id
        )

    async def suspend_user(self, user_id: str, organization_id: str) -> bool:
        """Suspend a user in the organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if user was suspended successfully, False otherwise
        """
        return await self.organization_member_repository.suspend_user(
            user_id=user_id, organization_id=organization_id
        )

    async def revoke_suspended_user(self, user_id: str, organization_id: str) -> bool:
        """Revoke a suspended user in the organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if user was revoked successfully, False otherwise
        """
        return await self.organization_member_repository.revoke_suspended_user(
            user_id=user_id, organization_id=organization_id
        )

    async def update_user_email(
        self, user_id: str, organization_id: str, new_email: str
    ) -> dict[str, Any]:
        """Update user's email address in both organization_members and Supabase Auth.

        Handles all business logic:
        - Validates user exists in organization
        - Updates email in organization_members table
        - Updates email in Supabase Auth
        - Returns current user data for audit

        Args:
            user_id: User ID
            organization_id: Organization ID
            new_email: New email address

        Returns:
            dict containing current_user_data for audit

        Raises:
            NotFoundException: If user not found in organization
        """
        # Get current user data for audit (before update)
        current_user_data = await self.organization_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not current_user_data:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Update in organization_members table
        result = await self.organization_member_repository.update_user_email(
            user_id=user_id, organization_id=organization_id, new_email=new_email
        )

        if not result:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Also update in Supabase Auth
        await update_supabase_user_email(
            user_id,
            organization_id,
            new_email,
            self.organization_member_repository,
            self.supabase_client,
        )

        return {"current_user_data": current_user_data}

    async def get_organization_member_status_by_email(self, email: str) -> str | None:
        """Get organization member status by email.

        Args:
            email: Email address

        Returns:
            str: Organization member status or None if not found
        """
        return await self.organization_member_repository.get_organization_member_status_by_email(
            email=email
        )

    async def get_user_profile_with_metadata(
        self,
        user_id: str,
        organization_id: str | None = None,
        current_user: dict | None = None,
    ) -> dict[str, Any]:
        """Get complete user profile merged with Supabase Auth metadata.
        Args:
            user_id: User ID
            organization_id: Organization ID
            current_user: Current user data
        Returns:
            dict[str, Any]: User profile data
        """
        user_profile = await self.get_user_profile_by_id(user_id, organization_id)

        # Get user data from Supabase Auth
        user_data = await get_user_by_id(self.supabase_client, user_id)

        (
            current_email,
            user_metadata,
            phone_number,
            phone_isd_code,
        ) = self._extract_auth_user_contact(user_data, fallback_email=self.user_context.email)

        user_profile = self._build_or_update_profile(
            base_profile=user_profile,
            user_metadata=user_metadata,
            user_id=user_id,
            current_email=current_email,
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
        )

        user_profile["verification_preference"] = self._extract_verification_preference(
            user_metadata
        )

        identities = self._build_identities(user_data)
        if identities:
            user_profile["identities"] = identities

        if organization_id:
            permissions_data = await self._get_permissions_with_activity(user_id, organization_id)

            if user_profile.get("roles") and isinstance(user_profile["roles"], dict):
                user_profile["role_description"] = user_profile["roles"].get("description", "")

            user_profile["permissions"] = permissions_data

            role_info = self._build_role_info(user_profile)
            permissions = self._format_permissions(permissions_data)

            organization_details = await self._build_organization_details(organization_id)

            isometrik_details = await get_isometrik_details(
                user_id=user_id,
                organization_id=organization_id,
                organization_repository=self.organization_repository,
                organization_member_repository=self.organization_member_repository,
            )
            if isometrik_details is not None:
                isometrik_details = isometrik_details.model_copy(
                    update={"user_id": user_profile.get("isometrik_user_id")}
                )
        else:
            # No organization context
            permissions_data = []
            role_info = None
            permissions = []
            organization_details = None
            isometrik_details = None

        is_superadmin = (
            await is_system_super_admin(current_user) if current_user is not None else False
        )

        profile_data = self._create_user_profile_data(
            user_profile=user_profile,
            role_info=role_info,
            permissions=permissions,
            organization_details=organization_details,
            isometrik_details=isometrik_details,
            is_superadmin=is_superadmin,
        )

        profile_response = profile_data.model_dump(exclude_none=True)

        audit_data = self._build_audit_data(user_profile, permissions)

        return {
            "profile_data": profile_response,
            "audit_data": audit_data,
        }

    async def _build_organization_details(
        self, organization_id: str
    ) -> OrganizationBasicDetails | None:
        """Fetch and normalize organization details for profile responses."""
        organization = await self.organization_repository.get_organization_details(organization_id)
        if not organization:
            return None

        return OrganizationService._map_to_organization_basic_details(organization)

    async def get_user_organizations(self, user_id: str) -> list[OrganizationBasicDetails]:
        """Get user's active organizations with basic details.

        Args:
            user_id: User ID

        Returns:
            list[OrganizationBasicDetails]: List of organization basic details
        """
        organizations_data = await self.organization_repository.get_user_active_organizations(
            user_id
        )
        organizations = [
            OrganizationBasicDetails(
                id=str(org["id"]),
                name=org["name"],
                domain=org.get("domain"),
                logo_url=org.get("logo_url"),
                description=org.get("description"),
            )
            for org in organizations_data
        ]
        return organizations

    @staticmethod
    def _extract_auth_user_contact(
        user_data: Any, fallback_email: str
    ) -> tuple[str, dict[str, Any], str | None, str | None]:
        """Pull email, metadata, and phone info from Supabase auth payload.
        Args:
            user_data: User data (dict from model_dump())
            fallback_email: Fallback email
        Returns:
            tuple[str, dict[str, Any], str | None, str | None]: Email, metadata,
                phone_number, phone_isd_code
        """
        email = fallback_email
        phone_number = None
        phone_isd_code = None
        metadata: dict[str, Any] = {}

        # user_data is a dict from get_user_by_id (model_dump() result)
        if not user_data or not isinstance(user_data, dict):
            return email, metadata, phone_number, phone_isd_code

        # Get email - check new_email first (for email changes), then email, then use fallback
        email = user_data.get("new_email") or user_data.get("email") or email
        metadata = user_data.get("user_metadata", {}) or {}

        # Get phone_number and phone_isd_code from user_metadata
        phone_number = metadata.get("phone_number")
        phone_isd_code = metadata.get("phone_isd_code")

        return email, metadata, phone_number, phone_isd_code

    def _build_or_update_profile(
        self,
        base_profile: dict[str, Any] | None,
        user_metadata: dict[str, Any],
        user_id: str,
        current_email: str,
        phone_number: str | None,
        phone_isd_code: str | None,
    ) -> dict[str, Any]:
        """Create profile from metadata or refresh email/phone on an existing profile.
        Args:
            base_profile: Base profile data
            user_metadata: User metadata
            user_id: User ID
            current_email: Current email
            phone_number: Phone number from user_metadata
            phone_isd_code: Phone ISD code from user_metadata
        Returns:
            dict[str, Any]: Profile data
        """
        if not base_profile:
            first_name = user_metadata.get("first_name", "")
            last_name = user_metadata.get("last_name", "")
            alternate_emails = user_metadata.get("alternate_emails")

            return {
                "user_id": user_id,
                "email": current_email,
                "first_name": first_name,
                "last_name": last_name,
                "avatar_url": user_metadata.get("avatar_url"),
                "phone_number": phone_number,
                "phone_isd_code": phone_isd_code,
                "timezone": user_metadata.get("timezone", "UTC"),
                "salutation": user_metadata.get("salutation"),
                "alternate_emails": alternate_emails,
                # When the user isn't an org member, we avoid extra auth.users DB calls here.
                # Callers that need a definitive value should populate has_password upstream.
                "has_password": False,
                "role_id": None,
                "status": OrganizationMemberStatus.ACTIVE.value,
                "created_at": None,
                "updated_at": None,
                "last_active_at": None,
                "joined_at": None,
                "organization_id": None,
                "roles": None,
            }

        # Update email from Supabase Auth if different
        if base_profile.get("email", "").lower() != current_email.lower():
            base_profile["email"] = current_email

        # Update phone_number and phone_isd_code from user_metadata if available
        if phone_number is not None or phone_isd_code is not None:
            base_profile["phone_number"] = phone_number
            base_profile["phone_isd_code"] = phone_isd_code

        # Update alternate_emails from user_metadata if available
        alternate_emails = user_metadata.get("alternate_emails")
        base_profile["alternate_emails"] = alternate_emails

        return base_profile

    @staticmethod
    def _extract_verification_preference(user_metadata: dict[str, Any]) -> dict[str, Any] | None:
        """Extract verification preference from user metadata.
        Args:
            user_metadata: User metadata
        Returns:
            dict[str, Any] | None: Verification preference
        """
        preference = user_metadata.get("verification_preference")
        return preference if isinstance(preference, dict) else None

    @staticmethod
    def _build_identities(user_data: Any) -> list[dict[str, Any]]:
        """Build identities from user data.
        Args:
            user_data: User data
        Returns:
            list[dict[str, Any]]: Identities
        """
        identities: list[dict[str, Any]] = []

        # user_data is a dict from get_user_by_id (model_dump() result)
        if not user_data or not isinstance(user_data, dict):
            return identities

        identities_list = user_data.get("identities", [])
        if not identities_list:
            return identities

        for identity in identities_list:
            # identity is already a dict
            identity_data = identity.get("identity_data", {})
            provider = identity.get("provider", "")

            # Get provider_id based on provider type
            if provider != "email":
                provider_id = identity_data.get("provider_id")
            else:
                provider_id = identity_data.get("email")

            # Fallback to sub if provider_id not found
            if not provider_id:
                provider_id = identity_data.get("sub")

            # Convert datetime objects to ISO strings if needed
            created_at = identity.get("created_at")
            updated_at = identity.get("updated_at")

            identities.append(
                {
                    "provider": provider,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "provider_id": provider_id,
                }
            )

        return identities

    async def _get_permissions_with_activity(
        self, user_id: str, organization_id: str | None
    ) -> list[dict[str, Any]]:
        """Update activity and fetch permissions when organization_id is provided.
        Args:
            user_id: User ID
            organization_id: Organization ID
        Returns:
            list[dict[str, Any]]: Permissions with activity
        """
        if not organization_id:
            return []

        await self.update_user_activity(user_id, organization_id)
        return await self.get_user_permissions(user_id, organization_id)

    @staticmethod
    def _build_role_info(user_profile: dict[str, Any]) -> RoleInfoWithDescription:
        """Build role information.
        Args:
            user_profile: User profile data
        Returns:
            RoleInfoWithDescription: Role information
        """
        if user_profile.get("role_id") is None:
            return RoleInfoWithDescription(role_id="", description="No organization assigned")
        return RoleInfoWithDescription(
            role_id=str(user_profile["role_id"]),
            role_name=user_profile.get("role", ""),
            description=user_profile.get("role_description", ""),
        )

    @staticmethod
    def _format_permissions(permissions_data: list[dict[str, Any]]) -> list[PermissionInfo]:
        """Format permissions data.
        Args:
            permissions_data: List of permissions data
        Returns:
            list[PermissionInfo]: List of formatted permissions
        """
        return [
            PermissionInfo(
                permission_id=str(p["id"]),
                permission_name=p["name"],
                permission_code=p["code"],
                category=p["category"],
            )
            for p in permissions_data
        ]

    @staticmethod
    def _build_audit_data(user_profile: dict[str, Any], permissions: list[PermissionInfo]) -> dict:
        """Build audit data.
        Args:
            user_profile: User profile data
            permissions: List of permissions
        Returns:
            dict: Audit data
        """
        return {
            "user_id": str(user_profile["user_id"]),
            "email": user_profile["email"],
            "first_name": user_profile["first_name"],
            "last_name": user_profile["last_name"],
            "organization_id": str(user_profile.get("organization_id", "")),
            "role_id": str(user_profile.get("role_id", "")),
            "status": user_profile["status"],
            "permission_count": len(permissions),
            "access_timestamp": datetime.now().isoformat(),
        }

    async def transform_users(
        self, users_data: list[dict[str, Any]], organization_id: str
    ) -> list[UserListItem]:
        """Transform users data to UserListItem.

        This method handles the business logic of transforming raw user data
        into UserListItem format, including fetching permissions count for each role.

        Args:
            users_data: List of users data
            organization_id: Organization ID

        Returns:
            list of UserListItem
        """
        if not users_data:
            return []

        # Group users by role_id to minimize database queries
        role_ids = {user.get("role_id") for user in users_data if user.get("role_id")}

        # Fetch permission counts for all unique roles in a single query
        permissions_count_map = await self.role_repository.get_permission_counts_for_roles(
            role_ids=list(role_ids), organization_id=organization_id
        )

        # Transform users data
        return [
            UserListItem(
                user_id=str(u["user_id"]),
                email=u["email"],
                alternate_emails=u.get("alternate_emails"),
                salutation=u.get("salutation"),
                first_name=u.get("first_name"),
                last_name=u.get("last_name"),
                phone_number=u.get("phone_number"),
                phone_isd_code=u.get("phone_isd_code"),
                role_id=str(u["role_id"]) if u.get("role_id") else "",
                role=u.get("role"),
                member_role=u.get("member_role"),
                status=u.get("status", OrganizationMemberStatus.ACTIVE.value),
                joined_at=(
                    format_iso_datetime(u["joined_at"])
                    if u.get("joined_at")
                    else datetime.now().isoformat()
                ),
                last_active_at=format_iso_datetime(u.get("last_active_at")),
                permissions_count=permissions_count_map.get(str(u.get("role_id")), 0),
                isometrik_user_id=u.get("isometrik_user_id"),
            )
            for u in users_data
        ]

    async def ban_user(self, user_id: str, organization_id: str) -> dict[str, Any]:
        """Ban/suspend a user **from an organization** (not Supabase Auth).

        Handles all business logic for banning:
        - Validates user cannot ban themselves
        - Gets current user data for audit
        - Suspends user in organization (organization_members.status)
        - Revokes all sessions for that user (deletes auth.sessions + terminates user_sessions)
        - Returns audit data

        Args:
            user_id: User ID to ban
            organization_id: Organization ID

        Returns:
            dict[str, Any]: Audit data for the ban operation

        Raises:
            BadRequestException: If user tries to ban themselves
            NotFoundException: If user not found
        """
        # Validate user cannot ban themselves
        if user_id == self.user_context.user_id:
            raise BadRequestException(
                message_key="users.errors.self_action",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Get current user data for audit (before ban)
        current_user_data = await self.organization_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not current_user_data:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Suspend user in organization
        suspend_result = await self.suspend_user(user_id, organization_id)
        if not suspend_result:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Best-effort email notification (do not fail the operation)
        try:
            org = await self.organization_repository.get_organization_by_id(organization_id)
            org_name = (org or {}).get("name") or "your organization"
            send_org_member_banned_email(
                email=current_user_data["email"],
                organization_name=org_name,
                banned_by_email=self.user_context.email,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send org-ban email: user_id=%s org_id=%s error=%s",
                user_id,
                organization_id,
                str(exc),
            )

        # Revoke sessions for that user scoped to this org (kicks them out)
        session_repo = SessionRepository(
            db_connection=self.organization_member_repository.db_connection
        )
        await session_repo.revoke_org_sessions_for_user(user_id, organization_id)

        # Prepare audit data
        audit_data = {
            "user_id": str(current_user_data["user_id"]),
            "email": current_user_data["email"],
            "first_name": current_user_data.get("first_name", ""),
            "last_name": current_user_data.get("last_name", ""),
            "status": OrganizationMemberStatus.SUSPENDED.value,
            "organization_id": str(current_user_data["organization_id"]),
            "banned_by_user_id": self.user_context.user_id,
            "banned_by_email": self.user_context.email,
            "ban_timestamp": datetime.now().isoformat(),
            "ban_reason": "Org-level ban",
        }

        return {
            "audit_data": audit_data,
            "current_user_data": current_user_data,
        }

    async def unban_user(self, user_id: str, organization_id: str) -> dict[str, Any]:
        """Unban/unsuspend a user **from an organization** (not Supabase Auth).

        Handles all business logic for unbanning:
        - Validates user cannot unban themselves
        - Gets current user data for audit
        - Revokes suspension in organization
        - Returns audit data

        Args:
            user_id: User ID to unban
            organization_id: Organization ID

        Returns:
            dict containing:
            - audit_data: Audit data for the unban operation
            - current_user_data: User data before unban (for audit old data)

        Raises:
            BadRequestException: If user tries to unban themselves
            NotFoundException: If user not found
        """
        # Validate user cannot unban themselves
        if user_id == self.user_context.user_id:
            raise BadRequestException(
                message_key="users.errors.self_action",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Get current user data for audit (before unban)
        current_user_data = await self.organization_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not current_user_data:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Revoke suspension in organization
        revoke_result = await self.revoke_suspended_user(user_id, organization_id)
        if not revoke_result:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Best-effort email notification (do not fail the operation)
        try:
            org = await self.organization_repository.get_organization_by_id(organization_id)
            org_name = (org or {}).get("name") or "your organization"
            send_org_member_unbanned_email(
                email=current_user_data["email"],
                organization_name=org_name,
                unbanned_by_email=self.user_context.email,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send org-unban email: user_id=%s org_id=%s error=%s",
                user_id,
                organization_id,
                str(exc),
            )

        # Prepare audit data
        audit_data = {
            "user_id": str(current_user_data["user_id"]),
            "email": current_user_data["email"],
            "first_name": current_user_data.get("first_name", ""),
            "last_name": current_user_data.get("last_name", ""),
            "status": OrganizationMemberStatus.ACTIVE.value,
            "organization_id": str(current_user_data["organization_id"]),
            "unbanned_by_user_id": self.user_context.user_id,
            "unbanned_by_email": self.user_context.email,
            "unban_timestamp": datetime.now().isoformat(),
            "ban_removed": True,
        }

        return {
            "audit_data": audit_data,
            "current_user_data": current_user_data,
        }

    async def update_user_profile(
        self, user_id: str, organization_id: str | None, body: UpdateUserProfileRequest
    ) -> dict[str, Any]:
        """Update user profile information.

        Handles all business logic for updating user profile:
        - Gets current user data
        - Prepares update data and metadata
        - Validates verification method
        - Updates organization_members table
        - Updates Supabase Auth metadata
        - Returns updated profile and audit data

        Args:
            user_id: User ID
            organization_id: Optional organization ID
            body: Update profile request body

        Returns:
            dict[str, Any]: Updated profile and audit data

        Raises:
            BadRequestException: If validation fails
        """
        current_user_data = await self._fetch_profile_for_update(user_id, organization_id)

        update_data, metadata_update = self._build_update_payload(body)

        if not update_data and not metadata_update:
            raise BadRequestException(
                message_key="users.errors.no_fields_provided_for_update",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        if organization_id and update_data:
            await self.update_user_info(user_id, organization_id, update_data)

        if metadata_update:
            await update_metadata(self.supabase_client, user_id, metadata_update)

        updated_profile = await self.get_user_profile_by_id(user_id, organization_id)
        profile_for_audit = updated_profile or current_user_data

        # Update Isometrik user if name or avatar_url changed
        await self._update_isometrik_user_if_needed(
            user_id=user_id,
            organization_id=organization_id,
            body=body,
            updated_profile=updated_profile,
        )

        return {
            "updated_profile": updated_profile,
            "audit_data": self._build_update_audit_data(
                profile_for_audit, organization_id, user_id
            ),
            "current_user_data": current_user_data,
        }

    async def _fetch_profile_for_update(
        self, user_id: str, organization_id: str | None
    ) -> dict[str, Any]:
        """Fetch current profile from org or fallback to Supabase metadata.
        Args:
            user_id: User ID
            organization_id: Organization ID
        Returns:
            dict[str, Any]: Current profile
        """
        if organization_id:
            profile = await self.organization_member_repository.get_user_profile_by_id(
                user_id=user_id, organization_id=organization_id
            )
            if profile:
                return profile

        user_metadata = {}
        user_data = await get_user_by_id(self.supabase_client, user_id)
        if user_data and getattr(user_data, "user", None):
            user_metadata = user_data.user.user_metadata or {}

        first_name = user_metadata.get("first_name", "")
        last_name = user_metadata.get("last_name", "")

        return {
            "user_id": user_id,
            "email": self.user_context.email,
            "first_name": first_name,
            "last_name": last_name,
            "timezone": user_metadata.get("timezone", "UTC"),
            "avatar_url": user_metadata.get("avatar_url"),
            "organization_id": organization_id,
        }

    def _build_update_payload(
        self, body: UpdateUserProfileRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create update payloads for DB and auth metadata.
        Args:
            body: Update user profile request body
        Returns:
            tuple[dict[str, Any], dict[str, Any]]: Update data and metadata update
        """
        update_data: dict[str, Any] = {}
        metadata_update: dict[str, Any] = {}

        if body.first_name is not None:
            update_data["first_name"] = body.first_name
            metadata_update["first_name"] = body.first_name

        if body.last_name is not None:
            update_data["last_name"] = body.last_name
            metadata_update["last_name"] = body.last_name

        if body.timezone is not None:
            update_data["timezone"] = body.timezone
            metadata_update["timezone"] = body.timezone

        if body.avatar_url is not None:
            update_data["avatar_url"] = body.avatar_url
            metadata_update["avatar_url"] = body.avatar_url

        if body.salutation is not None:
            update_data["salutation"] = body.salutation
            metadata_update["salutation"] = body.salutation

        if body.alternate_emails is not None:
            metadata_update["alternate_emails"] = list(set(body.alternate_emails))

        metadata_update |= self._build_verification_metadata(body)

        return update_data, metadata_update

    def _build_verification_metadata(self, body: UpdateUserProfileRequest) -> dict[str, Any]:
        """Validate and construct verification preference metadata.
        Args:
            body: Update user profile request body
        Returns:
            dict[str, Any]: Verification preference metadata
        """
        if body.two_fa_enabled is not None:
            verification_method = body.verification_method.upper()
            if verification_method not in ["PHONE", "EMAIL"]:
                raise BadRequestException(
                    message_key="users.errors.invalid_verification_method",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            return {
                "verification_preference": {
                    "enabled": body.two_fa_enabled,
                    "type": verification_method,
                }
            }

        if body.verification_method and body.verification_method.upper() != "EMAIL":
            raise BadRequestException(
                message_key="users.errors.two_fa_enabled_required",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        return {}

    def _build_update_audit_data(
        self, profile: dict[str, Any], organization_id: str | None, user_id: str
    ) -> dict[str, Any]:
        """Build audit payload for profile update."""
        return {
            "user_id": str(user_id),
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "salutation": profile.get("salutation"),
            "timezone": profile.get("timezone"),
            "avatar_url": profile.get("avatar_url"),
            "organization_id": str(organization_id) if organization_id else None,
            "updated_by_user_id": self.user_context.user_id,
            "updated_by_email": self.user_context.email,
            "update_timestamp": datetime.now().isoformat(),
        }

    def _create_user_profile_data(
        self,
        user_profile: dict[str, Any],
        role_info: RoleInfo | RoleInfoWithDescription | None = None,
        permissions: list[PermissionInfo] | None = None,
        organization_details: OrganizationBasicDetails | None = None,
        isometrik_details: IsometrikDetails | None = None,
        is_superadmin: bool = False,
    ) -> UserProfileData:
        """Creates a UserProfileData object from user profile data.
        This is the single source of truth for creating user profile responses.

        Args:
            user_profile: User profile data from database
            role_info: Optional role information
            permissions: Optional list of permissions
            organization_details: Optional organization details
            isometrik_details: Optional Isometrik integration details
            is_superadmin: Whether the user is a platform superadmin

        Returns:
            UserProfileData object with formatted user profile
        """
        # Extract verification_preference from user_profile dict
        verification_preference = None
        verification_pref_data = user_profile.get("verification_preference")
        if verification_pref_data and isinstance(verification_pref_data, dict):
            verification_preference = VerificationPreference(
                two_fa_enabled=verification_pref_data.get("enabled", False),
                verification_method=verification_pref_data.get("type", ""),
            )
        else:
            verification_preference = VerificationPreference(
                two_fa_enabled=False, verification_method="EMAIL"
            )
        # Get phone_number and phone_isd_code from user_profile
        phone_number = user_profile.get("phone_number", None)
        phone_isd_code = user_profile.get("phone_isd_code", None)

        return UserProfileData(
            user_id=str(user_profile["user_id"]),
            email=user_profile["email"],
            first_name=user_profile["first_name"],
            last_name=user_profile["last_name"],
            avatar_url=user_profile["avatar_url"],
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
            timezone=user_profile["timezone"] or "UTC",
            salutation=user_profile.get("salutation", None),
            status=user_profile["status"],
            joined_at=(
                user_profile["joined_at"].isoformat()
                if user_profile["joined_at"] and isinstance(user_profile["joined_at"], datetime)
                else datetime.now().isoformat()
            ),
            last_active_at=(
                user_profile["last_active_at"].isoformat()
                if user_profile["last_active_at"]
                and isinstance(user_profile["last_active_at"], datetime)
                else user_profile["last_active_at"]
            ),
            role=role_info,
            permissions=permissions or [],
            identities=user_profile.get("identities", []),
            has_password=bool(user_profile.get("has_password", False)),
            verification_preference=verification_preference,
            alternate_emails=user_profile.get("alternate_emails"),
            organization_details=organization_details,
            isometrik_details=isometrik_details,
            member_role=user_profile.get("member_role"),
            is_superadmin=is_superadmin,
        )

    async def _update_isometrik_user_if_needed(
        self,
        user_id: str,
        organization_id: str | None,
        body: UpdateUserProfileRequest,
        updated_profile: UserProfileData,
    ) -> None:
        """Update Isometrik user if name or profile image changed.

        This method is designed to be non-blocking - if Isometrik update fails,
        it logs the error but does not propagate the exception to avoid breaking
        the main user profile update flow.

        Args:
            user_id: User ID
            organization_id: Optional organization ID
            body: Update profile request body
            updated_profile: Updated user profile data (contains complete current state)
        """
        name_updated = body.first_name is not None or body.last_name is not None
        avatar_updated = body.avatar_url is not None

        if not name_updated and not avatar_updated:
            return

        try:
            # Get organization settings
            organization = await self.organization_repository.get_organization_by_id(
                organization_id
            )

            org_settings = parse_json_field(organization.get("settings"))
            isometrik_credentials = get_isometrik_data_from_settings(org_settings)

            # Login to Isometrik to get userToken
            login_response = await login_to_isometrik(
                user_id=user_id,
                isometrik_credentials=isometrik_credentials,
            )

            # Prepare credentials with userToken
            isometrik_update_credentials = {
                "userToken": login_response.get("userToken", ""),
                "licenseKey": isometrik_credentials.get("licenseKey", ""),
                "appSecret": isometrik_credentials.get("appSecret", ""),
            }

            # Build user name from updated profile
            user_name = None
            if name_updated:
                first_name = updated_profile.get("first_name", "")
                last_name = updated_profile.get("last_name", "")
                user_name = build_full_name(first_name, last_name).strip() or None

            # Get avatar URL from updated profile or request body
            user_profile_image_url = None
            if avatar_updated:
                user_profile_image_url = (
                    f"{shared_settings.cloudflare_r2.media_url}/{body.avatar_url}"
                )

            # Update Isometrik user
            await update_isometrik_user(
                isometrik_credentials=isometrik_update_credentials,
                user_name=user_name,
                user_profile_image_url=user_profile_image_url,
            )
        except Exception as e:
            logger.error(
                "Failed to update Isometrik user: user_id=%s, organization_id=%s, error=%s",
                user_id,
                organization_id,
                str(e),
                exc_info=True,
            )
