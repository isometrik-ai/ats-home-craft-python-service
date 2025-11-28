"""
Organization Invite Management API Module

This module provides comprehensive organization invitation management APIs.
All endpoints include proper authentication, validation, and database operations.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Endpoints Covered:
- Create organization invitation
- List organization invitations
- Get invitation details
- Accept invitation
- Reject invitation
- Resend invitation
- Revoke invitation
- Delete invitation
"""

import uuid
import os
from typing import Optional
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, status, Depends, Body, Query, Request
from datetime import datetime, timezone
# Logger import
# from apps.user_service.app.api.admin_management.users import update_user
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.app_instance import limiter

# Local imports - app dependencies and schemas
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    handle_api_exceptions,
    validate_pagination_params,
    validate_uuid_format,
    require_permission,
    check_permissions,
)
from apps.user_service.app.dependencies.invite_utils import (
    validate_email_format,
    build_invite_details_response,
    build_invite_list_item,
    handle_invite_validation_error,
    handle_invite_permission_error,
    generate_invite_url,
    check_organization_capacity,
    validate_organization_access,
)

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Schema imports
from apps.user_service.app.api.auth import _is_password_strong, PASSWORD_CONDITION_MESSAGE_EXTENDED
from apps.user_service.app.schemas.auth import SignupRequest
from apps.user_service.app.schemas.invites import (
    InviteCreateRequest,
    InviteResponse,
    InviteAcceptBySettingPasswordRequest,
    InviteAcceptResponse,
    InviteListResponse,
)

# Third-party imports
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import log_exception, sign_up_supabase_user
from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id, update_user

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.invite_operations import (
    create_organization_invite,
    get_invite_by_token,
    get_invite_by_id,
    get_organization_invites,
    get_organization_invites_count,
    update_invite_status,
    delete_invite,
    check_existing_invite,
    check_user_membership,
    add_user_to_organization
)
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    get_organisation_details_by_id,
)
from libs.shared_db.postgres_db.user_service_operations.role_operations import (
    get_role_by_id,
)

from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE, SETTINGS_USERS_MANAGE
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Email service import
from libs.shared_utils.email_utils import send_organization_invitation_email

# Create router for invite endpoints
router = APIRouter(prefix="/invite", tags=["Organization Invitations"])

# Initialize logger for invite module
logger = get_logger("invite-api")

# Authentication description for API documentation
INVITE_NOT_FOUND_MESSAGE = "Invitation not found"

# Configure this based on your environment
BASE_URL = os.getenv("BASE_URL")

# Constants for repeated strings
ORGANIZATION_MANAGE_PERMISSION = "organization.appscrip.manage"
INVITATION_ID_LABEL = "invitation ID"
DATABASE_ERROR_MESSAGE = "Database transaction failed - Request ID: %s, Error: %s"
INVALID_INVITATION_TOKEN_MESSAGE = "Invalid invitation token"
INVALID_INVITATION_REQUEST_MESSAGE = INVALID_INVITATION_TOKEN_MESSAGE + " Request ID: %s"



@dataclass
class InviteQueryParams:
    """Query parameters for invitation listing and filtering."""

    page: int = 1
    page_size: int = 20
    status: Optional[str] = None


def get_invite_query_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
) -> InviteQueryParams:
    """
    Dependency function to extract and validate invitation query parameters.

    Args:
        page: Page number for pagination
        page_size: Number of items per page
        status: Filter by invitation status

    Returns:
        InviteQueryParams: Validated query parameters
    """
    return InviteQueryParams(
        page=page, page_size=page_size, status=status
    )


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _validate_invite_request(request: InviteCreateRequest) -> None:
    """
    Validate invitation request data.

    Args:
        request: Invitation creation request

    Raises:
        HTTPException: If validation fails
    """
    # Validate email format
    if not validate_email_format(request.email):
        handle_invite_validation_error("email", request.email, "Invalid email format")

    # Validate role
    if not isinstance(request.role_id, uuid.UUID):
        handle_invite_validation_error(
            "role", request.role_id, "Invalid role ID")


async def _process_invite_list_request(
    user_context, organization_id: str, query_params: InviteQueryParams
):
    """
    Process the complete invitation list request.

    Args:
        user_context: User context from JWT
        organization_id: Organization ID
        query_params: Query parameters

    Returns:
        tuple: (invitations, total_count, page, page_size, message)
    """
    # Validate organization access
    if not await validate_organization_access(user_context, organization_id):
        handle_invite_permission_error("access organization invitations")

    # Validate and process query parameters
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )

    # Execute queries and get results
    invitations_data = await get_organization_invites(
        organization_id=organization_id,
        limit=page_size,
        offset=offset
    )
    total_count = await get_organization_invites_count(
        organization_id=organization_id,
    )

    # Process results
    invitations = [build_invite_list_item(invite) for invite in invitations_data]

    # Build response message
    message = f"Retrieved {len(invitations)} invitations"

    return invitations, total_count, page, page_size, message


# ============================================================================
# API ENDPOINTS
# ============================================================================

# IMPORTANT: Specific routes must come BEFORE parameterized routes to avoid conflicts
# Routes like /accept, /reject, /cleanup must be defined before /{organization_id}

@handle_api_exceptions("accept invitation by setting password")
@router.post(
    "/set-password",
    response_model=InviteAcceptResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Accepting invitation involves personal information
        "pii",  # Invitation acceptance contains personally identifiable information
        "soc2_audit",  # Invitation management is critical for SOC2 compliance
        "audit_required",  # Invitation acceptance requires audit trail
    ],
    table_name="organization_invites",
    category="INVITATION",
)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def accept_and_set_password_invitation(
    request: Request,
    body: InviteAcceptBySettingPasswordRequest = Body(...),
):
    """
    Accept an organization invitation

    Args:
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information
        body (InviteAcceptBySettingPasswordRequest): Invitation acceptance data

    Returns:
        InviteAcceptResponse: Success response with organization details
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for invitation acceptance
    request.state.audit_table = "organization_invites"
    request.state.audit_description = f"Accepted invitation for token: {body.token}"
    request.state.audit_risk_level = "medium"

    # # Extract user context
    # user_context = await extract_user_context(current_user)

    # request.state.audit_user_context = {
    #     "user_id": user_context.user_id,
    #     "user_email": user_context.email,
    #     "organization_id": user_context.organization_id,
    # }

    if not body.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is required"
        )

    if not _is_password_strong(body.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=PASSWORD_CONDITION_MESSAGE_EXTENDED
        )

    # Get invitation details by token
    invitation_data = await get_invite_by_token(body.token)

    if not invitation_data:
        logger.warning(INVALID_INVITATION_REQUEST_MESSAGE, request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INVALID_INVITATION_TOKEN_MESSAGE
        )

    # if user_context.email != invitation_data["email"]:
    #     raise HTTPException(
    #         status_code=status.HTTP_409_CONFLICT,
    #         detail="User email does not match invitation email\nPlease Login with the correct email"
    #     )

    # Check if user is already a member
    existing_member = await check_user_membership(
        invitation_data["organization_id"],
        invitation_data["email"]
    )
    if existing_member:
        logger.info("User already a member - Request ID: %s, User: %s",
                request_id, invitation_data["email"])
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this organization"
        )

    role_name = await get_role_by_id(invitation_data['role_id'], invitation_data["organization_id"])

    try:
        inv_meta = invitation_data["metadata"]

        signup_result = await sign_up_supabase_user(SignupRequest(
            email=invitation_data["email"],
            password=body.password,
            first_name=inv_meta.get("first_name", None),
            last_name=inv_meta.get("last_name", None),
            phone=inv_meta.get("phone", None),
            timezone="UTC",
            salutation=inv_meta.get("salutation", None),
            verification_id="",
            verification_code="",
        ))

        if not signup_result:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user account"
            )

        # Add user to organization
        await add_user_to_organization(
            organization_id=invitation_data["organization_id"],
            invite_data={
                "user_id": signup_result.user.id,
                "first_name": inv_meta.get("first_name", None),
                "last_name": inv_meta.get("last_name", None),
                "phone": inv_meta.get("phone", None),
                "timezone": "UTC",
                "salutation": inv_meta.get("salutation", None),
            },
            email=invitation_data["email"],
            role_id=invitation_data['role_id'],
            role_name=role_name["name"],
            invited_by=invitation_data["invited_by"]
        )

        # Update invitation status
        await update_invite_status(
            invitation_data["id"],
            "accepted",
            signup_result.user.id
        )

        logger.info("Invitation accepted successfully - Request ID: %s, User: %s",
                   request_id, invitation_data["email"])

        return InviteAcceptResponse(
            success=True,
            message="Invitation accepted successfully"
        )

    except HTTPException as signup_error:
        raise signup_error
    except Exception as db_error:
        log_exception()
        logger.error("Database error accepting invitation - Request ID: %s, Error: %s",
                    request_id, str(db_error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to accept invitation"
        ) from db_error


@handle_api_exceptions("create organization invitation")
@router.post(
    "/{organization_id}",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Creating invitation involves personal information
        "pii",  # Invitation contains personally identifiable information
        "soc2_audit",  # Invitation management is critical for SOC2 compliance
        "audit_required",  # Invitation creation requires audit trail
    ],
    table_name="organization_invites",
    category="INVITATION",
)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def create_invitation(
    organization_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: InviteCreateRequest = Body(...),
):
    """
    Create a new organization invitation (Requires: organization.appscrip.manage)

    This endpoint creates an invitation for a user to join an organization with a specific role.

    Args:
        organization_id (str): The UUID of the organization
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information
        body (InviteCreateRequest): Invitation creation data

    Returns:
        InviteResponse: Success response with invitation details
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for invitation creation
    request.state.audit_table = "organization_invites"
    temp_string = f"Created invitation for email: {body.email} in organization: {organization_id}"
    request.state.audit_description = temp_string
    request.state.audit_risk_level = "medium"

    # Validate organization ID format
    validate_uuid_format(organization_id, "organization ID")

    # Extract user context & Check permissions
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_USERS_MANAGE,
        organization_id=organization_id,
        action_description="create organization invitations"
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Validate organization access
    if not await validate_organization_access(user_context, organization_id):
        handle_invite_permission_error("create organization invitations")

    # Validate request data
    _validate_invite_request(body)

    # Get organization details
    organization_data = await get_organisation_details_by_id(organization_id)
    if not organization_data:
        logger.warning("Organization not found - Request ID: %s", request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    # Check organization capacity
    await check_organization_capacity(organization_data)

    # Check if user is already a member
    existing_member = await check_user_membership(organization_id, body.email)
    if existing_member:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this organization"
        )

    # Check for existing pending invitation
    existing_invite = await check_existing_invite(organization_id, body.email)
    if existing_invite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending invitation already exists for this email"
        )

    # Create invitation
    try:
        invite_data = {
            "organization_id": organization_id,
            "email": body.email,
            "role_id": body.role_id,
            "invited_by": user_context.user_id,
            "first_name": body.first_name,
            "last_name": body.last_name,
            "phone": body.phone,
            "salutation": body.salutation,
        }

        created_invite = await create_organization_invite(invite_data)

        # Generate invitation URL (you may need to configure your base URL)
        invite_url = generate_invite_url(BASE_URL, created_invite["token_hash"])

        role_name = await get_role_by_id(body.role_id, organization_id)
        inviter_name = await get_user_by_id(user_context.user_id)

        inviter_full_name, invitee_full_name = "", ""

        for z in [body.salutation, body.first_name, body.last_name]:
            if z:
                invitee_full_name += f"{z} "

        user_meta = inviter_name.user.user_metadata
        for z in [user_meta.get("salutation", None), user_meta.get("first_name", None), user_meta.get("last_name", None)]:
            if z:
                inviter_full_name += f"{z} "

        # Send invitation email
        email_sent = send_organization_invitation_email(
            email=body.email,
            organization_name=organization_data["name"],
            inviter_name=inviter_full_name.strip(),  # You might want to get the actual name
            invitee_name=invitee_full_name.strip(),
            invite_url=invite_url,
            role_name=role_name["name"],
            expires_at=created_invite["expires_at"]
        )

        if not email_sent:
            logger.warning("Failed to send invitation email - Request ID: %s, Email: %s",
                         request_id, body.email)

        logger.info("Invitation created successfully - Request ID: %s, Invite ID: %s",
                   request_id, created_invite["id"])

        return InviteResponse(
            success=True,
            invite_id=created_invite["id"],
            invite_url=invite_url,
            email=body.email,
            expires_at=created_invite["expires_at"],
            message="Invitation created successfully"
        )
    except HTTPException as email_error:
        raise email_error

    except Exception as db_error:
        log_exception()
        logger.error(DATABASE_ERROR_MESSAGE, request_id, str(db_error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create invitation"
        ) from db_error


@handle_api_exceptions("get organization invitations")
@router.get(
    "/{organization_id}",
    response_model=InviteListResponse,
    status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_organization_invitations(
    organization_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: InviteQueryParams = Depends(get_invite_query_params),
):
    """
    Get list of all invitations for an organization (Requires: organization.appscrip.manage)

    Args:
        organization_id (str): The UUID of the organization
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information
        query_params (InviteQueryParams): Query parameters for filtering and pagination

    Returns:
        InviteListResponse: List of invitations with pagination info
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for invitation listing
    request.state.audit_table = "organization_invites"
    request.state.audit_description = f"Retrieved invitations for organization: {organization_id}"
    request.state.audit_risk_level = "low"

    # Validate organization ID format
    validate_uuid_format(organization_id, "organization ID")

    # Extract user context
    user_context = await check_permissions(current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="view organization invitations",
        organization_id=organization_id)
    # Process the request
    invitations, total_count, page, page_size, message = (
        await _process_invite_list_request(user_context, organization_id, query_params)
    )

    logger.info("Retrieved invitations - Request ID: %s, Count: %s",
            request_id, len(invitations))

    return InviteListResponse(
        message=message,
        data=invitations,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


@handle_api_exceptions("resend invitation")
@router.put(
    "/resend/{invite_id}",
    response_model=InviteResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Resending invitation involves personal information
        "pii",  # Invitation resend contains personally identifiable information
        "soc2_audit",  # Invitation management is critical for SOC2 compliance
        "audit_required",  # Invitation resend requires audit trail
    ],
    table_name="organization_invites",
    category="INVITATION",
)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def resend_invitation(
    invite_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Resend an organization invitation (Requires: organization.appscrip.manage)

    Args:
        invite_id (str): The UUID of the invitation
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information

    Returns:
        InviteResponse: Success response
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for invitation resend
    request.state.audit_table = "organization_invites"
    request.state.audit_requested_id = invite_id
    request.state.audit_description = f"Resent invitation for ID: {invite_id}"
    request.state.audit_risk_level = "low"

    # Validate invitation ID format
    validate_uuid_format(invite_id, INVITATION_ID_LABEL)

    # Extract user context
    user_context = await check_permissions(current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="resend organization invitations")

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Get invitation details
    invitation_data = await get_invite_by_id(invite_id)

    if not invitation_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INVITE_NOT_FOUND_MESSAGE,
        )

    # Validate organization access
    if not await validate_organization_access(user_context, invitation_data["organization_id"]):
        handle_invite_permission_error("resend organization invitations")

    # Resend invitation email
    try:
        # Get organization details
        organization_data = await get_organisation_details_by_id(invitation_data["organization_id"])

        # Generate invitation URL
        invite_url = generate_invite_url(BASE_URL, invitation_data["token_hash"])

        role_name = await get_role_by_id(invitation_data["role_id"], organization_data["id"])
        inviter_name = await get_user_by_id(invitation_data["invited_by"])

        if not role_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found"
            )

        inviter_full_name, invitee_full_name = "", ""

        user_meta = inviter_name.user.user_metadata
        for z in [user_meta.get("salutation", None), user_meta.get("first_name", None), user_meta.get("last_name", None)]:
            if z:
                inviter_full_name += f"{z} "

        inv_meta = invitation_data["metadata"]
        for z in [inv_meta.get("salutation", None), inv_meta.get("first_name", None), inv_meta.get("last_name", None)]:
            if z:
                invitee_full_name += f"{z} "

        # Send invitation email
        email_sent = send_organization_invitation_email(
            email=invitation_data["email"],
            organization_name=organization_data["name"],
            inviter_name=inviter_full_name.strip(),  # You might want to get the actual name
            invitee_name=invitee_full_name.strip(),
            invite_url=invite_url,
            role_name=role_name["name"],
            expires_at=invitation_data["expires_at"]
        )

        if not email_sent:
            logger.warning("Failed to resend invitation email - Request ID: %s, Email: %s",
                         request_id, invitation_data["email"])

        logger.info("Invitation resent - Request ID: %s, Invite ID: %s, Email: %s",
                   request_id, invite_id, invitation_data["email"])

        return InviteResponse(
            success=True,
            invite_id=invite_id,
            invite_url=invite_url,
            email=invitation_data["email"],
            expires_at=invitation_data["expires_at"],
            message="Invitation resent successfully"
        )

    except Exception as email_error:
        logger.error("Failed to resend invitation email - Request ID: %s, Error: %s",
                   request_id, str(email_error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resend invitation email"
        ) from email_error



@handle_api_exceptions("delete invitation")
@router.delete(
    "/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Deleting invitation involves personal information
        "pii",  # Invitation deletion contains personally identifiable information
        "soc2_audit",  # Invitation management is critical for SOC2 compliance
        "audit_required",  # Invitation deletion requires audit trail
    ],
    table_name="organization_invites",
    category="INVITATION",
)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_invitation(
    invite_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Delete an organization invitation (Requires: organization.appscrip.manage)

    Args:
        invite_id (str): The UUID of the invitation
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information

    Returns:
        InviteResponse: Success response
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for invitation deletion
    request.state.audit_table = "organization_invites"
    request.state.audit_requested_id = invite_id
    request.state.audit_description = f"Deleted invitation for ID: {invite_id}"
    request.state.audit_risk_level = "high"

    # Validate invitation ID format
    validate_uuid_format(invite_id, INVITATION_ID_LABEL)

    # Extract user context
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="delete organization invitations",
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Get invitation details
    invitation_data = await get_invite_by_id(invite_id)

    if not invitation_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INVITE_NOT_FOUND_MESSAGE,
        )

    # Validate organization access
    if not await validate_organization_access(user_context, invitation_data["organization_id"]):
        handle_invite_permission_error("delete organization invitations")

    # Delete invitation
    try:
        result = await delete_invite(invite_id)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=INVITE_NOT_FOUND_MESSAGE,
            )

        logger.info("Invitation deleted successfully - Request ID: %s, Invite ID: %s",
                   request_id, invite_id)

        return None

    except HTTPException as http_error:
        raise http_error
    except Exception as db_error:
        log_exception()
        logger.error(DATABASE_ERROR_MESSAGE, request_id, str(db_error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete invitation"
        ) from db_error
