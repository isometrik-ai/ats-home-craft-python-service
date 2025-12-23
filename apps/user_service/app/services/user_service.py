"""Service for user business logic

This service handles all business logic related to users, including
validation, formatting, and orchestration of user operations.
"""

from datetime import datetime
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.organisation_member_repository import (
    OrganisationMemberRepository,
)
from apps.user_service.app.db.repositories.role_repository import RoleRepository
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.users import (
    PermissionInfo,
    RoleInfoWithDescription,
    UpdateUserProfileRequest,
    UserListItem,
)
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from apps.user_service.app.utils.user_utils import create_user_profile_data
from libs.shared_db.supabase_db.admin_operations.user import (
    ban_the_user,
    get_user_by_id,
    unban_the_user,
    update_metadata_of_user,
)
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    update_supabase_user_email,
)
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("user_service")


class UserService:
    """Service for user business logic.

    Handles all business logic related to users, including validation,
    formatting, and orchestration of user operations.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        """Initialize UserService with user context and database connection.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
        """
        self.user_context = user_context
        # Initialize repositories with database connection
        self.organisation_member_repository = OrganisationMemberRepository(
            db_connection=db_connection
        )
        self.role_repository = RoleRepository(db_connection=db_connection)

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
            logger.error("Invalid user_id provided: %s", user_id)
            return None

        # Get user from organization_members
        user_profile = await self.organisation_member_repository.get_user_profile_by_id(
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
            logger.error("Invalid user_id provided: %s", user_id)
            return []

        # Get user's role_id from organization_members
        role_id = await self.organisation_member_repository.get_user_role_id(
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
            "phone": user_data.get("phone"),
            "timezone": user_data.get("timezone", "UTC"),
            "role_id": user_data.get("role_id"),
            "status": user_data.get("status", "active"),
            "isometrik_user_id": user_data.get("isometrik_user_id"),
        }

        return await self.organisation_member_repository.add_member(
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
        return await self.organisation_member_repository.update_user_info(
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
        return await self.organisation_member_repository.delete_user(
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
        return await self.organisation_member_repository.check_user_exists(
            email=email, organization_id=organization_id
        )

    async def check_phone_exists_for_other_user(
        self, phone: str, organization_id: str, user_id: str | None = None
    ) -> bool:
        """Check if phone number exists for another user.

        Args:
            phone: Phone number
            organization_id: Organization ID
            user_id: Optional user ID to exclude from check

        Returns:
            bool: True if phone number exists for another user, False otherwise
        """
        return await self.organisation_member_repository.check_phone_exists_for_other_user(
            phone=phone, organization_id=organization_id, user_id=user_id
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
        users_data = await self.organisation_member_repository.get_users_details_list(
            organization_id=organization_id, search=search, limit=limit, offset=offset
        )

        # Get total count
        total_count = await self.organisation_member_repository.get_users_total_count(
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
        return await self.organisation_member_repository.get_users_total_count(
            organization_id=organization_id, search=search
        )

    async def update_user_activity(self, user_id: str, organization_id: str) -> None:
        """Update user's last active timestamp.

        Args:
            user_id: User ID
            organization_id: Organization ID
        """
        await self.organisation_member_repository.update_user_activity(
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
        return await self.organisation_member_repository.suspend_user(
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
        return await self.organisation_member_repository.revoke_suspended_user(
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
        current_user_data = await self.organisation_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not current_user_data:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Update in organization_members table
        result = await self.organisation_member_repository.update_user_email(
            user_id=user_id, organization_id=organization_id, new_email=new_email
        )

        if not result:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Also update in Supabase Auth
        await update_supabase_user_email(user_id, organization_id, new_email)

        return {"current_user_data": current_user_data}

    async def get_organization_member_status_by_email(self, email: str) -> str | None:
        """Get organization member status by email.

        Args:
            email: Email address

        Returns:
            str: Organization member status or None if not found
        """
        return await self.organisation_member_repository.get_organization_member_status_by_email(
            email=email
        )

    async def get_user_profile_with_metadata(
        self, user_id: str, organization_id: str | None = None
    ) -> dict[str, Any]:
        """Get complete user profile with metadata from Supabase Auth.

        This method handles all business logic for getting user profile:
        - Fetches user from organization_members
        - Fetches metadata from Supabase Auth
        - Merges and enriches the data
        - Gets user permissions
        - Updates user activity
        - Formats response data for API

        Args:
            user_id: User ID
            organization_id: Optional organization ID

        Returns:
            dict containing:
            - profile_data: UserProfileData formatted data
            - audit_data: Audit data for logging
        """
        # Get user profile from organization_members
        user_profile = await self.get_user_profile_by_id(user_id, organization_id)

        # Get user data from Supabase Auth
        user_data = await get_user_by_id(user_id)
        current_email = self.user_context.email
        current_phone = None
        user_metadata = {}

        if user_data and user_data.user:
            user_obj = user_data.user
            if hasattr(user_obj, "email_change") and user_obj.email_change:
                current_email = user_obj.email_change
            else:
                current_email = user_obj.email

            user_metadata = user_obj.user_metadata or {}

            if user_metadata and user_metadata.get("phone"):
                current_phone = user_metadata.get("phone")
            elif hasattr(user_obj, "phone") and user_obj.phone:
                current_phone = user_obj.phone
            elif hasattr(user_obj, "phone_change") and user_obj.phone_change:
                current_phone = user_obj.phone_change

        # If no profile in organization_members, create from metadata
        if not user_profile:
            first_name = user_metadata.get("first_name", "")
            last_name = user_metadata.get("last_name", "")
            full_name = user_metadata.get(
                "full_name",
                f"{first_name} {last_name}".strip() or current_email.split("@")[0],
            )
            avatar_url = user_metadata.get("avatar_url")
            phone = current_phone or user_metadata.get("phone")
            tzone = user_metadata.get("timezone", "UTC")
            salutation = user_metadata.get("salutation", None)
            user_profile = {
                "user_id": user_id,
                "email": current_email,
                "full_name": full_name,
                "first_name": first_name,
                "last_name": last_name,
                "avatar_url": avatar_url,
                "phone": phone,
                "timezone": tzone,
                "salutation": salutation,
                "role_id": None,
                "status": "active",
                "created_at": None,
                "updated_at": None,
                "last_active_at": None,
                "joined_at": None,
                "organization_id": None,
                "roles": None,
            }
        else:
            # Update email and phone from Supabase Auth if different
            if user_profile["email"].lower() != current_email.lower():
                user_profile["email"] = current_email

            profile_phone = user_profile.get("phone")
            if current_phone and profile_phone != current_phone:
                user_profile["phone"] = current_phone

        # Add verification preference
        verification_preference_data = user_metadata.get("verification_preference")
        if verification_preference_data and isinstance(verification_preference_data, dict):
            user_profile["verification_preference"] = verification_preference_data
        else:
            user_profile["verification_preference"] = None

        # Add identities
        identities_list = []
        if user_data and user_data.user and hasattr(user_data.user, "identities"):
            for identity in user_data.user.identities:
                identity_data = {
                    "provider": identity.provider,
                    "created_at": identity.created_at,
                    "updated_at": identity.updated_at,
                }
                if identity.provider != "email":
                    identity_data["provider_id"] = identity.identity_data.get(
                        "provider_id", identity.identity_data.get("sub", None)
                    )
                else:
                    identity_data["provider_id"] = identity.identity_data.get("email", None)
                identities_list.append(identity_data)

        if identities_list:
            user_profile["identities"] = identities_list

        # Get permissions and update activity if user is in organization
        permissions_data = []
        if organization_id:
            await self.update_user_activity(user_id, organization_id)
            permissions_data = await self.get_user_permissions(user_id, organization_id)

        # Add role description from roles if available
        if user_profile.get("roles") and isinstance(user_profile["roles"], dict):
            user_profile["role_description"] = user_profile["roles"].get("description", "")

        user_profile["permissions"] = permissions_data

        # Format role info for API response
        if user_profile.get("role_id") is None:
            role_info = RoleInfoWithDescription(
                role_id="",
                description="No organization assigned",
            )
        else:
            role_info = RoleInfoWithDescription(
                role_id=str(user_profile["role_id"]),
                description=user_profile.get("role_description", ""),
            )

        # Format permissions for API response
        permissions = [
            PermissionInfo(
                permission_id=str(p["id"]),
                permission_name=p["name"],
                permission_code=p["code"],
                category=p["category"],
            )
            for p in permissions_data
        ]

        # Create formatted profile data
        profile_data = create_user_profile_data(
            user_profile=user_profile,
            user_type=self.user_context.user_type or "organization_member",
            role_info=role_info,
            permissions=permissions,
        )

        # Prepare audit data
        audit_data = {
            "user_id": str(user_profile["user_id"]),
            "email": user_profile["email"],
            "full_name": user_profile["full_name"],
            "organization_id": str(user_profile.get("organization_id", "")),
            "role_id": str(user_profile.get("role_id", "")),
            "status": user_profile["status"],
            "permission_count": len(permissions),
            "access_timestamp": datetime.now().isoformat(),
        }

        return {
            "profile_data": profile_data,
            "audit_data": audit_data,
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
                phone=u.get("phone"),
                role_id=str(u["role_id"]) if u.get("role_id") else "",
                status=u.get("status", "active"),
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
            dict containing:
            - audit_data: Audit data for the ban operation
            - current_user_data: User data before ban (for audit old data)

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
        current_user_data = await self.organisation_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not current_user_data:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Ban user in Supabase Auth
        result = await ban_the_user(user_id)
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

        # Prepare audit data
        audit_data = {
            "user_id": str(current_user_data["user_id"]),
            "email": current_user_data["email"],
            "full_name": current_user_data.get("full_name", ""),
            "status": "suspended",
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
        current_user_data = await self.organisation_member_repository.get_user_profile_by_id(
            user_id=user_id, organization_id=organization_id
        )

        if not current_user_data:
            raise NotFoundException(
                message_key="users.errors.organization_user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Unban user in Supabase Auth
        result = await unban_the_user(user_id)
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

        # Prepare audit data
        audit_data = {
            "user_id": str(current_user_data["user_id"]),
            "email": current_user_data["email"],
            "full_name": current_user_data.get("full_name", ""),
            "status": "active",
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
            dict containing updated profile and audit data

        Raises:
            BadRequestException: If validation fails
        """
        # Get current user data
        current_user_data = None
        if organization_id:
            current_user_data = await self.organisation_member_repository.get_user_profile_by_id(
                user_id=user_id, organization_id=organization_id
            )

        # If user not in organization, get from Supabase Auth metadata
        if not current_user_data:
            user_metadata = {}
            user_data = await get_user_by_id(user_id)
            if user_data and hasattr(user_data, "user") and user_data.user:
                user_metadata = user_data.user.user_metadata or {}

            current_user_data = {
                "user_id": user_id,
                "email": self.user_context.email,
                "first_name": user_metadata.get("first_name", ""),
                "last_name": user_metadata.get("last_name", ""),
                "full_name": user_metadata.get("full_name", ""),
                "timezone": user_metadata.get("timezone", "UTC"),
                "avatar_url": user_metadata.get("avatar_url"),
                "organization_id": organization_id,
            }

        # Prepare update data
        update_data = {}
        metadata_update = {}

        # Get current values to calculate full_name
        current_first_name = current_user_data.get("first_name") or ""
        current_last_name = current_user_data.get("last_name") or ""

        # Update first_name if provided
        if body.first_name is not None:
            update_data["first_name"] = body.first_name
            metadata_update["first_name"] = body.first_name
            current_first_name = body.first_name

        # Update last_name if provided
        if body.last_name is not None:
            update_data["last_name"] = body.last_name
            metadata_update["last_name"] = body.last_name
            current_last_name = body.last_name

        # Calculate full_name from first_name + last_name
        if body.first_name is not None or body.last_name is not None:
            full_name_parts = [
                part.strip() for part in [current_first_name, current_last_name] if part.strip()
            ]
            full_name = " ".join(full_name_parts) if full_name_parts else ""
            if full_name:
                update_data["full_name"] = full_name
                metadata_update["full_name"] = full_name

        # Update timezone if provided
        if body.timezone is not None:
            update_data["timezone"] = body.timezone
            metadata_update["timezone"] = body.timezone

        # Update avatar_url if provided
        if body.avatar_url is not None:
            update_data["avatar_url"] = body.avatar_url
            metadata_update["avatar_url"] = body.avatar_url

        # Update salutation if provided
        if body.salutation is not None:
            update_data["salutation"] = body.salutation
            metadata_update["salutation"] = body.salutation

        # Handle verification preference
        if body.two_fa_enabled is not None:
            verification_method = body.verification_method.upper()
            if verification_method not in ["PHONE", "EMAIL"]:
                raise BadRequestException(
                    message_key="users.errors.invalid_verification_method",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            verification_preference = {
                "enabled": body.two_fa_enabled,
                "type": verification_method,
            }
            metadata_update["verification_preference"] = verification_preference
        elif body.verification_method and body.verification_method.upper() != "EMAIL":
            raise BadRequestException(
                message_key="users.errors.two_fa_enabled_required",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Validate at least one field is provided
        if not update_data and not metadata_update:
            raise BadRequestException(
                message_key="users.errors.no_fields_provided_for_update",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Update organization_members table if user is in an organization
        if organization_id:
            await self.update_user_info(user_id, organization_id, update_data)

        # Update Supabase Auth user_metadata if we have metadata to update
        if metadata_update:
            existing_metadata = {}
            user_data = await get_user_by_id(user_id)
            if user_data and hasattr(user_data, "user") and user_data.user:
                existing_metadata = user_data.user.user_metadata or {}

            updated_metadata = {**existing_metadata, **metadata_update}
            await update_metadata_of_user(user_id, updated_metadata)

        # Get updated user profile
        updated_profile = await self.get_user_profile_by_id(user_id, organization_id)

        # Return audit data
        return {
            "updated_profile": updated_profile,
            "audit_data": {
                "user_id": str(user_id),
                "first_name": updated_profile.get("first_name")
                if updated_profile
                else current_user_data.get("first_name"),
                "last_name": updated_profile.get("last_name")
                if updated_profile
                else current_user_data.get("last_name"),
                "salutation": updated_profile.get("salutation")
                if updated_profile
                else current_user_data.get("salutation"),
                "full_name": updated_profile.get("full_name")
                if updated_profile
                else current_user_data.get("full_name"),
                "timezone": updated_profile.get("timezone")
                if updated_profile
                else current_user_data.get("timezone"),
                "avatar_url": updated_profile.get("avatar_url")
                if updated_profile
                else current_user_data.get("avatar_url"),
                "organization_id": str(organization_id) if organization_id else None,
                "updated_by_user_id": self.user_context.user_id,
                "updated_by_email": self.user_context.email,
                "update_timestamp": datetime.now().isoformat(),
            },
            "current_user_data": current_user_data,
        }
