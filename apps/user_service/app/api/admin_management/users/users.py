"""
Users Management API Module
This module provides CRUD operations for user management.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request, Body

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.schemas.admin_access_management import UserQueryParams
from apps.user_service.app.app_instance import limiter

from apps.user_service.app.dependencies.common_utils import (
    validate_pagination_params,
    get_user_in_organization,
    set_audit_old_data_from_user,
    check_permissions,
    handle_api_exceptions,
)
from apps.user_service.app.dependencies.user_utils import (
    create_user_profile_data,
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserResponse,
    CreateUserRequest,
    UpdateUserRequest,
    UpdateUserResponse,
    UserListResponse,
)

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_db.supabase_db.admin_operations.user import delete_auth_user
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    invite_user_with_email
)
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE, USER_NOT_FOUND_MESSAGE

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_users_details_list,
    get_users_total_count,
    get_user_profile_by_id,
    get_user_permissions,
    create_new_user,
    update_user_info,
    delete_user,
    check_user_exists,
    check_phone_exists_for_other_user,
    transform_users
)

# Create router for users endpoints
router = APIRouter(prefix="/users", tags=["Users Management"])

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"

# Initialize logger for users module
logger = get_logger("users-api")


@router.get("/list", response_model=UserListResponse, status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",  # Accessing user list data involves personal information
#         "pii",  # User list contains personally identifiable information
#         "audit_required",  # User list access must be logged for compliance and security audits
#     ],
#     table_name="organization_members",
#     category="USER_LIST",
# )
async def get_users_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: UserQueryParams = Depends()
):
    """
    List all users in the current organization (async, paginated, sequential)
    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    # Set audit context for user list access
    request.state.audit_table = "organization_members"
    request.state.audit_description = (
        f"Admin accessed user list with search: '{query_params.search or 'none'}'"
    )
    request.state.audit_risk_level = "medium"

    # Validate pagination params and calculate offset
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )

    # Permission check
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Get users list using database operations
    users_data = await get_users_details_list(
        organization_id=user_context.organization_id,
        search=query_params.search,
        limit=page_size,
        offset=offset
    )

    # Get total count using database operations
    total_count = await get_users_total_count(
        organization_id=user_context.organization_id,
        search=query_params.search
    )

    users = await transform_users(users_data, user_context.organization_id)

    # Set audit data for user list access
    request.state.raw_audit_new_data = {
        "organization_id": str(user_context.organization_id),
        "accessed_by_user_id": user_context.user_id,
        "accessed_by_email": user_context.email,
        "search_term": query_params.search or "none",
        "page": page,
        "page_size": page_size,
        "total_users_retrieved": len(users),
        "total_count": total_count,
        "access_timestamp": datetime.now().isoformat(),
        "user_ids_accessed": [user.user_id for user in users] if users else [],
    }

    return UserListResponse(
        message="Users retrieved successfully",
        data=users,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Creating user involves personal information
        "pii",  # User creation contains personally identifiable information
        "audit_required",  # User creation must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_CREATION",
)
async def create_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: CreateUserRequest = Body(...)
):
    """
    Create a new user in the organization (invite/add, assign role)
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for user creation
    request.state.audit_table = "organization_members"
    request.state.audit_description = (
        f"Admin created new user: {body.email} with role_id: {body.role_id}"
    )
    request.state.audit_risk_level = "medium"

    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Check if user already exists in the organization
    exists = await check_user_exists(body.email, user_context.organization_id)

    if exists:
        logger.warning(
            "User already exists in organization - Request ID: %s, ",request_id)
        logger.warning(
            "Email: %s, Organization ID: %s",body.email,user_context.organization_id
        )
        raise HTTPException(
            status_code=409,
            detail="User already exists in organization",
        )

    # Create user using database operations
    user_data = {
        "organization_id": user_context.organization_id,
        "role_id": body.role_id,
        "email": body.email,
        "full_name": body.full_name,
        "phone": body.phone,
        "timezone": body.timezone or "UTC",
        "status": "invited"
    }

    result = await create_new_user(user_data)
    new_user_id = str(result["user_id"]) if result else "unknown"

    # Set audit data for user creation
    request.state.raw_audit_new_data = {
        "new_user_id": new_user_id,
        "email": body.email,
        "full_name": body.full_name,
        "phone": body.phone,
        "timezone": body.timezone or "UTC",
        "role_id": str(body.role_id),
        "organization_id": str(user_context.organization_id),
        "status": "invited",
        "created_by_user_id": user_context.user_id,
        "created_by_email": user_context.email,
        "creation_timestamp": datetime.now().isoformat(),
    }


    return UserResponse(
        message="User created and invited successfully",
        status="success",
    )


@router.put(
    "/update/{user_id}",
    response_model=UpdateUserResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "audit_required",
    ],
    table_name="organization_members",
    category="USER_UPDATE",
)
async def update_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateUserRequest = Body(...)
):
    """Update Users data by User id."""
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())


    # Set audit context for user update
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin updating user: {user_id}"
    request.state.audit_risk_level = "medium"

    user_ctx = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    if body.phone:
        duplicate = await check_phone_exists_for_other_user(
            body.phone, user_ctx.organization_id, user_id
        )
        if duplicate:
            logger.warning("Phone number already exists for other user - Request ID: %s",request_id)
            logger.warning("Phone: %s, Organization ID: %s",body.phone,user_ctx.organization_id)
            raise HTTPException(
                status_code=400,
                detail="Phone number already exists for another user in the organization",
            )

    # Get current user data for audit comparison
    current_user_data = await get_user_in_organization(
        user_id, user_ctx.organization_id
    )

    # Set old values for audit comparison
    request.state.raw_audit_old_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
        "full_name": current_user_data["full_name"],
        "first_name": current_user_data["first_name"],
        "last_name": current_user_data["last_name"],
        "phone": current_user_data["phone"],
        "timezone": current_user_data["timezone"],
        "avatar_url": current_user_data["avatar_url"],
        "status": current_user_data["status"],
        "role_id": str(current_user_data["role_id"]),
        "organization_id": str(current_user_data["organization_id"]),
    }

    # Update user using database operations
    update_data = {
        "full_name": body.full_name,
        "phone": body.phone,
        "timezone": body.timezone,
        "avatar_url": body.avatar_url,
        "status": body.status,
        "role_id": body.role_id
    }

    result = await update_user_info(user_id, user_ctx.organization_id, update_data)
    if not result:
        logger.warning("User not found in organization - Request ID: %s, ",request_id)
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

    user_profile = await get_user_profile_by_id(user_id, user_ctx.organization_id)
    if not user_profile:
        logger.warning("User profile not found in organization - Request ID: %s, ",request_id)
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

    permissions = await get_user_permissions(
        user_id, user_ctx.organization_id
    )
    profile_data = create_user_profile_data(
        user_profile=user_profile,
        user_type="organization_member",
        permissions=permissions
    )

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "user_id": str(user_profile["user_id"]),
        "email": user_profile["email"],
        "full_name": user_profile["full_name"],
        "first_name": user_profile["first_name"],
        "last_name": user_profile["last_name"],
        "phone": user_profile["phone"],
        "timezone": user_profile["timezone"],
        "avatar_url": user_profile["avatar_url"],
        "status": user_profile["status"],
        "role_id": str(user_profile["role_id"]),
        "organization_id": str(user_profile["organization_id"]),
        "updated_by_user_id": user_ctx.user_id,
        "updated_by_email": user_ctx.email,
        "update_timestamp": datetime.now().isoformat(),
    }


    return UpdateUserResponse(
        message="User updated successfully",
        data=profile_data,
    )


@router.delete(
    "/delete/{user_id}", response_model=UserResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Deleting user involves personal information
        "pii",  # User deletion contains personally identifiable information
        "audit_required",  # User deletion must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_DELETION",
)
async def delete_user_from_system(
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Remove a user from the organization (async)
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())


    # Set audit context for user deletion
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin deleting user: {user_id}"
    request.state.audit_risk_level = "high"

    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Get current user data for audit before deletion
    current_user_data = await get_user_in_organization(
        user_id, user_context.organization_id
    )

    # Set old values for audit comparison (what was deleted)
    set_audit_old_data_from_user(request, current_user_data)

    # Delete user using database operations
    result = await delete_user(user_id, user_context.organization_id)
    if not result:
        logger.warning("User not found in organization for deletion - Request ID: %s, ",request_id)
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

    # Delete auth user
    auth_result = await delete_auth_user(user_id)

    if not auth_result:
        logger.warning("Auth user not found for deletion - Request ID: %s, ",request_id)
        raise HTTPException(status_code=404, detail="User not found")

    # Set new values for audit comparison (deletion confirmation)
    request.state.raw_audit_new_data = {
        "user_id": user_id,
        "email": current_user_data["email"],
        "full_name": current_user_data["full_name"],
        "organization_id": str(user_context.organization_id),
        "deletion_status": "deleted",
        "deleted_by_user_id": user_context.user_id,
        "deleted_by_email": user_context.email,
        "deletion_timestamp": datetime.now().isoformat(),
        "auth_user_deleted": True,  # Indicates auth.users table entry was also deleted
    }

    return UserResponse(
        message="User removed successfully",
        status="success",
    )


@router.post(
    "/invite", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Inviting user involves personal information
        "pii",  # User invitation contains personally identifiable information
        "audit_required",  # User invitation must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_INVITATION",
)
@handle_api_exceptions("invite user")
async def invite_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: CreateUserRequest = Body(...)
):
    """
    Invite a user by email, send magic link, and create pending org member.
    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    # Set audit context for user invitation
    request.state.audit_table = "organization_members"
    request.state.audit_description = (
        f"Admin invited user: {body.email} with role_id: {body.role_id}"
    )
    request.state.audit_risk_level = "medium"

    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    if body.phone:
        duplicate = await check_phone_exists_for_other_user(
            body.phone, user_context.organization_id
        )
        if duplicate:
            logger.warning("Phone number already exists for another user during invitation - ")
            logger.warning("Phone: %s, Organization ID: %s",body.phone,user_context.organization_id)
            raise HTTPException(
                status_code=400,
                detail="Phone number already exists for another user in the organization",
            )

    user_id = await invite_user_with_email(body, user_context)
    # Create user using database operations
    user_data = {
        "user_id": user_id,
        "organization_id": user_context.organization_id,
        "role_id": body.role_id,
        "email": body.email,
        "full_name": body.full_name,
        "phone": body.phone,
        "timezone": body.timezone or "UTC",
        "status": "active"
    }

    await create_new_user(user_data)

    # Set audit data for user invitation
    request.state.raw_audit_new_data = {
        "invited_user_id": str(user_id),
        "email": body.email,
        "full_name": body.full_name,
        "phone": body.phone,
        "timezone": body.timezone or "UTC",
        "role_id": str(body.role_id),
        "organization_id": str(user_context.organization_id),
        "status": "active",
        "invited_by_user_id": user_context.user_id,
        "invited_by_email": user_context.email,
        "invitation_timestamp": datetime.now().isoformat(),
        "invitation_method": "supabase_magic_link",
    }


    return UserResponse(
        message="Invite sent successfully",
        status="success",
    )
