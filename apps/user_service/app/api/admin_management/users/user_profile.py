"""
User Profile API Module
This module provides user profile operations including getting own profile and getting user by ID.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    # require_permission,
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
router = APIRouter(prefix="", tags=["User Profile"])

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

    user_context = extract_user_context(current_user)

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
            logger.warning("User profile not found - Request ID: %s, ",request_id)
            logger.warning(
                "User ID: %s, Organization ID: %s",
                user_context.user_id,user_context.organization_id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found or access denied to organization",
            )

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

    # Handle different user types
    if user_context.user_type == "organization_member":
        # Original flow for organization members
        user_profile = await _fetch_org_member_profile()

        async def _fetch_permissions() -> list:
            """Fetch user permissions and update activity."""

            permissions_data = await get_user_permissions(
                user_context.user_id,
                user_context.organization_id
            )
            await update_user_activity(
                user_context.user_id,
                user_context.organization_id
            )
            return permissions_data

        permissions_data = await _fetch_permissions()

        def _format_org_member_data(user_profile: dict, permissions_data: list) -> UserProfileData:
            """Format organization member profile data."""
            role_info = RoleInfoWithDescription(
                role_id=str(user_profile["role_id"]),
                role_name=user_profile["role_name"],
                description=user_profile.get("role_description", ""),
            )
            permissions = [
                PermissionInfo(
                    permission_id=str(p["permission_id"]),
                    permission_name=p["permission_name"],
                    permission_code=p["permission_code"],
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
                "organization_id": str(user_profile["organization_id"]),
                "role_id": str(user_profile["role_id"]),
                "role_name": user_profile["role_name"],
                "status": user_profile["status"],
                "permission_count": len(permissions),
                "access_timestamp": datetime.now().isoformat(),
            }

            return create_user_profile_data(
                user_profile=user_profile,
                user_type=user_context.user_type,
                role_info=role_info,
                permissions=permissions
            )

        profile_data = _format_org_member_data(user_profile, permissions_data)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user type",
        )

    # User type validation is now handled in extract_user_context function
    # This else block should never be reached due to validation in common_utils


    return UserProfileResponse(
        # status_code=status.HTTP_200_OK,
        message="User profile retrieved successfully",
        data=profile_data,
    )

@router.get(
    "/{user_id}", response_model=UserProfileResponse, status_code=status.HTTP_200_OK
)
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

    # user_context = extract_user_context(current_user)
    user_context = await check_permissions(
        current_user, "settings.users.manage","access user profiles")

    # Only organization members can access other user profiles
    if user_context.user_type != "organization_member":
        logger.warning(
            "Non-organization member trying to access user profile - Request ID: %s, ",
            request_id
        )
        logger.warning("User Type: %s, Target User ID: %s",user_context.user_id,user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization members can access user profiles",
        )

    # await require_permission(
    #     permission_code="settings.users.manage",
    #     user_context=user_context,
    #     action_description="access user profiles",
    # )

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
        logger.warning(
            "Target User ID: %s, Organization ID: %s",
            user_id,user_context.organization_id
        )
        raise HTTPException(
            status_code=404,
            detail="User not found in organization",
        )

    permissions_data = await get_user_permissions(
        user_id, user_context.organization_id
    )

    print("permission_id")
    print(permissions_data)
    permissions = [
        PermissionInfo(
            permission_id=str(p.permission_id),
            permission_name=p.permission_name,
            permission_code=p.permission_code,
            category=p.category,
        )
        for p in permissions_data
    ]
    # permissions = [
    #     PermissionInfo(
    #         permission_code=str(p["permission_id"]),
    #         permission_name=p["permission_name"],
    #         permission_code=p["permission_code"],
    #         category=p["category"],
    #     )
    #     for p in permissions_data
    # ]
    print("permission_id")

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
        # status_code=200,
        message="User profile retrieved successfully",
        data=profile_data,
    )
