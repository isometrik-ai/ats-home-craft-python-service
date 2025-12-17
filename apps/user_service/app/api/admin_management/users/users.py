"""Users Management API Module
This module provides CRUD operations for user management.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.users import (
    PermissionInfo,
    RoleInfoWithDescription,
    UpdateUserEmailRequest,
    UpdateUserProfileRequest,
    UserListResponse,
)
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    get_user_in_organization,
    handle_api_exceptions,
    set_audit_old_data_from_user,
)
from apps.user_service.app.utils.user_utils import create_user_profile_data
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_permissions,
    get_user_profile_by_id,
    get_users_details_list,
    get_users_total_count,
    revoke_suspended_user,
    suspend_user,
    transform_users,
    update_user_activity,
    update_user_info,
)
from libs.shared_db.supabase_db.admin_operations.user import (
    ban_the_user,
    get_user_by_id,
    unban_the_user,
    update_metadata_of_user,
)
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    update_supabase_user_email,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/users", tags=["Users Management"])

logger = get_logger("users-api")


@handle_api_exceptions("get users list")
@router.get(
    "/list",
    response_model=UserListResponse,
    status_code=http_status.HTTP_200_OK,
    description="Get users list",
    summary="Get users list",
    responses={
        http_status.HTTP_200_OK: {"description": "Users list retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_users_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(
        None, description="Search term to filter Users by name (case-insensitive)"
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """List all users in the current organization (paginated, sequential)"""
    # Check permissions
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Get users list
    users_data = await get_users_details_list(
        organization_id=user_context.organization_id,
        search=search,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    if not users_data:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    # Get total count
    total_count = await get_users_total_count(
        organization_id=user_context.organization_id, search=search
    )

    users = await transform_users(users_data, user_context.organization_id)

    return list_response(
        request=request,
        items=users,
        total=total_count,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        page=page,
        page_size=page_size,
    )


@handle_api_exceptions("get user profile")
@router.get(
    "/profile",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get user profile",
    summary="Get user profile",
    responses={
        http_status.HTTP_200_OK: {"description": "User profile retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_user_profile(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """Retrieve the authenticated user's profile (async)"""
    # pylint: disable=too-many-branches, too-complex
    user_context = await extract_user_context(current_user)

    user_profile = await get_user_profile_by_id(user_context.user_id, user_context.organization_id)

    current_email = user_context.email
    current_phone = None
    user_metadata = {}
    user_data = await get_user_by_id(user_context.user_id)
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
            "user_id": user_context.user_id,
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
        if user_profile["email"].lower() != current_email.lower():
            user_profile["email"] = current_email

        profile_phone = user_profile.get("phone")
        if current_phone and profile_phone != current_phone:
            user_profile["phone"] = current_phone

    verification_preference_data = user_metadata.get("verification_preference")
    if verification_preference_data and isinstance(verification_preference_data, dict):
        user_profile["verification_preference"] = verification_preference_data
    else:
        user_profile["verification_preference"] = None

    identities_list = []
    user_data = await get_user_by_id(user_context.user_id)
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
            user_profile.update({"identities": identities_list})

    if not user_context.organization_id:
        return success_response(
            request=request,
            message_key="users.success.user_profile_retrieved",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
            data=[],
        )

    await update_user_activity(user_context.user_id, user_context.organization_id)
    permissions_data = await get_user_permissions(
        user_context.user_id, user_context.organization_id
    )

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

    profile_data = create_user_profile_data(
        user_profile=user_profile,
        user_type=user_context.user_type,
        role_info=role_info,
        permissions=permissions,
    )
    return success_response(
        request=request,
        message_key="users.success.user_profile_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=profile_data,
    )


@router.put(
    "/{user_id}/email",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Update user email",
    summary="Update user email",
    responses={
        http_status.HTTP_200_OK: {"description": "User email updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Updating user email involves personal information
        "pii",  # Email updates contain personally identifiable information
        "audit_required",  # Email updates must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_EMAIL_UPDATE",
)
@handle_api_exceptions("update user email")
async def update_user_email(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateUserEmailRequest = Body(...),
    user_id: str = Path(..., description="The ID of the user to update"),
):
    """Update user email"""
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Get current user data for audit before email update
    current_user_data = await get_user_in_organization(user_id, user_context.organization_id)

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    await update_supabase_user_email(user_id, user_context.organization_id, body.email)

    return success_response(
        request=request,
        message_key="users.success.email_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@router.post(
    "/ban/{user_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Ban user",
    summary="Ban user",
    responses={
        http_status.HTTP_200_OK: {"description": "User banned successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")  # Example: Limit to 5 requests per minute
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Banning user involves personal information
        "pii",  # User banning contains personally identifiable information
        "audit_required",  # User banning must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_BAN",
)
@handle_api_exceptions("ban user")
async def ban_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    user_id: str = Path(..., description="The ID of the user to ban"),
):
    """Ban a user for a specified duration."""
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Set audit context for user banning
    request.state.audit_risk_level = "high"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin banned user: {user_id}"

    if user_id == user_context.user_id:
        raise BadRequestException(
            message_key="users.errors.self_action",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Get current user data for audit before banning
    current_user_data = await get_user_in_organization(user_id, user_context.organization_id)

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    # Ban user using database operations
    result = await ban_the_user(user_id)

    if not result:
        raise NotFoundException(
            message_key="users.errors.user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    result = await suspend_user(user_id, user_context.organization_id)
    if not result:
        raise NotFoundException(
            message_key="users.errors.organization_user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
        "full_name": current_user_data["full_name"],
        "status": "suspended",
        "organization_id": str(current_user_data["organization_id"]),
        # "banned_until": banned_until.isoformat(),
        "banned_by_user_id": user_context.user_id,
        "banned_by_email": user_context.email,
        "ban_timestamp": datetime.now().isoformat(),
        "ban_reason": "Admin ban action",
    }

    return success_response(
        request=request,
        message_key="users.success.user_banned",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@router.post(
    "/unban/{user_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Unban user",
    summary="Unban user",
    responses={
        http_status.HTTP_200_OK: {"description": "User unbanned successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")  # Example: Limit to 5 requests per minute
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Unbanning user involves personal information
        "pii",  # User unbanning contains personally identifiable information
        "audit_required",  # User unbanning must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_UNBAN",
)
@handle_api_exceptions("unban user")
async def unban_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    user_id: str = Path(..., description="The ID of the user to unban"),
):
    """Unban a user by user ID."""
    # Extract and validate user context from JWT token
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Set audit context for user unbanning
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin unbanned user: {user_id}"
    request.state.audit_risk_level = "medium"

    if user_id == user_context.user_id:
        raise BadRequestException(
            message_key="users.errors.self_action",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Get current user data for audit before unbanning
    current_user_data = await get_user_in_organization(user_id, user_context.organization_id)

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    # Unban user using database operations
    result = await unban_the_user(user_id)

    if not result:
        raise NotFoundException(
            message_key="users.errors.user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    result = await revoke_suspended_user(user_id, user_context.organization_id)

    if not result:
        raise NotFoundException(
            message_key="users.errors.organization_user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
        "full_name": current_user_data["full_name"],
        "status": "active",
        "organization_id": str(current_user_data["organization_id"]),
        "unbanned_by_user_id": user_context.user_id,
        "unbanned_by_email": user_context.email,
        "unban_timestamp": datetime.now().isoformat(),
        "ban_removed": True,
    }

    return success_response(
        request=request,
        message_key="users.success.user_unbanned",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@router.put(
    "/update",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Update user profile",
    summary="Update user profile",
    responses={
        http_status.HTTP_200_OK: {"description": "User profile updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Updating user profile involves personal information
        "pii",  # User profile updates contain personally identifiable information
        "audit_required",  # User updates must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_UPDATE",
)
@handle_api_exceptions("update user profile")
async def update_user_profile(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateUserProfileRequest = Body(...),
):
    """Update authenticated user's own profile information."""
    # pylint: disable=too-complex, too-many-branches
    user_context = await extract_user_context(current_user)
    user_id = user_context.user_id

    # Set audit context
    request.state.audit_risk_level = "low"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"User updating their own profile: {user_id}"

    # Get current user data for audit
    # If user has organization_id, get from organization, otherwise allow update without org
    current_user_data = None
    if user_context.organization_id:
        current_user_data = await get_user_in_organization(user_id, user_context.organization_id)

    # If user not in organization, create a basic profile structure
    if not current_user_data:
        # Allow users without organization to update their profile
        # Get metadata from JWT token or Supabase Auth
        user_metadata = current_user.get("user_metadata", {})
        user_data = await get_user_by_id(user_id)
        if user_data and hasattr(user_data, "user") and user_data.user:
            user_metadata = user_data.user.user_metadata or {}

        current_user_data = {
            "user_id": user_id,
            "email": user_context.email,
            "first_name": user_metadata.get("first_name", ""),
            "last_name": user_metadata.get("last_name", ""),
            "full_name": user_metadata.get("full_name", ""),
            "timezone": user_metadata.get("timezone", "UTC"),
            "avatar_url": user_metadata.get("avatar_url"),
            "organization_id": user_context.organization_id,
        }

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    # Prepare update data - only include fields that are provided
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

    if not update_data and not metadata_update:
        raise BadRequestException(
            message_key="users.errors.no_fields_provided_for_update",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Update organization_members table if user is in an organization
    if user_context.organization_id:
        await update_user_info(user_id, user_context.organization_id, update_data)

    # Update Supabase Auth user_metadata if we have metadata to update
    if metadata_update:
        existing_metadata = {}
        user_data = await get_user_by_id(user_id)
        if user_data and hasattr(user_data, "user") and user_data.user:
            existing_metadata = user_data.user.user_metadata or {}

        updated_metadata = {**existing_metadata, **metadata_update}

        await update_metadata_of_user(user_id, updated_metadata)

    # Get updated user profile for response
    updated_profile = await get_user_profile_by_id(user_id, user_context.organization_id)

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
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
        "organization_id": str(user_context.organization_id)
        if user_context.organization_id
        else None,
        "updated_by_user_id": user_context.user_id,
        "updated_by_email": user_context.email,
        "update_timestamp": datetime.now().isoformat(),
    }

    return success_response(
        request=request,
        message_key="users.success.user_profile_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )
