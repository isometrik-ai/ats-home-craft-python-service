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
    extract_user_context,
    check_permissions
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
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE, USER_NOT_FOUND_MESSAGE

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
    get_user_permissions,
    update_user_activity
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

    print("user_context______", user_context)
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
        if not user_profile:
            # User is not linked to any organization, create a basic profile from JWT token
            logger.info("User not linked to any organization - Request ID: %s, ",request_id)
            logger.info(
                "User ID: %s, Email: %s - Creating basic profile",
                user_context.user_id, user_context.email
            )
            
            # Get user metadata from JWT token
            user_data = await get_user_by_id(user_context.user_id)
            user_metadata = user_data.user.user_metadata if user_data and user_data.user else {}

            # Extract fields from user metadata
            first_name = user_metadata.get("first_name", "")
            last_name = user_metadata.get("last_name", "")
            full_name = user_metadata.get("full_name", f"{first_name} {last_name}".strip() or user_context.email.split('@')[0])
            avatar_url = user_metadata.get("avatar_url")
            phone = user_metadata.get("phone")
            timezone = user_metadata.get("timezone", "UTC")
            
            # Create a basic profile for users without organization membership
            user_profile = {
                "user_id": user_context.user_id,
                "email": user_context.email,
                "full_name": full_name,
                "first_name": first_name,
                "last_name": last_name,
                "avatar_url": avatar_url,
                "phone": phone,
                "timezone": timezone,
                "role_id": None,
                "status": "active",
                "created_at": None,
                "updated_at": None,
                "last_active_at": None,
                "joined_at": None,
                "organization_id": None,
                "roles": None
            }
            print("Created basic profile for user without organization:", user_profile)

        if user_profile["email"].lower() != user_context.email.lower():
            logger.warning("Token email does not match user profile - Request ID: %s, ",request_id)
            logger.warning(
                "Token email: %s, Profile email: %s",
                user_context.email,user_profile['email']
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token email does not match user profile",
            )

        return user_profile

    user_profile = await _fetch_org_member_profile()

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

# @router.get(
#     "/{user_id}", response_model=UserProfileResponse, status_code=status.HTTP_200_OK
# )
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",  # Accessing other user profile data involves personal information
#         "pii",  # User profile contains personally identifiable information
#         "audit_required",  # Profile access must be logged for compliance and security audits
#     ],
#     table_name="organization_members",
#     category="USER_PROFILE",
# )
async def get_user_by_id(
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Get a user's profile by user_id (async, sequential)
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    user_context = await check_permissions(
        current_user, SETTINGS_USERS_MANAGE,"access user profiles")

    # Only organization members can access other user profiles
    if user_context.user_type != "organization_member":
        logger.warning(
            "Non-organization member trying to access user profile - Request ID: %s, ",
            request_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization members can access user profiles",
        )

    # Set audit context for user profile access
    # This endpoint only handles organization members, so audit table is always organization_members
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin accessed user profile: {user_id}"
    request.state.audit_risk_level = "medium"

    user_profile = await get_user_profile_by_id(
        user_id, user_context.organization_id
    )

    if not user_profile:
        logger.warning("User not found in organization - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=404,
            detail=USER_NOT_FOUND_MESSAGE,
        )

    permissions_data = await get_user_permissions(
        user_id, user_context.organization_id
    )

    permissions = [
        PermissionInfo(
            permission_id=str(p.permission_id),
            permission_name=p.permission_name,
            permission_code=p.permission_code,
            category=p.category,
        )
        for p in permissions_data
    ]

    role_info = RoleInfoWithDescription(
        role_id=str(user_profile["role_id"]),
        role_name=user_profile["role_name"],
        description=user_profile.get("role_description", ""),
    )

    # Set audit data for user profile access
    request.state.raw_audit_new_data = {
        "target_user_id": str(user_profile["user_id"]),
        "target_email": user_profile["email"],
        "target_full_name": user_profile["full_name"],
        "organization_id": str(user_profile["organization_id"]),
        "role_id": str(user_profile["role_id"]),
        "role_name": user_profile["role_name"],
        "status": user_profile["status"],
        "permission_count": len(permissions),
        "accessed_by_user_id": user_context.user_id,
        "accessed_by_email": user_context.email,
        "access_timestamp": datetime.now().isoformat(),
    }

    profile_data = create_user_profile_data(
        user_profile=user_profile,
        user_type="organization_member",  # This endpoint only handles organization members
        role_info=role_info,
        permissions=permissions
    )


    return UserProfileResponse(
        message="User profile retrieved successfully",
        data=profile_data,
    )
