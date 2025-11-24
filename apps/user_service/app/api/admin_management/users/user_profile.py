"""
User Profile API Module
This module provides user profile operations including getting own profile and getting user by ID.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context
)
from apps.user_service.app.dependencies.user_utils import (
    create_user_profile_data,
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserProfileResponse,
    UserProfileData,
    PermissionInfo,
    RoleInfoWithDescription
)

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
    get_user_permissions,
    update_user_activity
)

from libs.shared_db.supabase_db.admin_operations.user import (
    get_user_by_id,
)

# # Audit logging imports
# from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
#     audit_api_call,
# )

# Create router for user profile endpoints
router = APIRouter(prefix="/users", tags=["User Profile"])

# Initialize logger for user profile module
logger = get_logger("user-profile-api")


@router.get(
    "/profile",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",  # Accessing user profile data involves personal information
#         "pii",  # User profile contains personally identifiable information
#         "audit_required",  # Profile access must be logged for compliance and security audits
#     ],
#     table_name="organization_members",
#     category="USER_PROFILE",
# )
async def get_user_profile(
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Retrieve the authenticated user's profile (optimized + async).
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    user_context = await extract_user_context(current_user)

    # Set audit context for profile access
    request.state.audit_table = "organization_members"
    request.state.audit_description = (
        f"User accessed their own profile: {user_context.email}"
    )
    request.state.audit_risk_level = "low"

    async def _fetch_org_member_profile() -> dict:
        """Fetch and validate organization member profile."""
        user_profile = await get_user_profile_by_id(
            user_context.user_id,
            user_context.organization_id
        )
        
        # Get current email and phone from Supabase auth (source of truth)
        # JWT token email/phone might be stale if updated after token was issued
        current_email = user_context.email  # Default to JWT token email
        current_phone = None  # Will be set from Supabase auth
        user_metadata = {}
        try:
            user_data = await get_user_by_id(user_context.user_id)
            if user_data and user_data.user:
                user_obj = user_data.user
                # Check if there's a pending email change
                # If email_change exists and is confirmed, use it; otherwise use current email
                if hasattr(user_obj, 'email_change') and user_obj.email_change:
                    # There's a pending email change - use it as the current email
                    # This handles cases where email was updated but not yet confirmed
                    current_email = user_obj.email_change
                    logger.info(
                        "Using pending email change '%s' for user %s (current email: '%s')",
                        current_email, user_context.user_id, user_obj.email
                    )
                else:
                    # No pending change, use the current email
                    current_email = user_obj.email
                
                # Get current phone from Supabase auth
                # Priority: user_metadata (raw_user_meta_data) > phone field > phone_change
                # We prioritize user_metadata since that's what we explicitly update
                user_metadata = user_obj.user_metadata or {}
                
                # First check user_metadata (raw_user_meta_data) - this is what we update
                if user_metadata and user_metadata.get("phone"):
                    current_phone = user_metadata.get("phone")
                # Then check phone field
                elif hasattr(user_obj, 'phone') and user_obj.phone:
                    current_phone = user_obj.phone
                # Finally check for pending phone change (similar to email_change)
                elif hasattr(user_obj, 'phone_change') and user_obj.phone_change:
                    current_phone = user_obj.phone_change
        except Exception as user_fetch_error:
            # If admin API fails, fall back to JWT token email and metadata
            logger.warning("Could not fetch user data from admin API: %s, using JWT token data", str(user_fetch_error))
            user_metadata = current_user.get("user_metadata", {})
            # Try to get phone from JWT token metadata as fallback
            if not current_phone:
                current_phone = user_metadata.get("phone")
        
        if not user_profile:
            # User is not linked to any organization, create a basic profile
            logger.info("User not linked to any organization - Request ID: %s, ",request_id)
            logger.info(
                "User ID: %s, Email: %s - Creating basic profile",
                user_context.user_id, current_email
            )

            # Extract fields from user metadata
            first_name = user_metadata.get("first_name", "")
            last_name = user_metadata.get("last_name", "")
            full_name = user_metadata.get("full_name",
                f"{first_name} {last_name}".strip() or current_email.split('@')[0]
            )
            avatar_url = user_metadata.get("avatar_url")
            # Use current_phone from Supabase auth if available, otherwise fall back to metadata
            phone = current_phone or user_metadata.get("phone")
            tzone = user_metadata.get("timezone", "UTC")

            # Create a basic profile for users without organization membership
            user_profile = {
                "user_id": user_context.user_id,
                "email": current_email,  # Use current email from Supabase auth
                "full_name": full_name,
                "first_name": first_name,
                "last_name": last_name,
                "avatar_url": avatar_url,
                "phone": phone,  # Use current phone from Supabase auth
                "timezone": tzone,
                "role_id": None,
                "status": "active",
                "created_at": None,
                "updated_at": None,
                "last_active_at": None,
                "joined_at": None,
                "organization_id": None,
                "roles": None
            }
        else:
            # User has organization membership - update email and phone to current values from Supabase auth
            # This ensures the profile shows the latest email/phone even if organization_members table is stale
            if user_profile["email"].lower() != current_email.lower():
                logger.info(
                    "Updating profile email from '%s' to current email '%s' (from Supabase auth)",
                    user_profile["email"], current_email
                )
                user_profile["email"] = current_email
            
            # Update phone if it differs from current phone in Supabase auth
            profile_phone = user_profile.get("phone")
            if current_phone and profile_phone != current_phone:
                user_profile["phone"] = current_phone

        return user_profile

    user_profile = await _fetch_org_member_profile()

    async def _fetch_user_identities() -> list:
        """Fetch user identities."""
        identities_list = []
        try:
            user_data = await get_user_by_id(user_context.user_id)
            if user_data and user_data.user and hasattr(user_data.user, 'identities'):
                for identity in user_data.user.identities:
                    identity_data = {
                        "provider": identity.provider,
                        "created_at": identity.created_at,
                        "updated_at": identity.updated_at
                    }
                    if identity.provider != "email":
                        identity_data["provider_id"] = identity.identity_data.get("provider_id",identity.identity_data.get("sub",None))
                    else:
                        identity_data["provider_id"] = identity.identity_data.get("email",None)
                    identities_list.append(identity_data)
        except Exception as identity_error:
            # If we can't fetch identities from admin API, create a basic identity from JWT token
            logger.warning("Could not fetch user identities from admin API: %s, creating from JWT token", str(identity_error))
            # Create basic identity from JWT token
            # Use current time for created_at and updated_at since we don't have the actual timestamps
            current_time = datetime.now(timezone.utc)
            identities_list = [{
                "provider": "email",
                "provider_id": user_context.email,
                "created_at": current_time,
                "updated_at": current_time
            }]
        return identities_list

    identities_data = await _fetch_user_identities()
    user_profile.update({
        "identities": identities_data
    })

    async def _fetch_permissions() -> list:
        """Fetch user permissions and update activity."""

        # If user has no organization, return empty permissions
        if not user_context.organization_id:
            logger.info("User has no organization, returning empty permissions")
            return []

        # When organization exists, update activity and fetch permissions
        await update_user_activity(
            user_context.user_id,
            user_context.organization_id
        )
        return await get_user_permissions(
            user_context.user_id,
            user_context.organization_id
        )

    permissions_data = await _fetch_permissions()

    def _format_org_member_data(user_profile: dict, permissions_data: list) -> UserProfileData:
        """Format organization member profile data."""
        # Handle users without organization
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

        permissions = [
            PermissionInfo(
                permission_id=str(p["id"]),
                permission_name=p["name"],
                permission_code=p["code"],
                category=p["category"],
            )
            for p in permissions_data
        ]
        # Timestamps are now handled by create_user_profile_data

        # Set audit data for profile access
        request.state.raw_audit_new_data = {
            "user_id": str(user_profile["user_id"]),
            "email": user_profile["email"],
            "full_name": user_profile["full_name"],
            "organization_id": str(user_profile.get("organization_id", "")),
            "role_id": str(user_profile.get("role_id", "")),
            # "role_name": user_profile["role_name"],
            "status": user_profile["status"],
            "permission_count": len(permissions),
            "access_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return create_user_profile_data(
            user_profile=user_profile,
            user_type=user_context.user_type,
            role_info=role_info,
            permissions=permissions
        )

    profile_data = _format_org_member_data(user_profile, permissions_data)

    # User type validation is now handled in extract_user_context function
    # This else block should never be reached due to validation in common_utils

    return UserProfileResponse(
        message="User profile retrieved successfully",
        data=profile_data,
    )
