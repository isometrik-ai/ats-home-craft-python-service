"""Organization Invite Management API Module
This module provides comprehensive organization invitation management APIs.
All endpoints include proper authentication, validation, and database operations.
"""

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request
from fastapi import status as http_status

# Schema imports
from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call

# Logger import
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.auth import SignupRequest
from apps.user_service.app.schemas.invites import (
    InviteAcceptBySettingPasswordRequest,
    InviteAcceptResponse,
    InviteCreateRequest,
    InviteListResponse,
    InviteResponse,
)

# Local imports - app dependencies and schemas
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
    validate_uuid_format,
)
from apps.user_service.app.utils.invite_utils import build_invite_list_item
from apps.user_service.app.utils.organisation_utils import (
    validate_organization_subscription,
)
from apps.user_service.app.utils.user_utils import build_full_name

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.invite_operations import (
    add_user_to_organization,
    check_existing_invite,
    check_user_membership,
    create_organization_invite,
    delete_invite,
    get_invite_by_id,
    get_invite_by_token,
    get_organization_invites,
    get_organization_invites_count,
    update_invite_status,
)
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    get_organisation_details_by_id,
)
from libs.shared_db.postgres_db.user_service_operations.role_operations import (
    get_role_by_id,
)
from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id

# Third-party imports
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    sign_up_supabase_user,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE, SETTINGS_USERS_MANAGE

# Email service import
from libs.shared_utils.email_utils import send_organization_invitation_email
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("invite-api")


# Create router for invite endpoints
router = APIRouter(prefix="/invite", tags=["Organization Invitations"])


BASE_URL = os.getenv("BASE_URL")


@handle_api_exceptions("accept invitation by setting password")
@router.post(
    "/set-password",
    description="Accept an organization invitation by setting password",
    summary="Accept an organization invitation by setting password",
    status_code=http_status.HTTP_202_ACCEPTED,
    response_model=None,
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
    body: InviteAcceptBySettingPasswordRequest = Body(...),
):
    """Accept an organization invitation by setting password"""
    try:
        # Set audit context for invitation acceptance
        request.state.audit_table = "organization_invites"
        request.state.audit_description = f"Accepted invitation for token: {body.token}"
        request.state.audit_risk_level = "medium"

        # Get invitation details by token
        invitation_data = await get_invite_by_token(body.token)
        if not invitation_data:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Check if user is already a member
        existing_member = await check_user_membership(
            invitation_data["organization_id"], invitation_data["email"]
        )
        if existing_member:
            raise ConflictException(
                message_key="invitations.errors.user_already_a_member",
                custom_code=CustomStatusCode.CONFLICT,
            )

        organization_data = await get_organisation_details_by_id(invitation_data["organization_id"])
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        role_name = await get_role_by_id(
            invitation_data["role_id"], invitation_data["organization_id"]
        )
        if not role_name:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        inv_meta = invitation_data["metadata"]

        signup_result = await sign_up_supabase_user(
            SignupRequest(
                email=invitation_data["email"],
                password=body.password,
                first_name=inv_meta.get("first_name", None),
                last_name=inv_meta.get("last_name", None),
                phone=inv_meta.get("phone", None),
                timezone="UTC",
                salutation=inv_meta.get("salutation", None),
                verification_id="",
                verification_code="",
            )
        )

        if not signup_result:
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        isometrik_credentials = organization_data.get("settings", {}).get(
            "isometrik_application_details", {}
        )

        if not isometrik_credentials:
            raise NotFoundException(
                message_key="invitations.errors.isometrik_application_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
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
            role_id=invitation_data["role_id"],
            role_name=role_name["name"],
            invited_by=invitation_data["invited_by"],
            isometrik_credentials=isometrik_credentials,
        )

        # Update invitation status
        await update_invite_status(invitation_data["id"], "accepted", signup_result.user.id)

        return success_response(
            request=request,
            message_key="invitations.success.invitation_accepted",
            custom_code=CustomStatusCode.ACCEPTED,
            status_code=http_status.HTTP_202_ACCEPTED,
        )

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error accepting invitation - Error: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


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
    current_user: dict = Depends(get_user_from_auth),
    body: InviteCreateRequest = Body(...),
):
    """Create a new organization invitation"""
    try:
        # Set audit context for invitation creation
        request.state.audit_table = "organization_invites"
        temp_string = (
            f"Created invitation for email: {body.email} in organization: {organization_id}"
        )
        request.state.audit_description = temp_string
        request.state.audit_risk_level = "medium"

        # Validate organization ID format
        validate_uuid_format(organization_id, "organization ID")

        # Extract user context & Check permissions
        user_context = await check_permissions(
            current_user=current_user,
            permission_codes=SETTINGS_USERS_MANAGE,
            organization_id=organization_id,
        )

        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }

        if not user_context.organization_id == organization_id:
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Get organization details
        organization_data = await get_organisation_details_by_id(organization_id)
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Check organization capacity
        await validate_organization_subscription(organization_data)

        # Check if user is already a member
        existing_member = await check_user_membership(organization_id, body.email)
        if existing_member:
            raise ConflictException(
                message_key="invitations.errors.user_already_a_member",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Check for existing pending invitation
        existing_invite = await check_existing_invite(organization_id, body.email)
        if existing_invite:
            raise ConflictException(
                message_key="invitations.errors.pending_invitation_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

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

        # Generate invitation URL
        invite_url = (
            f"{BASE_URL.rstrip('/')}/invite/accept/"
            f"?token={created_invite['token_hash']}&page=invite-user"
        )

        role_name = await get_role_by_id(body.role_id, organization_id)
        if not role_name:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        inviter = await get_user_by_id(user_context.user_id)

        invitee_full_name = build_full_name(body.salutation, body.first_name, body.last_name)

        user_meta = inviter.user.user_metadata or {}
        inviter_full_name = build_full_name(
            user_meta.get("salutation"),
            user_meta.get("first_name"),
            user_meta.get("last_name"),
        )

        # Send invitation email
        await send_organization_invitation_email(
            email=body.email,
            organization_name=organization_data["name"],
            inviter_name=inviter_full_name.strip(),
            invitee_name=invitee_full_name.strip(),
            invite_url=invite_url,
            role_name=role_name["name"],
            expires_at=created_invite["expires_at"],
        )

        return success_response(
            request=request,
            message_key="invitations.success.invitation_created",
            custom_code=CustomStatusCode.CREATED,
            status_code=http_status.HTTP_201_CREATED,
            data=InviteResponse(
                invite_id=created_invite["id"],
                invite_url=invite_url,
                email=body.email,
                expires_at=created_invite["expires_at"],
            ),
        )

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error creating organization invitation - Error: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


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
    current_user: dict = Depends(get_user_from_auth),
    organization_id: str = Path(..., description="The UUID of the organization"),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    status: str | None = Query(None, description="The status of the invitations"),
):
    """Get list of all invitations for an organization with pagination"""
    try:
        # Validate organization ID format
        validate_uuid_format(organization_id, "organization ID")

        # Extract user context
        user_context = await check_permissions(
            current_user=current_user,
            permission_codes=SETTINGS_SYSTEM_MANAGE,
            organization_id=organization_id,
        )

        if not user_context.organization_id == organization_id:
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Execute queries and get results
        invitations_data = await get_organization_invites(
            organization_id=organization_id,
            limit=page_size,
            offset=(page - 1) * page_size,
            status=status,
        )

        if not invitations_data:
            return success_response(
                request=request,
                message_key="success.no_data",
                custom_code=CustomStatusCode.NO_CONTENT,
                status_code=http_status.HTTP_204_NO_CONTENT,
            )

        invitations_list = [build_invite_list_item(invite) for invite in invitations_data]

        total_count = await get_organization_invites_count(
            organization_id=organization_id, status=status
        )

        return list_response(
            request=request,
            items=invitations_list,
            total=total_count,
            message_key="success.retrieved",
            page=page,
            page_size=page_size,
            status_code=http_status.HTTP_200_OK,
            custom_code=CustomStatusCode.SUCCESS,
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error getting organization invitations - Error: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            params={"operation_name": "get organization invitations"},
        ) from error


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
    current_user: dict = Depends(get_user_from_auth),
    invite_id: str = Path(..., description="The UUID of the invitation"),
):
    """Resend an organization invitation"""
    try:
        # Set audit context for invitation resend
        request.state.audit_table = "organization_invites"
        request.state.audit_requested_id = invite_id
        request.state.audit_description = f"Resent invitation for ID: {invite_id}"
        request.state.audit_risk_level = "low"

        # Extract user context
        user_context = await check_permissions(
            current_user=current_user,
            permission_codes=SETTINGS_SYSTEM_MANAGE,
        )

        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }

        # Get invitation details
        invitation_data = await get_invite_by_id(invite_id)

        if not invitation_data:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if not user_context.organization_id == invitation_data["organization_id"]:
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Get organization details
        organization_data = await get_organisation_details_by_id(invitation_data["organization_id"])
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Generate invitation URL
        invite_url = (
            f"{BASE_URL.rstrip('/')}/invite/accept/"
            f"?token={invitation_data['token_hash']}&page=invite-user"
        )

        role_name = await get_role_by_id(invitation_data["role_id"], organization_data["id"])

        if not role_name:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        inviter = await get_user_by_id(invitation_data["invited_by"])

        invitee_full_name = build_full_name(
            invitation_data["metadata"].get("salutation"),
            invitation_data["metadata"].get("first_name"),
            invitation_data["metadata"].get("last_name"),
        )

        user_meta = inviter.user.user_metadata or {}
        inviter_full_name = build_full_name(
            user_meta.get("salutation"),
            user_meta.get("first_name"),
            user_meta.get("last_name"),
        )

        # Send invitation email
        await send_organization_invitation_email(
            email=invitation_data["email"],
            organization_name=organization_data["name"],
            inviter_name=inviter_full_name.strip(),
            invitee_name=invitee_full_name.strip(),
            invite_url=invite_url,
            role_name=role_name["name"],
            expires_at=invitation_data["expires_at"],
        )

        return success_response(
            request=request,
            message_key="invitations.success.invitation_resent",
            custom_code=CustomStatusCode.ACCEPTED,
            status_code=http_status.HTTP_202_ACCEPTED,
            data=InviteResponse(
                invite_id=invite_id,
                invite_url=invite_url,
                email=invitation_data["email"],
                expires_at=invitation_data["expires_at"],
            ),
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error resending organization invitation - Error: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


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
    current_user: dict = Depends(get_user_from_auth),
    invite_id: str = Path(..., description="The UUID of the invitation"),
):
    """Delete an organization invitation"""
    try:
        # Set audit context for invitation deletion
        request.state.audit_table = "organization_invites"
        request.state.audit_requested_id = invite_id
        request.state.audit_description = f"Deleted invitation for ID: {invite_id}"
        request.state.audit_risk_level = "high"

        # Extract user context
        user_context = await check_permissions(
            current_user=current_user,
            permission_codes=SETTINGS_SYSTEM_MANAGE,
        )

        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }

        # Get invitation details
        invitation_data = await get_invite_by_id(invite_id)

        if not invitation_data:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if not user_context.organization_id == invitation_data["organization_id"]:
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        await delete_invite(invite_id)

        return success_response(
            request=request,
            message_key="invitations.success.invitation_deleted",
            custom_code=CustomStatusCode.DELETED,
            status_code=http_status.HTTP_200_OK,
        )

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error deleting organization invitation - Error: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error
