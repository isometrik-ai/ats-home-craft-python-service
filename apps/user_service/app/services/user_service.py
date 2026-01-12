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
from apps.user_service.app.schemas.enums import OrganizationMemberStatus
from apps.user_service.app.schemas.organizations import OrganizationBasicDetails
from apps.user_service.app.schemas.users import (
    PermissionInfo,
    RoleInfo,
    RoleInfoWithDescription,
    UpdateUserProfileRequest,
    UserListItem,
    UserProfileData,
    VerificationPreference,
)
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from apps.user_service.app.utils.user_utils import (
    update_supabase_user_email,
)
from libs.shared_db.supabase_db.auth_repository import (
    ban_user,
    get_user_by_id,
    unban_user,
    update_metadata,
)
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class UserService:
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

    async def delete_user(self, user_id: str, organization_id: str) -> bool:
        """Delete user from organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if user was deleted successfully, False otherwise
        """
        return await self.organization_member_repository.delete_user(
            user_id=user_id, organization_id=organization_id
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
        self, user_id: str, organization_id: str | None = None
    ) -> dict[str, Any]:
        """Get complete user profile merged with Supabase Auth metadata.
        Args:
            user_id: User ID
            organization_id: Organization ID
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

        permissions_data = await self._get_permissions_with_activity(user_id, organization_id)

        if user_profile.get("roles") and isinstance(user_profile["roles"], dict):
            user_profile["role_description"] = user_profile["roles"].get("description", "")

        user_profile["permissions"] = permissions_data

        role_info = self._build_role_info(user_profile)
        permissions = self._format_permissions(permissions_data)

        organization_details = await self._build_organization_details(organization_id)

        profile_data = self._create_user_profile_data(
            user_profile=user_profile,
            role_info=role_info,
            permissions=permissions,
            organization_details=organization_details,
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

    @staticmethod
    def _extract_auth_user_contact(
        user_data: Any, fallback_email: str
    ) -> tuple[str, dict[str, Any], str | None, str | None]:
        """Pull email, metadata, and phone info from Supabase auth payload.
        Args:
            user_data: User data
            fallback_email: Fallback email
        Returns:
            tuple[str, dict[str, Any], str | None, str | None]: Email, metadata,
                phone_number, phone_isd_code
        """
        email = fallback_email
        phone_number = None
        phone_isd_code = None
        metadata: dict[str, Any] = {}

        user_obj = getattr(user_data, "user", None)
        if not user_obj:
            return email, metadata, phone_number, phone_isd_code

        email = getattr(user_obj, "email_change", None) or getattr(user_obj, "email", email)
        metadata = getattr(user_obj, "user_metadata", {}) or {}

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
            # Compute full_name from first_name and last_name
            full_name = f"{first_name} {last_name}".strip() or user_metadata.get(
                "full_name", current_email.split("@")[0]
            )
            return {
                "user_id": user_id,
                "email": current_email,
                "full_name": full_name,
                "first_name": first_name,
                "last_name": last_name,
                "avatar_url": user_metadata.get("avatar_url"),
                "phone_number": phone_number,
                "phone_isd_code": phone_isd_code,
                "timezone": user_metadata.get("timezone", "UTC"),
                "salutation": user_metadata.get("salutation"),
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

        # Compute full_name from first_name and last_name if not present
        if "full_name" not in base_profile or not base_profile.get("full_name"):
            first_name = base_profile.get("first_name", "")
            last_name = base_profile.get("last_name", "")
            base_profile["full_name"] = f"{first_name} {last_name}".strip()

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
        user_obj = getattr(user_data, "user", None)
        if not user_obj or not hasattr(user_obj, "identities"):
            return identities

        for identity in user_obj.identities:
            provider_id = (
                identity.identity_data.get("provider_id")
                if identity.provider != "email"
                else identity.identity_data.get("email")
            ) or identity.identity_data.get("sub")

            identities.append(
                {
                    "provider": identity.provider,
                    "created_at": identity.created_at,
                    "updated_at": identity.updated_at,
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
            "full_name": user_profile["full_name"],
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
                salutation=u.get("salutation"),
                first_name=u.get("first_name"),
                last_name=u.get("last_name"),
                phone_number=u.get("phone_number"),
                phone_isd_code=u.get("phone_isd_code"),
                role_id=str(u["role_id"]) if u.get("role_id") else "",
                status=u.get("status", OrganizationMemberStatus.ACTIVE.value),
                joined_at=(
                    format_iso_datetime(u["joined_at"])
                    if u.get("joined_at")
                    else datetime.now().isoformat()
                ),
                last_active_at=format_iso_datetime(u.get("last_active_at")),
                permissions_count=permissions_count_map.get(str(u.get("role_id")), 0),
            )
            for u in users_data
        ]

    async def ban_user(self, user_id: str, organization_id: str) -> dict[str, Any]:
        """Ban a user in the organization.

        Handles all business logic for banning:
        - Validates user cannot ban themselves
        - Gets current user data for audit
        - Bans user in Supabase Auth
        - Suspends user in organization
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

        # Ban user in Supabase Auth
        result = await ban_user(self.supabase_client, user_id)
        if not result:
            raise NotFoundException(
                message_key="users.errors.user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Suspend user in organization
        suspend_result = await self.suspend_user(user_id, organization_id)
        if not suspend_result:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Compute full_name from first_name and last_name
        first_name = current_user_data.get("first_name", "")
        last_name = current_user_data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or None

        # Prepare audit data
        audit_data = {
            "user_id": str(current_user_data["user_id"]),
            "email": current_user_data["email"],
            "full_name": full_name,
            "status": OrganizationMemberStatus.SUSPENDED.value,
            "organization_id": str(current_user_data["organization_id"]),
            "banned_by_user_id": self.user_context.user_id,
            "banned_by_email": self.user_context.email,
            "ban_timestamp": datetime.now().isoformat(),
            "ban_reason": "Admin ban action",
        }

        return {
            "audit_data": audit_data,
            "current_user_data": current_user_data,
        }

    async def unban_user(self, user_id: str, organization_id: str) -> dict[str, Any]:
        """Unban a user in the organization.

        Handles all business logic for unbanning:
        - Validates user cannot unban themselves
        - Gets current user data for audit
        - Unbans user in Supabase Auth
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

        # Unban user in Supabase Auth
        result = await unban_user(self.supabase_client, user_id)
        if not result:
            raise NotFoundException(
                message_key="users.errors.user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Revoke suspension in organization
        revoke_result = await self.revoke_suspended_user(user_id, organization_id)
        if not revoke_result:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Compute full_name from first_name and last_name
        first_name = current_user_data.get("first_name", "")
        last_name = current_user_data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or ""

        # Prepare audit data
        audit_data = {
            "user_id": str(current_user_data["user_id"]),
            "email": current_user_data["email"],
            "full_name": full_name,
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

        update_data, metadata_update = self._build_update_payload(body, current_user_data)

        if not update_data and not metadata_update:
            raise BadRequestException(
                message_key="users.errors.no_fields_provided_for_update",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        if organization_id and update_data:
            await self.update_user_info(user_id, organization_id, update_data)

        if metadata_update:
            merged_metadata = await self._merge_metadata(user_id, metadata_update)
            await update_metadata(self.supabase_client, user_id, merged_metadata)

        updated_profile = await self.get_user_profile_by_id(user_id, organization_id)
        profile_for_audit = updated_profile or current_user_data

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

        # Compute full_name from first_name and last_name
        first_name = user_metadata.get("first_name", "")
        last_name = user_metadata.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or user_metadata.get("full_name", "")

        return {
            "user_id": user_id,
            "email": self.user_context.email,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "timezone": user_metadata.get("timezone", "UTC"),
            "avatar_url": user_metadata.get("avatar_url"),
            "organization_id": organization_id,
        }

    def _build_update_payload(
        self, body: UpdateUserProfileRequest, current_user_data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create update payloads for DB and auth metadata.
        Args:
            body: Update user profile request body
            current_user_data: Current user data
        Returns:
            tuple[dict[str, Any], dict[str, Any]]: Update data and metadata update
        """
        update_data: dict[str, Any] = {}
        metadata_update: dict[str, Any] = {}

        current_first_name = current_user_data.get("first_name") or ""
        current_last_name = current_user_data.get("last_name") or ""

        if body.first_name is not None:
            update_data["first_name"] = body.first_name
            metadata_update["first_name"] = body.first_name
            current_first_name = body.first_name

        if body.last_name is not None:
            update_data["last_name"] = body.last_name
            metadata_update["last_name"] = body.last_name
            current_last_name = body.last_name

        if body.first_name is not None or body.last_name is not None:
            # Compute full_name for Supabase metadata (not stored in organization_members)
            full_name = self._compute_full_name(current_first_name, current_last_name)
            if full_name:
                metadata_update["full_name"] = full_name

        if body.timezone is not None:
            update_data["timezone"] = body.timezone
            metadata_update["timezone"] = body.timezone

        if body.avatar_url is not None:
            update_data["avatar_url"] = body.avatar_url
            metadata_update["avatar_url"] = body.avatar_url

        if body.salutation is not None:
            update_data["salutation"] = body.salutation
            metadata_update["salutation"] = body.salutation

        metadata_update |= self._build_verification_metadata(body)

        return update_data, metadata_update

    @staticmethod
    def _compute_full_name(first_name: str, last_name: str) -> str:
        """Compute full name from first and last name.
        Args:
            first_name: First name
            last_name: Last name
        Returns:
            str: Full name
        """
        full_name_parts = [part.strip() for part in [first_name, last_name] if part.strip()]
        return " ".join(full_name_parts) if full_name_parts else ""

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

    async def _merge_metadata(
        self, user_id: str, metadata_update: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge incoming metadata with existing Supabase metadata."""
        existing_metadata: dict[str, Any] = {}
        user_data = await get_user_by_id(self.supabase_client, user_id)
        if user_data and getattr(user_data, "user", None):
            existing_metadata = user_data.user.user_metadata or {}

        return {**existing_metadata, **metadata_update}

    def _build_update_audit_data(
        self, profile: dict[str, Any], organization_id: str | None, user_id: str
    ) -> dict[str, Any]:
        """Build audit payload for profile update."""
        return {
            "user_id": str(user_id),
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "salutation": profile.get("salutation"),
            "full_name": (
                f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                or profile.get("full_name")
            ),
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
    ) -> UserProfileData:
        """Creates a UserProfileData object from user profile data.
        This is the single source of truth for creating user profile responses.

        Args:
            user_profile: User profile data from database
            role_info: Optional role information
            permissions: Optional list of permissions
            organization_details: Optional organization details

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
        # Get phone_number and phone_isd_code from user_profile
        phone_number = user_profile.get("phone_number", None)
        phone_isd_code = user_profile.get("phone_isd_code", None)

        # Compute full_name from first_name and last_name if not present
        full_name = user_profile.get("full_name")
        if not full_name:
            first_name = user_profile.get("first_name", "")
            last_name = user_profile.get("last_name", "")
            full_name = f"{first_name} {last_name}".strip()

        return UserProfileData(
            user_id=str(user_profile["user_id"]),
            email=user_profile["email"],
            full_name=full_name,
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
            verification_preference=verification_preference,
            organization_details=organization_details,
        )

    def _build_full_name(self, *parts: str) -> str:
        """Build a full name from parts.

        Args:
            *parts: Parts of the full name

        Returns:
            str: Full name
        """
        return " ".join(filter(None, parts))
