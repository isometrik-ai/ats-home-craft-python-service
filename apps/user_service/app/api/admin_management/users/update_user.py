
"""
User Update Operations API Module
This module provides user update operations including email updates, ban, and unban functionality.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request, Body

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.app_instance import limiter

# Common utils imports
from apps.user_service.app.dependencies.common_utils import (
    handle_api_exceptions,
    validate_uuid_format,
    check_permissions,
    get_user_in_organization,
    set_audit_old_data_from_user
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserResponse,
    UpdateUserEmailRequest,
    UnbanResponse,
    BanResponse,
    ResponseModel,
)

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    update_supabase_user_email
)

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    suspend_user,
    revoke_suspended_user,
)

from libs.shared_db.supabase_db.admin_operations.user import (
    ban_the_user,
    unban_the_user,
)

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE

# Create router for user update endpoints
router = APIRouter(prefix="", tags=["User Update Operations"])

# Initialize logger for user update module
logger = get_logger("user-update-api")

DELETED_ROLES = "delete roles"

@router.put(
    "/{user_id}/email",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
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
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateUserEmailRequest = Body(...)
):
    """
    Update user email
    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    validate_uuid_format(user_id, "user ID")

    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE, DELETED_ROLES)

    # Set audit context for user email update
    request.state.audit_risk_level = "medium"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = (
        f"Admin updating user email: {user_id} to {body.email}"
    )

    # Get current user data for audit before email update
    current_user_data = await get_user_in_organization(
        user_id, user_context.organization_id
    )

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    await update_supabase_user_email(
        user_id, user_context.organization_id, body.email
    )

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": body.email,  # New email
        "full_name": current_user_data["full_name"],
        "organization_id": str(current_user_data["organization_id"]),
        "updated_by_user_id": user_context.user_id,
        "updated_by_email": user_context.email,
        "email_update_timestamp": datetime.now().isoformat(),
    }

    return UserResponse(
        message="User email updated successfully and magic link sent",
        status="success",
    )


@router.post(
    "/ban/{user_id}",
    response_model=BanResponse,
    responses={404: {"model": ResponseModel}},
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
    user_id: str,
    request: Request,
    # req: BanRequest = Body(...),
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Ban a user for a specified duration.

    Parameters:
    - user_id (str): The ID of the user to ban.
    - req (BanRequest): The request body containing ban duration and reason.
    - db_conn: Database connection dependency.
    Returns:
    - BanResponse: Confirmation message of user ban.
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    validate_uuid_format(user_id, "User ID")

    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE, DELETED_ROLES)

    # Set audit context for user banning
    request.state.audit_risk_level = "high"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin banned user: {user_id}"

    if user_id == user_context.user_id:
        logger.warning("User attempted to ban themselves - Request ID: %s, ",request_id)
        logger.warning("User ID: %s",user_id)
        raise HTTPException(status_code=400, detail="You cannot ban yourself.")

    # Get current user data for audit before banning
    current_user_data = await get_user_in_organization(
        user_id, user_context.organization_id
    )

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    # Ban user using database operations
    result = await ban_the_user(user_id)

    if not result:
        logger.warning("User not found for banning in auth.users - Request ID: %s, ",request_id)
        raise HTTPException(status_code=404, detail="User not found")


    result = await suspend_user(user_id, user_context.organization_id)
    if not result:
        logger.warning("Organization user not found for banning - Request ID: %s, ",request_id)
        logger.warning(
            "Target User ID: %s, Organization ID: %s",
            user_id,user_context.organization_id
        )
        # logging.warning("User not found for banning: %s", user_id)
        raise HTTPException(status_code=404, detail="Organization User not found")


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

    return BanResponse(message="User successfully banned", reason="")


@router.post(
    "/unban/{user_id}",
    response_model=UnbanResponse,
    responses={404: {"model": ResponseModel}},
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
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Unban a user by user ID.
    Parameters:
    - user_id (str): The ID of the user to unban.
    - db_conn: Database connection dependency.
    Returns:
    - UnbanResponse: Confirmation message of user unban.
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Validate user access
    validate_uuid_format(user_id, "User ID")

    # Extract and validate user context from JWT token
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE, DELETED_ROLES)

    # Set audit context for user unbanning
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin unbanned user: {user_id}"
    request.state.audit_risk_level = "medium"


    if user_id == user_context.user_id:
        logger.warning("User attempted to unban themselves - Request ID: %s, ",request_id)
        logger.warning("User ID: %s",user_id)
        raise HTTPException(status_code=400, detail="You cannot Unban yourself.")

    # Get current user data for audit before unbanning
    current_user_data = await get_user_in_organization(
        user_id, user_context.organization_id
    )

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    # Unban user using database operations
    result = await unban_the_user(user_id)

    if not result:
        logger.warning("User not found or not banned in auth.users - Request ID: %s, ",request_id)
        logger.warning("Target User ID: %s",user_id)
        raise HTTPException(status_code=404, detail="User not found or not banned")


    result = await revoke_suspended_user(user_id, user_context.organization_id)

    if not result:
        logger.warning("Organization user not found for unbanning - Request ID: %s, ",request_id)
        logger.warning(
            "Target User ID: %s, Organization ID: %s",
            user_id,user_context.organization_id
        )
        # logging.warning("User not found for banning: %s", user_id)
        raise HTTPException(status_code=404, detail="Organization User not found")


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

    return UnbanResponse(message="User successfully unbanned")
