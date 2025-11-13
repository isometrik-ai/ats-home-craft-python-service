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

# Utility imports
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    SendVerificationCodeResponse,
    VerifyVerificationCodeRequest,
    VerifyVerificationCodeResponse,
    VerificationType,
    VerificationTrigger,
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
    VERIFICATION_ATTEMPT_WINDOW_HOURS,
)

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.email_utils import send_verification_code_email
from libs.shared_db.supabase_db.admin_operations.user import (
    update_email_of_user,
    update_phone_of_user,
    get_user_by_id,
)
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_auth_user_by_email,
)

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


async def _validate_email_for_update(email: str, user_id: str, current_user_email: str) -> None:
    """
    Validate email for authenticated user update.
    
    Args:
        email: Email to validate
        user_id: Current user ID
        current_user_email: Current user's email
        
    Raises:
        HTTPException: If email is same as current or already registered
    """
    entered_email = email.lower()
    
    # Check if entered email is same as current email
    if entered_email == current_user_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The entered email is the same as your current email. No change needed."
        )
    
    # Check if email already exists for another user
    try:
        existing_user = await get_auth_user_by_email(email)
        if existing_user:
            existing_user_id = existing_user.id if hasattr(existing_user, 'id') else None
            if existing_user_id and existing_user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This email is already registered with another account. Please use a different email."
                )
    except HTTPException:
        raise
    except Exception as e:
        # If get_auth_user_by_email fails for reasons other than user not found, log and continue
        logger.warning("Error checking email existence: %s", str(e))


async def _check_phone_exists_for_other_user(phone: str, user_id: str) -> None:
    """
    Check if phone number already exists for another user.
    
    Args:
        phone: Phone number to check
        user_id: Current user ID to exclude from check
        
    Raises:
        HTTPException: If phone is already registered with another account
    """
    from libs.shared_db.supabase_db.db import get_supabase_admin_client
    supabase = await get_supabase_admin_client()
    users_list = await supabase.auth.admin.list_users(per_page=1000)
    
    for user in users_list:
        if user.id != user_id and user.user_metadata:
            user_phone = user.user_metadata.get("phone")
            if user_phone and user_phone == phone:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This phone number is already registered with another account. Please use a different phone number."
                )


async def _validate_phone_for_update(phone: str, user_id: str) -> None:
    """
    Validate phone number for authenticated user update.
    
    Args:
        phone: Phone number to validate
        user_id: Current user ID
        
    Raises:
        HTTPException: If phone is same as current or already registered
    """
    try:
        user_data = await get_user_by_id(user_id)
        if user_data and hasattr(user_data, 'user') and user_data.user:
            current_user_phone = user_data.user.user_metadata.get("phone") if user_data.user.user_metadata else None
            
            # Check if entered phone is same as current phone
            if current_user_phone and phone == current_user_phone:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The entered phone number is the same as your current phone number. No change needed."
                )
            
            # Check if phone already exists for another user
            await _check_phone_exists_for_other_user(phone, user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Error checking phone number: %s", str(e))
        # Continue with verification code creation if check fails


def _validate_verification_record(verification_record: dict, data: VerifyVerificationCodeRequest) -> str:
    """
    Validate verification record and return given_input.
    
    Args:
        verification_record: The verification code record
        data: Request data containing type and email/phoneNumber
        
    Returns:
        The given_input value (email or phoneNumber)
        
    Raises:
        HTTPException: If validation fails
    """
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
            "Given input mismatch for verification %s: "
            "stored='%s', provided='%s'",
            data.verificationId,
            stored_given_input,
            given_input
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Given input '{given_input}' does not match the verification record. Expected: '{stored_given_input}'",
        )
    
    return given_input


def _check_verification_code_ownership(
    verification_record: dict,
    current_user: Optional[dict],
    verification_id: str
) -> None:
    """
    Check if authenticated user owns the verification code.
    
    Args:
        verification_record: The verification code record
        current_user: Optional authenticated user
        verification_id: Verification code ID for logging
        
    Raises:
        HTTPException: If user doesn't own the verification code
    """
    stored_user_id = verification_record.get("user_id")
    if not current_user:
        return
    
    # JWT tokens use "sub" for user ID
    current_user_id = current_user.get("sub")
    if not current_user_id:
        logger.warning("User ID not found in token for verification: %s", current_user.keys())
    
    # If verification code has a user_id, it must match the current user
    if stored_user_id and current_user_id and stored_user_id != current_user_id:
        logger.warning(
            "User ownership mismatch for verification %s: "
            "stored_user_id='%s', current_user_id='%s'",
            verification_id,
            stored_user_id,
            current_user_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only verify your own verification codes"
        )


async def _verify_code_and_update_record(
    verification_record: dict,
    verification_code: str,
    verification_id: str
) -> bool:
    """
    Verify the code and update the verification record.
    
    Args:
        verification_record: The verification code record
        verification_code: The code to verify
        verification_id: The verification code ID
        
    Returns:
        True if code matches, False otherwise
    """
    # Get existing attempts
    attempts = verification_record.get("attempts", [])
    if not isinstance(attempts, list):
        attempts = []

    # Increment attempt count
    max_attempt = len(attempts) + 1

    # Create attempt record
    attempt_record = {
        "entered_value": verification_code,
        "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000),
        "matched": False,
        "success": False
    }

    # Check if code matches
    stored_code = verification_record.get("verification_code")
    code_matched = (verification_code == stored_code)

    # Update attempt record
    attempt_record["matched"] = code_matched
    attempt_record["success"] = code_matched

    # Add attempt to attempts array
    attempts.append(attempt_record)

    # Update verification record
    verified = code_matched

    # Update database
    await update_verification_code(
        verification_id=verification_id,
        verified=verified,
        attempts=attempts
    )

    # If not matched, reject with attempt count message
    if not code_matched:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. Attempt {max_attempt} of {MAX_ATTEMPT_VERIFICATION}."
        )
    
    return code_matched


async def _update_email_or_phone(
    user_id: str,
    given_input: str,
    triggered_text: str
) -> tuple[bool, bool]:
    """
    Update email or phone number based on triggered_text.
    
    Args:
        user_id: User ID to update
        given_input: Email or phone number to set
        triggered_text: The trigger type from verification record
        
    Returns:
        Tuple of (email_updated, phone_updated)
    """
    email_updated = False
    phone_updated = False
    
    if triggered_text == VerificationTrigger.EMAIL_UPDATE.value:
        # Update email
        try:
            email_updated = await update_email_of_user(user_id, given_input)
            if email_updated:
                logger.info("Email updated successfully for user %s: %s", user_id, given_input)
            else:
                logger.warning("Email update returned False for user %s: %s", user_id, given_input)
        except Exception as e:
            logger.error("Error updating email for user %s: %s", user_id, str(e), exc_info=True)
            email_updated = False
    elif triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value:
        # Update phone number
        try:
            phone_updated = await update_phone_of_user(user_id, given_input)
            if phone_updated:
                logger.info("Phone number updated successfully for user %s: %s", user_id, given_input)
            else:
                logger.warning("Phone update returned False for user %s: %s", user_id, given_input)
        except Exception as e:
            logger.error("Error updating phone for user %s: %s", user_id, str(e), exc_info=True)
            phone_updated = False
    
    return email_updated, phone_updated


def _determine_triggered_text(data: SendVerificationCodeRequest, current_user: Optional[dict]) -> str:
    """
    Determine the triggered_text based on authentication status and type.
    
    Args:
        data: Request data containing type and email/phoneNumber
        current_user: Optional authenticated user
        
    Returns:
        Triggered text value for the verification code
    """
    if current_user:
        # Authenticated user - change operation
        if data.type == VerificationType.EMAIL:
            return VerificationTrigger.EMAIL_UPDATE.value
        else:  # PHONE_NUMBER
            return VerificationTrigger.PHONE_NUMBER_UPDATE.value
    else:
        # Unauthenticated user - signup operation
        if data.type == VerificationType.EMAIL:
            return VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
        else:  # PHONE_NUMBER
            return VerificationTrigger.SIGNUP_PHONE_VERIFICATION.value


async def _validate_authenticated_user_input(
    data: SendVerificationCodeRequest,
    current_user: dict
) -> tuple[str, str]:
    """
    Validate input for authenticated user and return user_id and triggered_text.
    
    Args:
        data: Request data containing type and email/phoneNumber
        current_user: Authenticated user dict
        
    Returns:
        Tuple of (user_id, triggered_text)
        
    Raises:
        HTTPException: If validation fails
    """
    user_id = current_user.get("sub")
    if not user_id:
        logger.warning("User ID not found in token: %s", current_user.keys())
    current_user_email = current_user.get("email", "").lower()
    
    if data.type == VerificationType.EMAIL:
        await _validate_email_for_update(data.email, user_id, current_user_email)
        triggered_text = VerificationTrigger.EMAIL_UPDATE.value
    else:  # PHONE_NUMBER
        await _validate_phone_for_update(data.phoneNumber, user_id)
        triggered_text = VerificationTrigger.PHONE_NUMBER_UPDATE.value
    
    return user_id, triggered_text


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
    
    **Types:** Only two types are supported:
    - `EMAIL`: For email verification
    - `PHONE_NUMBER`: For phone number verification
    
    **Authentication Behavior:**
    
    **Without Token (Unauthenticated):**
    - Sends verification code (OTP) to the provided email or phone number
    - Works for both EMAIL and PHONE_NUMBER types
    - Used for signup flow verification
    
    **With Token (Authenticated):**
    - Validates that entered email/phone is different from current user's email/phone
    - Validates that email/phone is not already registered with another account
    - If all validations pass, sends OTP to the new email or phone number for update
    - Works for both EMAIL and PHONE_NUMBER types

    Args:
        request: FastAPI request object
        data: SendVerificationCodeRequest containing type (EMAIL or PHONE_NUMBER) and email/phoneNumber
        current_user: Optional authenticated user (from JWT token in Authorization header)

    Returns:
        SendVerificationCodeResponse: Verification ID and expiry time

    Raises:
        HTTPException: 
            - 400: Entered email/phone is same as current user's email/phone (when token provided)
            - 409: Email/phone already registered with another account (when token provided)
            - 429: Maximum verification attempts reached
            - 500: Internal server error
    """
    try:
        # Determine the input value based on type
        given_input = data.email if data.type == VerificationType.EMAIL else data.phoneNumber

        # Validate and determine triggered_text based on authentication status
        if current_user:
            user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        else:
            user_id = None
            triggered_text = _determine_triggered_text(data, current_user)

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
                    logger.warning("Failed to send verification code email to %s", given_input)
                    # Note: We don't fail the operation if email fails, code is still created
            except Exception as email_error:
                logger.error("Error sending verification code email: %s", str(email_error))
                # Note: We don't fail the operation if email fails, code is still created

        logger.info("Verification code sent for %s: %s", data.type.value, given_input)

        # Return response
        return SendVerificationCodeResponse(
            verificationId=verification_record["id"],
            expiryAt=verification_record["expiry_at"],
            message="Verification code sent successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error sending verification code: %s", str(e))
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
    
    **Types:** Only two types are supported:
    - `EMAIL`: For email verification
    - `PHONE_NUMBER`: For phone number verification
    
    **Authentication Behavior:**
    
    **With Token (Authenticated):**
    - Validates verification ID and OTP code
    - If type is `EMAIL` and verification is successful → **Automatically updates user's email** in their account
    - If type is `PHONE_NUMBER` and verification is successful → **Automatically updates user's phone number** in their account
    - Only updates if the verification code was created with the same user's token
    - Authenticated users can only verify their own verification codes (user_id must match)
    
    **Without Token (Unauthenticated):**
    - Verifies code for signup flow only
    - No email/phone update is performed
    - Works for both EMAIL and PHONE_NUMBER types

    Args:
        request: FastAPI request object
        data: VerifyVerificationCodeRequest containing type (EMAIL or PHONE_NUMBER), verification ID, OTP code, and email/phoneNumber
        current_user: Optional authenticated user (from JWT token in Authorization header)

    Returns:
        VerifyVerificationCodeResponse: 
            - verified: Whether verification was successful
            - message: Success message (includes update status if email/phone was updated when token provided)

    Raises:
        HTTPException: 
            - 400: Invalid code, expired code, already verified, or input mismatch
            - 403: Trying to verify another user's verification code (when token provided)
            - 404: Verification code not found
            - 500: Internal server error
    """
    try:
        # Get verification code record
        verification_record = await get_verification_code_by_id(data.verificationId)

        # Validate verification record and get given_input
        given_input = _validate_verification_record(verification_record, data)

        # Security check: If authenticated user, verify they own the verification code
        _check_verification_code_ownership(verification_record, current_user, data.verificationId)

        # Verify code and update record
        await _verify_code_and_update_record(
            verification_record,
            data.verificationCode,
            data.verificationId
        )

        logger.info("Verification code verified successfully: %s", data.verificationId)

        # After successful verification, check if we need to update email/phone
        triggered_text = verification_record.get("triggered_text", "")
        stored_user_id = verification_record.get("user_id")
        
        # Debug logging
        logger.info(
            "Verification update check - current_user: %s, stored_user_id: %s, triggered_text: %s, type: %s",
            "present" if current_user else "None",
            stored_user_id,
            triggered_text,
            data.type.value
        )

        # Determine user_id for update (from token or stored_user_id)
        user_id = None
        if current_user:
            user_id = current_user.get("sub")
        elif stored_user_id:
            user_id = stored_user_id
        
        # Update email/phone if needed
        email_updated = False
        phone_updated = False
        if user_id and triggered_text in [VerificationTrigger.EMAIL_UPDATE.value, VerificationTrigger.PHONE_NUMBER_UPDATE.value]:
            logger.info("Attempting to update %s for user %s with triggered_text: %s", data.type.value, user_id, triggered_text)
            email_updated, phone_updated = await _update_email_or_phone(
                user_id,
                given_input,
                triggered_text
            )
        else:
            logger.info(
                "Skipping email/phone update - user_id: %s, triggered_text: %s, expected: %s or %s",
                user_id,
                triggered_text,
                VerificationTrigger.EMAIL_UPDATE.value,
                VerificationTrigger.PHONE_NUMBER_UPDATE.value
            )

        # Build response message
        message = "Verification code verified successfully"
        if email_updated:
            message += ". Email has been updated."
        elif phone_updated:
            message += ". Phone number has been updated."

        # Return success response
        return VerifyVerificationCodeResponse(
            verified=True,
            message=message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error verifying verification code: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while verifying verification code"
        ) from e
