"""
Verification Codes API Module

This module provides API endpoints for verification code operations.
Includes send and verify functionality with proper error handling.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.responses import JSONResponse

# Utility imports
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    SendVerificationCodeResponse,
    VerifyVerificationCodeRequest,
    VerifyVerificationCodeResponse,
    VerificationType,
)

# App instance
from apps.user_service.app.app_instance import limiter

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
    create_verification_code,
    get_verification_code_by_id,
    get_recent_verification_codes,
    update_verification_code,
    MAX_ATTEMPT_VERIFICATION,
    OTP_ENABLED,
    DEFAULT_OTP,
    VERIFICATION_ATTEMPT_WINDOW_HOURS,
)

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.email_utils import send_verification_code_email

# Create router for verification code endpoints
router = APIRouter(prefix="/v1/verification-code", tags=["Verification Codes"])

# Initialize logger
logger = get_logger("verification-codes-api")

# Environment variables
VERIFICATION_CODE_EXPIRY_MINUTES = int(os.getenv("VERIFICATION_CODE_EXPIRY_MINUTES", "10"))


def get_optional_user(request: Request) -> Optional[dict]:
    """
    Get user from auth if available, return None if not authenticated.
    This allows endpoints to work with or without authentication.
    """
    # Check if user exists in request state first
    user = getattr(request.state, "user", None)
    if not user:
        return None
    
    # Try to get user from auth (validates token and sets audit context)
    try:
        return get_user_from_auth(request)
    except Exception:
        # Return None if authentication fails (allows optional auth)
        return None


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address from request.
    
    This function handles various proxy scenarios and extracts the real client IP.
    """
    # Check for forwarded headers (common with proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain
        return forwarded_for.split(",")[0].strip()
    
    # Check for real IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Fallback to client host
    return request.client.host if request.client else "unknown"


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/send", response_model=SendVerificationCodeResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def send_verification_code(
    request: Request,
    data: SendVerificationCodeRequest,
    current_user: Optional[dict] = Depends(get_optional_user)
):
    """
    Send verification code endpoint
    
    Creates a verification code for email or phone number verification.
    
    Args:
        request: FastAPI request object
        data: SendVerificationCodeRequest containing type and email/phoneNumber
        current_user: Optional authenticated user
    
    Returns:
        SendVerificationCodeResponse: Verification ID and expiry time
    
    Raises:
        HTTPException: 400 for validation errors, 429 for rate limiting, 500 for other errors
    """
    try:
        # Determine the input value based on type
        if data.type == VerificationType.EMAIL:
            given_input = data.email
            triggered_text = data.email
        else:  # PHONE_NUMBER
            given_input = data.phoneNumber
            triggered_text = data.phoneNumber
        
        # Check for recent verification codes to count attempts
        recent_codes = await get_recent_verification_codes(
            type_text=data.type.value,
            given_input=given_input,
            limit=MAX_ATTEMPT_VERIFICATION,
            window_hours=VERIFICATION_ATTEMPT_WINDOW_HOURS
        )
        
        # Count attempts based on time window (default: 24 hours = per day)
        # Count all unverified codes within the time window
        unverified_count = sum(
            1 for code in recent_codes
            if not code.get("verified", False)
        )
        
        # Check if max attempts reached
        if unverified_count >= MAX_ATTEMPT_VERIFICATION:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Maximum verification attempts ({MAX_ATTEMPT_VERIFICATION}) reached. Please try again later."
            )
        
        # Get user ID if authenticated
        user_id = current_user.get("id") if current_user else None
        
        # Get client IP address
        ip_address = get_client_ip(request)
        
        # Create verification code
        verification_record = await create_verification_code(
            type_text=data.type.value,
            given_input=given_input,
            triggered_text=triggered_text,
            user_id=user_id,
            ip_address=ip_address
        )
        
        # Send verification code email if type is EMAIL
        if data.type == VerificationType.EMAIL:
            verification_code = verification_record.get("verification_code")
            try:
                email_sent = send_verification_code_email(
                    email=given_input,
                    otp_code=verification_code,
                    expiry_minutes=VERIFICATION_CODE_EXPIRY_MINUTES
                )
                if not email_sent:
                    logger.warning(f"Failed to send verification code email to {given_input}")
                    # Note: We don't fail the operation if email fails, code is still created
            except Exception as email_error:
                logger.error(f"Error sending verification code email: {str(email_error)}")
                # Note: We don't fail the operation if email fails, code is still created
        
        logger.info(f"Verification code sent for {data.type.value}: {given_input}")
        
        # Return response
        return SendVerificationCodeResponse(
            verificationId=verification_record["id"],
            expiryAt=verification_record["expiry_at"],
            message="Verification code sent successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending verification code: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while sending verification code"
        ) from e


@router.post("/verify", response_model=VerifyVerificationCodeResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_verification_code(
    request: Request,
    data: VerifyVerificationCodeRequest,
    current_user: Optional[dict] = Depends(get_optional_user)
):
    """
    Verify verification code endpoint
    
    Verifies a verification code for email or phone number.
    
    Args:
        request: FastAPI request object
        data: VerifyVerificationCodeRequest containing verification ID, code, and email/phoneNumber
        current_user: Optional authenticated user
    
    Returns:
        VerifyVerificationCodeResponse: Verification result
    
    Raises:
        HTTPException: 400 for validation errors, 404 for not found, 429 for rate limiting, 500 for other errors
    """
    try:
        # Get verification code record
        verification_record = await get_verification_code_by_id(data.verificationId)
        
        if not verification_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Verification code not found"
            )
        
        # Check if already verified
        if verification_record.get("verified", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has already been verified"
            )
        
        # Check expiry
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        expiry_at = verification_record.get("expiry_at", 0)
        
        if expiry_at < current_time_ms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired"
            )
        
        # Validate given input matches
        if data.type == VerificationType.EMAIL:
            given_input = data.email
        else:  # PHONE_NUMBER
            given_input = data.phoneNumber
        
        stored_given_input = verification_record.get("given_input")
        if stored_given_input != given_input:
            logger.warning(
                f"Given input mismatch for verification {data.verificationId}: "
                f"stored='{stored_given_input}', provided='{given_input}'"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Given input '{given_input}' does not match the verification record. Expected: '{stored_given_input}'"
            )
        
        # Get existing attempts
        attempts = verification_record.get("attempts", [])
        if not isinstance(attempts, list):
            attempts = []
        
        # Increment attempt count
        max_attempt = len(attempts) + 1
        
        # Create attempt record
        attempt_record = {
            "entered_value": data.verificationCode,
            "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000),
            "matched": False,
            "success": False
        }
        
        # Check if code matches
        stored_code = verification_record.get("verification_code")
        
        # Match only if entered code exactly equals stored code
        # When OTP_ENABLED=false, the stored code is already DEFAULT_OTP, so this will match
        # When OTP_ENABLED=true, the stored code is random, so only exact match works
        code_matched = (data.verificationCode == stored_code)
        
        # Update attempt record
        attempt_record["matched"] = code_matched
        attempt_record["success"] = code_matched
        
        # Add attempt to attempts array
        attempts.append(attempt_record)
        
        # Update verification record
        verified = code_matched
        
        # Update database
        await update_verification_code(
            verification_id=data.verificationId,
            verified=verified,
            attempts=attempts
        )
        
        # If not matched, reject with attempt count message
        if not code_matched:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid verification code. Attempt {max_attempt} of {MAX_ATTEMPT_VERIFICATION}."
            )
        
        logger.info(f"Verification code verified successfully: {data.verificationId}")
        
        # Return success response
        return VerifyVerificationCodeResponse(
            verified=True,
            message="Verification code verified successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying verification code: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while verifying verification code"
        ) from e

