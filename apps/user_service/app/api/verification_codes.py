"""Verification Codes API Module

This module provides API endpoints for verification code operations.
Includes send and verify functionality with proper error handling.
All business logic is delegated to VerificationCodeService.
"""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status

# App instance
from apps.user_service.app.app_instance import limiter

# Utility imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_uow

# Schema imports
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    SendVerificationCodeResponse,
    VerifyVerificationCodeRequest,
    VerifyVerificationCodeResponse,
)

# Business logic imports
from apps.user_service.app.services.verification_code_service import (
    VerificationCodeService,
)
from apps.user_service.app.utils.common_utils import handle_api_exceptions

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.http_exceptions import InternalServerErrorException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for verification code endpoints
router = APIRouter(prefix="/verification-code", tags=["Verification Codes"])

# Initialize logger
logger = get_logger("verification-codes-api")


def get_optional_user(request: Request) -> dict | None:
    """Get user from auth if available, return None if not authenticated.
    Allows endpoints to work with or without authentication.

    Args:
        request: FastAPI request object

    Returns:
        User dict if authenticated, None otherwise
    """
    user = getattr(request.state, "user", None)
    if not user:
        return None

    return get_user_from_auth(request)


@handle_api_exceptions("send verification code")
@router.post(
    "/send",
    response_model=SendVerificationCodeResponse,
    status_code=http_status.HTTP_200_OK,
    description="Send verification code endpoint",
    summary="Send verification code endpoint",
    responses={
        http_status.HTTP_200_OK: {"description": "Verification code sent successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="verification_codes",
    category="VERIFICATION_CODE_SEND",
)
async def send_verification_code(
    request: Request,
    data: SendVerificationCodeRequest,
    current_user: dict | None = Depends(get_optional_user),
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Send verification code endpoint for email or phone number verification.

    Delegates all business logic to VerificationCodeService.
    """
    try:
        # Initialize service with database connection
        verification_service = VerificationCodeService(db_connection=db_connection)

        # Call service method to handle business logic
        result = await verification_service.send_verification_code(
            request=request,
            data=data,
            current_user=current_user,
        )

        return success_response(
            request=request,
            message_key="verification_codes.success.verification_code_sent",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=SendVerificationCodeResponse(
                verification_id=result["verification_id"],
                expiryAt=result["expiryAt"],
                attemptsLeft=result["attemptsLeft"],
            ),
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("Error sending verification code: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e


@router.post(
    "/verify",
    response_model=VerifyVerificationCodeResponse,
    description="Verify verification code endpoint",
    summary="Verify verification code endpoint",
    responses={
        http_status.HTTP_200_OK: {"description": "Verification code verified successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="verification_codes",
    category="VERIFICATION_CODE_VERIFY",
)
@handle_api_exceptions("verify verification code")
async def verify_verification_code(
    request: Request,
    data: VerifyVerificationCodeRequest,
    current_user: dict | None = Depends(get_optional_user),
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Verify verification code endpoint.

    Delegates all business logic to VerificationCodeService.
    """
    try:
        # Initialize service with database connection
        verification_service = VerificationCodeService(db_connection=db_connection)

        # Call service method to handle business logic
        result = await verification_service.verify_verification_code(
            request=request,
            data=data,
            current_user=current_user,
        )

        return success_response(
            request=request,
            message_key="verification_codes.success.verification_code_verified",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=result,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("Error verifying verification code: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e
