# pylint: disable=logging-fstring-interpolation
"""
User Update Operations API Module
This module provides user update operations including email updates, ban, and unban functionality.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request, Body

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.dependencies.common_utils import (
    handle_api_exceptions,
    validate_uuid_format,
    extract_user_context,
    require_permission,
    get_user_in_organization,
    set_audit_old_data_from_user,
)
from apps.user_service.app.dependencies.user_utils import (
    update_supabase_user_email,
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserResponse,
    UpdateUserEmailRequest,
    UnbanResponse,
    BanResponse,
    ErrorResponse,
)

from apps.user_service.app.app_instance import limiter

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Create router for user update endpoints
router = APIRouter(prefix="", tags=["User Update Operations"])

# Initialize logger for user update module
logger = get_logger("user-update-api")
logger.info("User Update API module loaded")


@handle_api_exceptions("update user email")
@router.put(  # pylint: disable=too-many-arguments
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
async def update_user_email(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    user_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
    body: UpdateUserEmailRequest = Body(...),
    supabase=Depends(get_supabase_admin_client),
):
    """
    Update user email
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(  # pylint: disable=logging-fstring-interpolation
        f"PUT /{user_id}/email request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}, "
        f"Target User ID: {user_id}, New Email: {body.email}"
    )

    validate_uuid_format(user_id, "role ID")
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User ID format validated - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    user_context = extract_user_context(current_user)
    logger.debug(
        f"User context extracted - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}"
    )

    # Set audit context for user email update
    request.state.audit_risk_level = "medium"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = (
        f"Admin updating user email: {user_id} to {body.email}"
    )
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"Audit context set for email update - Request ID: {request_id}, "
        f"Target User ID: {user_id}, New Email: {body.email}"
    )

    await require_permission(
        permission_code="settings.users.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="delete roles",
    )
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User permissions validated for email update - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    # Get current user data for audit before email update
    current_user_data = await get_user_in_organization(
        db_conn, user_id, user_context.organization_id
    )
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"Current user data retrieved for audit - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Current Email: {current_user_data.get('email', 'N/A')}"
    )

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    await update_supabase_user_email(
        user_id, user_context.organization_id, body.email, supabase, db_conn
    )
    logger.info(  # pylint: disable=logging-fstring-interpolation
        f"Supabase user email updated and magic link sent - Request ID: {request_id}, "
        f"Target User ID: {user_id}, New Email: {body.email}"
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

    logger.info(  # pylint: disable=logging-fstring-interpolation
        f"PUT /{user_id}/email request completed successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Old Email: {current_user_data.get('email', 'N/A')}, "
        f"New Email: {body.email}, Status Code: 200"
    )

    return UserResponse(
        message="User email updated successfully and magic link sent",
        status="success",
    )


@router.post(
    "/ban/{user_id}",
    response_model=BanResponse,
    responses={404: {"model": ErrorResponse}},
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
async def ban_user(
    user_id: str,
    request: Request,  # pylint: disable=unused-argument
    # req: BanRequest = Body(...),
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):  # pylint: disable=unused-argument
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
    logger.info(  # pylint: disable=logging-fstring-interpolation
        f"POST /ban/{user_id} request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}, "
        f"Target User ID: {user_id}"
    )

    validate_uuid_format(user_id, "User ID")
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User ID format validated - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    user_context = extract_user_context(current_user)
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User context extracted - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}"
    )

    # Set audit context for user banning
    request.state.audit_risk_level = "high"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin banned user: {user_id}"
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"Audit context set for user banning - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    await require_permission(
        permission_code="settings.users.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="delete roles",
    )
    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User permissions validated for user banning - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    if user_id == user_context.user_id:
        logger.warning(
            f"User attempted to ban themselves - Request ID: {request_id}, "
            f"User ID: {user_id}"
        )
        raise HTTPException(status_code=400, detail="You cannot ban yourself.")

    # Get current user data for audit before banning
    current_user_data = await get_user_in_organization(
        db_conn, user_id, user_context.organization_id
    )
    logger.debug(
        f"Current user data retrieved for ban audit - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Email: {current_user_data.get('email', 'N/A')}"
    )

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    banned_until = datetime.now(timezone.utc) + timedelta(days=365 * 100)
    logger.debug(
        f"Ban duration calculated - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Banned until: {banned_until}"
    )

    update_sql = """
        UPDATE auth.users
        SET banned_until = $1
        WHERE id = $2
        RETURNING id;
    """

    result = await db_conn.fetchrow(
        update_sql,
        banned_until,  # or use banned_until directly if the column is timestamptz
        user_id,
    )

    if not result:
        logger.warning(  # pylint: disable=logging-fstring-interpolation
            f"User not found for banning in auth.users - Request ID: {request_id}, "
            f"Target User ID: {user_id}"
        )
        # logging.warning("User not found for banning: %s", user_id)
        raise HTTPException(status_code=404, detail="User not found")

    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User banned in auth.users successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    suspend_member_sql = """
    UPDATE public.organization_members
    SET status = 'suspended', updated_at = NOW()
    WHERE user_id = $1 AND organization_id = $2
    RETURNING user_id;
    """
    result = await db_conn.fetchrow(
        suspend_member_sql, user_id, user_context.organization_id
    )

    if not result:
        logger.warning(  # pylint: disable=logging-fstring-interpolation
            f"Organization user not found for banning - Request ID: {request_id}, "
            f"Target User ID: {user_id}, Organization ID: {user_context.organization_id}"
        )
        # logging.warning("User not found for banning: %s", user_id)
        raise HTTPException(status_code=404, detail="Organization User not found")

    logger.debug(  # pylint: disable=logging-fstring-interpolation
        f"User suspended in organization successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Organization ID: {user_context.organization_id}"
    )

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
        "full_name": current_user_data["full_name"],
        "status": "suspended",
        "organization_id": str(current_user_data["organization_id"]),
        "banned_until": banned_until.isoformat(),
        "banned_by_user_id": user_context.user_id,
        "banned_by_email": user_context.email,
        "ban_timestamp": datetime.now().isoformat(),
        "ban_reason": "Admin ban action",
    }

    # logging.info("Banned user: %s for reason: %s", user_id, req.reason)
    logger.info(
        f"POST /ban/{user_id} request completed successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Email: {current_user_data.get('email', 'N/A')}, "
        f"Banned until: {banned_until}, Status Code: 200"
    )

    return BanResponse(message="User successfully banned", reason="")


@router.post(
    "/unban/{user_id}",
    response_model=UnbanResponse,
    responses={404: {"model": ErrorResponse}},
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
async def unban_user(
    user_id: str,
    request: Request,  # pylint: disable=unused-argument
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):  # pylint: disable=unused-argument
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
    logger.info(
        f"POST /unban/{user_id} request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}, "
        f"Target User ID: {user_id}"
    )

    # Validate user access
    validate_uuid_format(user_id, "User ID")
    logger.debug(
        f"User ID format validated - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        f"User context extracted - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}"
    )

    # Set audit context for user unbanning
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin unbanned user: {user_id}"
    request.state.audit_risk_level = "medium"
    logger.debug(
        f"Audit context set for user unbanning - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    # Check permission using utility function
    await require_permission(
        permission_code="settings.users.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="delete roles",
    )
    logger.debug(
        f"User permissions validated for user unbanning - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    if user_id == user_context.user_id:
        logger.warning(
            f"User attempted to unban themselves - Request ID: {request_id}, "
            f"User ID: {user_id}"
        )
        raise HTTPException(status_code=400, detail="You cannot Unban yourself.")

    # Get current user data for audit before unbanning
    current_user_data = await get_user_in_organization(
        db_conn, user_id, user_context.organization_id
    )
    logger.debug(
        f"Current user data retrieved for unban audit - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Email: {current_user_data.get('email', 'N/A')}"
    )

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, current_user_data)

    unban_sql = """
    UPDATE auth.users
    SET banned_until = NULL
    WHERE id = $1
    RETURNING id;
    """

    result = await db_conn.fetchrow(unban_sql, user_id)

    if not result:
        logger.warning(
            f"User not found or not banned in auth.users - Request ID: {request_id}, "
            f"Target User ID: {user_id}"
        )
        raise HTTPException(status_code=404, detail="User not found or not banned")

    logger.debug(
        f"User unbanned in auth.users successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    suspend_member_sql = """
    UPDATE public.organization_members
    SET status = 'active', updated_at = NOW()
    WHERE user_id = $1 AND organization_id = $2
    RETURNING user_id;
    """
    result = await db_conn.fetchrow(
        suspend_member_sql, user_id, user_context.organization_id
    )

    if not result:
        logger.warning(
            f"Organization user not found for unbanning - Request ID: {request_id}, "
            f"Target User ID: {user_id}, Organization ID: {user_context.organization_id}"
        )
        # logging.warning("User not found for banning: %s", user_id)
        raise HTTPException(status_code=404, detail="Organization User not found")

    logger.debug(
        f"User activated in organization successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Organization ID: {user_context.organization_id}"
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

    # logging.info("Unbanned user: %s", user_id)
    logger.info(
        f"POST /unban/{user_id} request completed successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Email: {current_user_data.get('email', 'N/A')}, "
        f"Status Code: 200"
    )

    return UnbanResponse(message="User successfully unbanned")
