
"""
User Update Operations API Module
This module provides user update operations including email updates, ban, and unban functionality.
All endpoints include proper authentication, validation, and database operations.
"""

from typing import Optional
from datetime import datetime
import uuid

from pydantic import BaseModel, Field, field_validator
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
    UpdateUserResponse,
)
from apps.user_service.app.schemas import validate_url_field

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)
from apps.user_service.app.dependencies.common_utils import extract_user_context

from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    update_supabase_user_email
)

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    suspend_user,
    revoke_suspended_user,
    update_user_info,
    get_user_profile_by_id,
)

from libs.shared_db.supabase_db.admin_operations.user import (
    ban_the_user,
    unban_the_user,
    update_metadata_of_user,
    get_user_by_id,
)

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE

# Create router for user update endpoints
router = APIRouter(prefix="/users", tags=["User Update Operations"])

# Initialize logger for user update module
logger = get_logger("user-update-api")

DELETED_ROLES = "delete roles"


# Update profile request schema
class UpdateUserProfileRequest(BaseModel):
    """Request model for updating user profile information.

    Only these fields can be updated:
    - first_name: Updated first name
    - last_name: Updated last name
    - timezone: Updated timezone preference
    - avatar_url: Updated avatar URL

    full_name will be automatically calculated from first_name + last_name.
    """
    first_name: Optional[str] = Field(None, description="Updated first name")
    last_name: Optional[str] = Field(None, description="Updated last name")
    timezone: Optional[str] = Field(None, description="Updated timezone preference")
    avatar_url: Optional[str] = Field(None, description="Updated avatar URL")

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, v):
        """Validate avatar_url is a valid URL if provided"""
        return validate_url_field(v, "avatar_url")

    model_config = {
        "json_schema_extra": {
            "example": {
                "first_name": "John",
                "last_name": "Doe",
                "timezone": "America/New_York",
                "avatar_url": "https://example.com/avatar.jpg"
            }
        }
    }

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


@router.put(
    "/update",
    response_model=UpdateUserResponse,
    status_code=status.HTTP_200_OK,
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
    body: UpdateUserProfileRequest = Body(...)
):
    """
    Update authenticated user's own profile information.

    **Self Profile Update:**
    - Uses the authenticated user's ID from the JWT token (no user_id parameter needed)
    - Users can only update their own profile
    - No admin permissions required

    **Updatable Fields:**
    - `first_name`: Updated first name
    - `last_name`: Updated last name
    - `timezone`: Updated timezone preference (e.g., "America/New_York", "UTC")
    - `avatar_url`: Updated avatar/profile picture URL

    **Automatic Behavior:**
    - `full_name` is automatically calculated from `first_name + last_name` when either is updated
    - Only fields provided in the request will be updated (partial updates supported)
    - Updates both `organization_members` table (if user is in organization) and Supabase Auth `user_metadata`

    Args:
        request: FastAPI request object
        current_user: Authenticated user from JWT token (required)
        body: UpdateUserProfileRequest containing optional fields to update

    Returns:
        UpdateUserResponse: Success message with status

    Raises:
        HTTPException:
            - 400: No fields provided for update
            - 404: User not found in organization
            - 500: Internal server error
    """
    # Extract user context from JWT token (no permission check needed for self-update)
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
        current_user_data = await get_user_in_organization(
            user_id, user_context.organization_id
        )

    # If user not in organization, create a basic profile structure
    if not current_user_data:
        # Allow users without organization to update their profile
        # Get metadata from JWT token or Supabase Auth
        user_metadata = current_user.get("user_metadata", {})
        try:
            user_data = await get_user_by_id(user_id)
            if user_data and hasattr(user_data, 'user') and user_data.user:
                user_metadata = user_data.user.user_metadata or {}
        except Exception:
            # Use JWT token metadata if Supabase call fails
            pass

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
        full_name_parts = [part.strip() for part in [current_first_name, current_last_name] if part.strip()]
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

    # Check if there's anything to update
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update"
        )

    # Update organization_members table if user is in an organization
    if user_context.organization_id:
        updated_user = await update_user_info(
            user_id,
            user_context.organization_id,
            update_data
        )

        if not updated_user:
            logger.warning(
                "Failed to update user in organization_members table for user: %s", user_id)
            # Continue with metadata update even if organization update fails
    else:
        logger.info("User %s not in organization, updating only Supabase Auth metadata", user_id)

    # Update Supabase Auth user_metadata if we have metadata to update
    if metadata_update:
        # Get current user metadata to preserve existing fields
        try:
            user_data = await get_user_by_id(user_id)
            existing_metadata = {}
            if user_data and hasattr(user_data, 'user') and user_data.user:
                existing_metadata = user_data.user.user_metadata or {}
        except Exception as e:
            # If get_user_by_id fails, use JWT token metadata as fallback
            logger.warning("Could not fetch user metadata from Supabase Auth: %s. Using JWT token metadata.", str(e))
            existing_metadata = current_user.get("user_metadata", {})

        # Merge with existing metadata
        updated_metadata = {**existing_metadata, **metadata_update}

        # Update metadata in Supabase Auth
        try:
            metadata_updated = await update_metadata_of_user(user_id, updated_metadata)
            if not metadata_updated:
                logger.warning(
                    "Failed to update user metadata in Supabase Auth for user: %s", user_id)
        except Exception as e:
            logger.warning("Error updating user metadata in Supabase Auth: %s", str(e))
            # Don't fail the entire operation if metadata update fails

    # Get updated user profile for response
    updated_profile = await get_user_profile_by_id(user_id, user_context.organization_id)

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "user_id": str(user_id),
        "first_name": updated_profile.get("first_name") if updated_profile else current_user_data.get("first_name"),
        "last_name": updated_profile.get("last_name") if updated_profile else current_user_data.get("last_name"),
        "full_name": updated_profile.get("full_name") if updated_profile else current_user_data.get("full_name"),
        "timezone": updated_profile.get("timezone") if updated_profile else current_user_data.get("timezone"),
        "avatar_url": updated_profile.get("avatar_url") if updated_profile else current_user_data.get("avatar_url"),
        "organization_id": str(user_context.organization_id) if user_context.organization_id else None,
        "updated_by_user_id": user_context.user_id,
        "updated_by_email": user_context.email,
        "update_timestamp": datetime.now().isoformat(),
    }

    return UpdateUserResponse(
        message="User profile updated successfully",
        data=None
    )
