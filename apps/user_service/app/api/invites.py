"""Organization Invite Management API Module
This module provides comprehensive organization invitation management APIs.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

# Schema imports
from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call

# Logger import
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.supabase import supabase_anon, supabase_service
from apps.user_service.app.schemas.invites import (
    InviteAcceptBySettingPasswordRequest,
    InviteAcceptResponse,
    InviteCreateRequest,
    InviteListResponse,
    InviteResponse,
    InviteStatus,
    InviteValidateLinkRequest,
    InviteValidateLinkResponse,
)

# Service import
from apps.user_service.app.services.invite_service import InviteService

# Local imports - app dependencies and schemas
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE, SETTINGS_USERS_MANAGE
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for invite endpoints
router = APIRouter(prefix="/invite", tags=["Organization Invitations"])


@handle_api_exceptions("validate invite link")
@router.post(
    "/validate/link",
    description="Validate invite link and check if user is existing",
    summary="Validate invite link and check if user is existing",
    status_code=http_status.HTTP_200_OK,
    response_model=InviteValidateLinkResponse,
    responses={
        http_status.HTTP_200_OK: {
            "model": InviteValidateLinkResponse,
            "description": "Invite link validated successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Invitation not found or expired"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def validate_invite_link(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    body: InviteValidateLinkRequest = Body(...),
):
    """Validate invite link and check if user is existing"""
    # Create service and delegate to service
    invite_service = InviteService(user_context=None, db_connection=db_connection)
    result = await invite_service.validate_invite_link(body.token)

    return success_response(
        request=request,
        message_key="invitations.success.link_validated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=InviteValidateLinkResponse(
            is_existing_user=result["is_existing_user"],
            has_password=result.get("has_password", False),
        ),
    )


@handle_api_exceptions("accept invitation by setting password")
@router.post(
    "/set-password",
    description="Accept an organization invitation by setting password",
    summary="Accept an organization invitation by setting password",
    status_code=http_status.HTTP_202_ACCEPTED,
    response_model=InviteAcceptResponse,
    responses={
        http_status.HTTP_202_ACCEPTED: {
            "model": InviteAcceptResponse,
            "description": "Invitation accepted successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
    },
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
async def accept_and_set_password_invitation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_admin_client: AsyncClient = Depends(supabase_service),
    sb_anon_client: AsyncClient = Depends(supabase_anon),
    body: InviteAcceptBySettingPasswordRequest = Body(...),
):
    """Accept an organization invitation by setting password"""
    # Set audit context for invitation acceptance
    request.state.audit_table = "organization_invites"
    request.state.audit_description = f"Accepted invitation for token: {body.token}"
    request.state.audit_risk_level = "medium"

    # Create service and delegate to service
    invite_service = InviteService(
        user_context=None,
        db_connection=db_connection,
        sb_admin_client=sb_admin_client,
        sb_anon_client=sb_anon_client,
    )
    result = await invite_service.accept_and_set_password(body)

    return success_response(
        request=request,
        message_key="invitations.success.invitation_accepted",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=result,
    )


@handle_api_exceptions("create organization invitation")
@router.post(
    "/{organization_id}",
    response_model=InviteResponse,
    description="Create a new organization invitation",
    summary="Create a new organization invitation",
    status_code=http_status.HTTP_201_CREATED,
    responses={
        http_status.HTTP_201_CREATED: {
            "model": InviteResponse,
            "description": "Invitation created successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
    },
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
async def create_invitation(
    organization_id: str,
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_service),
    current_user: dict = Depends(get_user_from_auth),
    body: InviteCreateRequest = Body(...),
):
    """Create a new organization invitation"""
    # Set audit context for invitation creation
    request.state.audit_table = "organization_invites"
    temp_string = f"Created invitation for email: {body.email} in organization: {organization_id}"
    request.state.audit_description = temp_string
    request.state.audit_risk_level = "medium"

    # Extract user context & Check permissions
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_USERS_MANAGE,
        organization_id=organization_id,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service and delegate to service
    invite_service = InviteService(
        user_context=user_context,
        db_connection=db_connection,
        sb_admin_client=sb_client,
    )
    result = await invite_service.create_invitation(organization_id, body)
    request.state.audit_requested_id = str(result.get("invite_id", "")) if result else ""

    return success_response(
        request=request,
        message_key="invitations.success.invitation_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=InviteResponse(
            invite_id=str(result["invite_id"]),
            invite_url=result["invite_url"],
            email=result["email"],
            expires_at=str(result["expires_at"]),
        ),
    )


@handle_api_exceptions("get organization invitations")
@router.get(
    "/{organization_id}",
    description="Get list of all invitations for an organization",
    summary="Get list of all invitations for an organization",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses={
        http_status.HTTP_200_OK: {
            "model": InviteListResponse,
            "description": "List of invitations with pagination info",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
    },
)
@limiter.limit("100/minute")
async def get_organization_invitations(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    organization_id: str = Path(..., description="The UUID of the organization"),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    status: InviteStatus | None = Query(None, description="The status of the invitations"),
):
    """Get list of all invitations for an organization with pagination"""
    # Extract user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=organization_id,
    )

    # Create service and delegate to service
    invite_service = InviteService(user_context=user_context, db_connection=db_connection)
    result = await invite_service.get_organization_invitations(
        organization_id=organization_id, page=page, page_size=page_size, status=status
    )

    if not result["items"]:
        return success_response(
            request=request,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_204_NO_CONTENT,
        )

    return list_response(
        request=request,
        items=result["items"],
        total=result["total_count"],
        message_key="success.retrieved",
        page=result["page"],
        page_size=result["page_size"],
        status_code=http_status.HTTP_200_OK,
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("resend invitation")
@router.put(
    "/resend/{invite_id}",
    description="Resend an organization invitation",
    summary="Resend an organization invitation",
    status_code=http_status.HTTP_202_ACCEPTED,
    response_model=None,
    responses={
        http_status.HTTP_202_ACCEPTED: {
            "model": InviteResponse,
            "description": "Invitation resent successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
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
        "gdpr",  # Resending invitation involves personal information
        "pii",  # Invitation resend contains personally identifiable information
        "soc2_audit",  # Invitation management is critical for SOC2 compliance
        "audit_required",  # Invitation resend requires audit trail
    ],
    table_name="organization_invites",
    category="INVITATION",
)
async def resend_invitation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_service),
    current_user: dict = Depends(get_user_from_auth),
    invite_id: str = Path(..., description="The UUID of the invitation"),
):
    """Resend an organization invitation"""
    # Set audit context for invitation resend
    request.state.audit_table = "organization_invites"
    request.state.audit_requested_id = invite_id
    request.state.audit_description = f"Resent invitation for ID: {invite_id}"
    request.state.audit_risk_level = "low"

    # Extract user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service and delegate to service
    invite_service = InviteService(
        user_context=user_context,
        db_connection=db_connection,
        sb_admin_client=sb_client,
    )
    result = await invite_service.resend_invitation(invite_id)

    return success_response(
        request=request,
        message_key="invitations.success.invitation_resent",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=InviteResponse(
            invite_id=result["invite_id"],
            invite_url=result["invite_url"],
            email=result["email"],
            expires_at=str(result["expires_at"]),
        ),
    )


@handle_api_exceptions("delete invitation")
@router.delete(
    "/{invite_id}",
    description="Delete an organization invitation",
    summary="Delete an organization invitation",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses={
        http_status.HTTP_200_OK: {"description": "Invitation deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
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
async def delete_invitation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    invite_id: str = Path(..., description="The UUID of the invitation"),
):
    """Delete an organization invitation"""
    # Set audit context for invitation deletion
    request.state.audit_table = "organization_invites"
    request.state.audit_requested_id = invite_id
    request.state.audit_description = f"Deleted invitation for ID: {invite_id}"
    request.state.audit_risk_level = "high"

    # Extract user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service and delegate to service
    invite_service = InviteService(user_context=user_context, db_connection=db_connection)
    await invite_service.delete_invitation(invite_id)

    return success_response(
        request=request,
        message_key="invitations.success.invitation_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
