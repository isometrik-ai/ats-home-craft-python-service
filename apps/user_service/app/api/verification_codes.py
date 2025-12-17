"""Verification Codes API Module

This module provides API endpoints for verification code operations.
Includes send and verify functionality with proper error handling.
"""

import ipaddress
import os
import time
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from supabase import create_async_client
from supabase.lib.client_options import AsyncClientOptions
from supabase_auth.types import Session as SupabaseSession

# App instance
from apps.user_service.app.app_instance import limiter

# Utility imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    SendVerificationCodeResponse,
    VerificationTrigger,
    VerificationType,
    VerifyVerificationCodeRequest,
    VerifyVerificationCodeResponse,
)
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_auth_user_by_email,
)

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
    MAX_ATTEMPT_VERIFICATION,
    VERIFICATION_ATTEMPT_WINDOW_HOURS,
    create_verification_code,
    get_recent_verification_codes,
    get_verification_code_by_id,
    update_verification_code,
)
from libs.shared_db.supabase_db.admin_operations.user import (
    get_user_by_id,
)
from libs.shared_db.supabase_db.db import get_supabase_admin_client

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.email_utils import send_verification_code_email
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GoneException,
    InternalServerErrorException,
    TooManyRequestsException,
    UnauthorizedException,
)
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for verification code endpoints
router = APIRouter(prefix="/verification-code", tags=["Verification Codes"])

# Initialize logger
logger = get_logger("verification-codes-api")

# Environment variables
VERIFICATION_CODE_EXPIRY_MINUTES = int(os.getenv("VERIFICATION_CODE_EXPIRY_MINUTES", "10"))


def get_optional_user(request: Request) -> dict | None:
    """Get user from auth if available, return None if not authenticated.
    Allows endpoints to work with or without authentication.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return None

    return get_user_from_auth(request)


def _sanitize_ip(candidate: str | None) -> str | None:
    """Validate and sanitize IP address strings.
    Returns a valid IP string or None if invalid."""
    if not candidate:
        return None
    candidate = candidate.split(",")[0].strip()
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Extract client IP address from request.
    Ensures that the returned value is a valid IPv4/IPv6 string to avoid
    database errors when storing as inet type.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if ip := _sanitize_ip(forwarded_for):
        return ip

    real_ip = request.headers.get("X-Real-IP")
    if ip := _sanitize_ip(real_ip):
        return ip

    client_host = request.client.host if request.client else None
    if client_host:
        sanitized_host = _sanitize_ip(client_host)
        return sanitized_host if sanitized_host else client_host

    return "unknown"


async def _validate_email_for_update(email: str, user_id: str, current_user_email: str) -> None:
    """Validate email for authenticated user update.

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
        raise BadRequestException(
            message_key="verification_codes.errors.email_same_as_current",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Check if email already exists for another user
    existing_user = await get_auth_user_by_email(email)
    if existing_user:
        existing_user_id = existing_user.id if hasattr(existing_user, "id") else None
        if existing_user_id and existing_user_id != user_id:
            raise ConflictException(
                message_key="verification_codes.errors.email_already_registered",
                custom_code=CustomStatusCode.CONFLICT,
            )


def _normalize_phone(phone: str) -> str:
    """Normalize phone number by removing '+' sign for comparison.
    Supabase phone field may not preserve '+' sign, so we normalize for matching.

    Args:
        phone: Phone number to normalize

    Returns:
        Normalized phone number (without '+')
    """
    if not phone:
        return phone
    # Remove '+' sign if present for comparison
    return phone.lstrip("+")


async def _check_phone_exists_for_other_user(phone: str, user_id: str) -> None:
    """Check if phone number already exists for another user.
    Checks the actual phone field in auth.users, not user_metadata.
    Normalizes phone numbers (removes '+') for comparison.

    Args:
        phone: Phone number to check
        user_id: Current user ID to exclude from check

    Raises:
        HTTPException: If phone is already registered with another account
    """
    # Normalize the input phone for comparison
    normalized_input_phone = _normalize_phone(phone)

    supabase = await get_supabase_admin_client()
    users_list = await supabase.auth.admin.list_users(per_page=1000)

    for user in users_list:
        if user.id != user_id:
            # Check the actual phone field, not user_metadata
            user_phone = None
            if hasattr(user, "phone") and user.phone:
                user_phone = user.phone

            # Normalize both phones for comparison
            if user_phone:
                normalized_user_phone = _normalize_phone(user_phone)
                if normalized_user_phone == normalized_input_phone:
                    raise ConflictException(
                        message_key="verification_codes.errors.phone_already_registered",
                        custom_code=CustomStatusCode.CONFLICT,
                    )


async def _check_auth_user_exists_by_phone(phone: str) -> bool:
    """Check if phone number already exists in auth.users.
    Checks the actual phone field in auth.users, not user_metadata.
    Normalizes phone numbers (removes '+') for comparison.

    Args:
        phone: Phone number to check

    Returns:
        True if phone exists in auth.users, False otherwise
    """
    # Normalize the input phone for comparison
    normalized_input_phone = _normalize_phone(phone)

    supabase = await get_supabase_admin_client()
    users_list = await supabase.auth.admin.list_users(per_page=1000)

    for user in users_list:
        # Check the actual phone field, not user_metadata
        user_phone = None
        if hasattr(user, "phone") and user.phone:
            user_phone = user.phone

        # Normalize both phones for comparison
        if user_phone:
            normalized_user_phone = _normalize_phone(user_phone)
            if normalized_user_phone == normalized_input_phone:
                return True
    return False


async def _validate_phone_for_update(phone: str, user_id: str) -> None:
    """Validate phone number for authenticated user update.
    Normalizes phone numbers (removes '+') for comparison.

    Args:
        phone: Phone number to validate
        user_id: Current user ID

    Raises:
        BadRequestException: If phone is same as current or already registered
    """
    user_data = await get_user_by_id(user_id)
    if user_data and hasattr(user_data, "user") and user_data.user:
        current_user_phone = None
        if hasattr(user_data.user, "phone") and user_data.user.phone:
            current_user_phone = user_data.user.phone

        normalized_input_phone = _normalize_phone(phone)
        normalized_current_phone = (
            _normalize_phone(current_user_phone) if current_user_phone else None
        )

        # Check if entered phone is same as current phone (after normalization)
        if normalized_current_phone and normalized_input_phone == normalized_current_phone:
            raise BadRequestException(
                message_key="verification_codes.errors.phone_same_as_current",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Check if phone already exists for another user
        await _check_phone_exists_for_other_user(phone, user_id)


def _validate_verification_record(
    verification_record: dict, data: VerifyVerificationCodeRequest
) -> str:
    """Validate verification record and return given_input.

    Args:
        verification_record (dict): The verification code record
        data (VerifyVerificationCodeRequest): Request data containing type and email/phoneNumber

    Returns:
        str: The given_input value (email or phoneNumber)

    Raises:
        BadRequestException: If validation fails
        GoneException: If verification code has expired
    """
    if not verification_record:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_not_found",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Check if already verified
    if verification_record.get("verified", False):
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_not_verified",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Check expiry
    current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    expiry_at = verification_record.get("expiry_at", 0)

    if expiry_at < current_time_ms:
        raise GoneException(
            message_key="verification_codes.errors.verification_code_expired",
            custom_code=CustomStatusCode.GONE,
        )

    # Validate given input matches
    if data.type == VerificationType.EMAIL:
        given_input = data.email
    else:  # PHONE_NUMBER
        given_input = data.phoneNumber

    stored_given_input = verification_record.get("given_input")
    if stored_given_input != given_input:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_invalid",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    return given_input


def _check_verification_code_ownership(
    verification_record: dict, current_user: dict | None
) -> None:
    """Check if authenticated user owns the verification code.

    Args:
        verification_record (dict): The verification code record
        current_user (dict | None): Optional authenticated user

    Raises:
        ForbiddenException: If user doesn't own the verification code
        BadRequestException: If user doesn't own the verification code
    """
    stored_user_id = verification_record.get("user_id")
    if not current_user:
        return

    # JWT tokens use "sub" for user ID
    current_user_id = current_user.get("sub")

    # If verification code has a user_id, it must match the current user
    if stored_user_id and current_user_id and stored_user_id != current_user_id:
        raise ForbiddenException(
            message_key="verification_codes.errors.verification_code_ownership_mismatch",
            custom_code=CustomStatusCode.FORBIDDEN,
        )


async def _verify_code_and_update_record(
    verification_record: dict, verification_code: str, verification_id: str
) -> bool:
    """Verify the code and update the verification record.

    Note: There is no limit on verification attempts. Users can verify as many times as they want.

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

    # Create attempt record
    attempt_record = {
        "entered_value": verification_code,
        "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000),
        "matched": False,
        "success": False,
    }

    # Check if code matches
    stored_code = verification_record.get("verification_code")
    code_matched = verification_code == stored_code

    # Update attempt record
    attempt_record["matched"] = code_matched
    attempt_record["success"] = code_matched

    # Add attempt to attempts array
    attempts.append(attempt_record)

    # Update verification record
    verified = code_matched

    # Update database
    await update_verification_code(
        verification_id=verification_id, verified=verified, attempts=attempts
    )

    # If not matched, reject with simple error message (no attempt limit)
    if not code_matched:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_invalid",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    return code_matched


async def _get_supabase_client_with_token(access_token: str):
    """Create a Supabase client with user's access token.

    Args:
        access_token: User's JWT access token

    Returns:
        Supabase AsyncClient configured with user's token
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_anon_key:
        raise InternalServerErrorException(
            message_key="errors.missing_configuration",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    # Create client with user's access token in headers

    options = AsyncClientOptions(headers={"Authorization": f"Bearer {access_token}"})

    client = await create_async_client(supabase_url, supabase_anon_key, options)
    return client


async def _update_email_or_phone(
    user_id: str, given_input: str, triggered_text: str, access_token: str
) -> tuple[bool, bool]:
    """Update email or phone number using Supabase auth.update_user() with user's token.

    This function uses the authenticated user's token to update their own email/phone,
    following Supabase's recommended approach for user updates.

    Args:
        user_id: User ID to update
        given_input: Email or phone number to set
        triggered_text: The trigger type from verification record
        access_token: User's JWT access token for authentication

    Returns:
        Tuple of (email_updated, phone_updated)
    """
    # pylint: disable=too-complex
    email_updated = False
    phone_updated = False

    try:
        # Create Supabase client with user's access token
        supabase = await _get_supabase_client_with_token(access_token)

        # Get user first to validate token and get user info
        user_response = await supabase.auth.get_user(access_token)
        if not user_response or not user_response.user:
            raise UnauthorizedException(
                message_key="errors.invalid_access_token_or_user_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )

        # Decode JWT to get expiration time
        supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
        if not supabase_jwt_secret:
            raise InternalServerErrorException(
                message_key="errors.missing_configuration",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        if not supabase_jwt_secret:
            raise InternalServerErrorException(
                message_key="errors.missing_configuration",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        decoded = jwt.decode(
            access_token,
            supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_exp": False},
        )
        exp = decoded.get("exp", 0)
        current_time = int(time.time())

        if 0 < exp <= current_time:
            raise UnauthorizedException(
                message_key="errors.access_token_expired",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )

        # Calculate expires_in
        expires_in = max(exp - current_time, 3600) if exp > 0 else 3600
        expires_at = exp if exp > 0 else current_time + expires_in

        user_obj = user_response.user

        # Create session with access token and a placeholder refresh token
        # The refresh token won't be used if the access token is still valid
        session = SupabaseSession(
            access_token=access_token,
            # Placeholder - won't be validated
            refresh_token="placeholder_refresh_token",
            expires_in=expires_in,
            expires_at=expires_at,
            token_type="bearer",
            user=user_obj,
        )

        # Save session directly to storage and set in-memory session
        # This mimics what _save_session() does internally
        await supabase.auth.set_session(session)

        if triggered_text == VerificationTrigger.EMAIL_UPDATE.value:
            admin_supabase = await get_supabase_admin_client()
            current_time = datetime.now(timezone.utc).isoformat()
            user_data = await admin_supabase.auth.admin.get_user_by_id(user_id)
            existing_metadata = user_data.user.user_metadata or {}
            updated_metadata = {**existing_metadata, "email": given_input}

            response = await admin_supabase.auth.admin.update_user_by_id(
                user_id, {"email": given_input, "email_confirmed_at": current_time}
            )

            await admin_supabase.auth.admin.update_user_by_id(
                user_id,
                {"user_metadata": updated_metadata},
            )

            updated_user = await admin_supabase.auth.admin.get_user_by_id(user_id)

            if response and response.user:
                email_updated = True
                updated_user = await admin_supabase.auth.admin.get_user_by_id(user_id)
                if updated_user and updated_user.user:
                    if updated_user.user.email != given_input:
                        # Also update raw_user_meta_data on retry
                        await admin_supabase.auth.admin.update_user_by_id(
                            user_id,
                            {
                                "email": given_input,
                                "email_confirmed_at": current_time,
                                "user_metadata": updated_metadata,
                            },
                        )
            else:
                raise InternalServerErrorException(
                    message_key="errors.internal_server_error",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                )

        elif triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value:
            admin_supabase = await get_supabase_admin_client()
            current_time = datetime.now(timezone.utc).isoformat()
            user_data = await admin_supabase.auth.admin.get_user_by_id(user_id)
            existing_metadata = {}
            if user_data and user_data.user:
                existing_metadata = user_data.user.user_metadata or {}

            updated_metadata = dict(existing_metadata)
            updated_metadata["phone"] = given_input

            response = await admin_supabase.auth.admin.update_user_by_id(
                user_id,
                {"phone": given_input, "phone_confirmed_at": current_time},
            )

            await admin_supabase.auth.admin.update_user_by_id(
                user_id,
                {"user_metadata": updated_metadata},
            )

            if response and response.user:
                phone_updated = True
                # Verify the phone was actually updated in both places
                updated_phone = response.user.phone if hasattr(response.user, "phone") else None
                updated_metadata_phone = None
                if hasattr(response.user, "user_metadata") and response.user.user_metadata:
                    updated_metadata_phone = response.user.user_metadata.get("phone")

                # Verify both phone field and user_metadata were updated
                if updated_phone != given_input or updated_metadata_phone != given_input:
                    # Also update raw_user_meta_data on retry
                    await admin_supabase.auth.admin.update_user_by_id(
                        user_id,
                        {
                            "phone": given_input,
                            "phone_confirmed_at": current_time,
                            "user_metadata": updated_metadata,
                        },
                    )
                    await (
                        admin_supabase.table("organization_members")
                        .update(
                            {
                                "phone": given_input,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        .eq("user_id", user_id)
                        .execute()
                    )

        return email_updated, phone_updated
    except Exception as e:
        logger.error("Error updating phone: %s", str(e), exc_info=True)
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e


def _determine_triggered_text(data: SendVerificationCodeRequest, current_user: dict | None) -> str:
    """Determine the triggered_text based on authentication status and type.

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
        # PHONE_NUMBER
        return VerificationTrigger.PHONE_NUMBER_UPDATE.value
    # Unauthenticated user - signup operation
    if data.type == VerificationType.EMAIL:
        return VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    # PHONE_NUMBER
    return VerificationTrigger.SIGNUP_PHONE_VERIFICATION.value


async def _validate_authenticated_user_input(
    data: SendVerificationCodeRequest, current_user: dict | None
) -> tuple[str, str]:
    """Validate input for authenticated user and return user_id and triggered_text.

    Args:
        data: Request data containing type and email/phoneNumber
        current_user: Authenticated user dict

    Returns:
        Tuple of (user_id, triggered_text)

    Raises:
        BadRequestException: If validation fails
    """
    user_id = current_user.get("sub") if current_user else None

    # Get current email from Supabase auth (actual email field, not user_metadata)
    current_user_email = ""
    if user_id:
        user_data = await get_user_by_id(user_id)
        if user_data and hasattr(user_data, "user") and user_data.user:
            # Get the actual email field, not user_metadata
            if hasattr(user_data.user, "email") and user_data.user.email:
                current_user_email = user_data.user.email.lower()

    if data.type == VerificationType.EMAIL:
        await _validate_email_for_update(data.email, user_id, current_user_email)
        triggered_text = VerificationTrigger.EMAIL_UPDATE.value
    else:  # PHONE_NUMBER
        await _validate_phone_for_update(data.phoneNumber, user_id)
        triggered_text = VerificationTrigger.PHONE_NUMBER_UPDATE.value

    return user_id, triggered_text


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
):
    """Send verification code endpoint for email or phone number verification"""
    # pylint: disable=too-many-nested-blocks, too-complex, too-many-branches
    try:
        # Determine the input value based on type
        given_input = data.email if data.type == VerificationType.EMAIL else data.phoneNumber

        # Validate and determine triggered_text based on authentication status
        if data.verification_method:
            # Use verification_method as triggered_text if provided
            triggered_text = data.verification_method
            if current_user:
                user_id, _ = await _validate_authenticated_user_input(data, current_user)
            else:
                if data.verification_method.upper() != "TWO_FACTOR_AUTH":
                    if data.type == VerificationType.EMAIL:
                        existing_auth_user = await get_auth_user_by_email(data.email)
                        if existing_auth_user:
                            raise BadRequestException(
                                message_key="verification_codes.errors.email_already_registered",
                                custom_code=CustomStatusCode.BAD_REQUEST,
                            )
                    else:  # PHONE_NUMBER
                        phone_exists = await _check_auth_user_exists_by_phone(data.phoneNumber)
                        if phone_exists:
                            raise BadRequestException(
                                message_key="verification_codes.errors.phone_already_registered",
                                custom_code=CustomStatusCode.BAD_REQUEST,
                            )
                user_id = None
        else:
            if current_user:
                user_id, triggered_text = await _validate_authenticated_user_input(
                    data, current_user
                )
            else:
                if data.type == VerificationType.EMAIL:
                    existing_auth_user = await get_auth_user_by_email(data.email)
                    if existing_auth_user:
                        raise BadRequestException(
                            message_key="verification_codes.errors.email_already_registered",
                            custom_code=CustomStatusCode.BAD_REQUEST,
                        )
                else:  # PHONE_NUMBER
                    phone_exists = await _check_auth_user_exists_by_phone(data.phoneNumber)
                    if phone_exists:
                        raise BadRequestException(
                            message_key="verification_codes.errors.phone_already_registered",
                            custom_code=CustomStatusCode.BAD_REQUEST,
                        )

                user_id = None
                triggered_text = _determine_triggered_text(data, current_user)

        recent_codes = await get_recent_verification_codes(
            type_text=data.type.value,
            given_input=given_input,
            limit=MAX_ATTEMPT_VERIFICATION,
            window_hours=VERIFICATION_ATTEMPT_WINDOW_HOURS,
        )

        unverified_count = sum(1 for code in recent_codes if not code.get("verified", False))

        attempts_left = MAX_ATTEMPT_VERIFICATION - unverified_count

        if unverified_count >= MAX_ATTEMPT_VERIFICATION:
            raise TooManyRequestsException(
                message_key="verification_codes.errors.maximum_send_otp_attempts_reached",
                custom_code=CustomStatusCode.RATE_LIMIT_EXCEEDED,
                params={
                    "max_attempts": MAX_ATTEMPT_VERIFICATION,
                },
            )

        ip_address = get_client_ip(request)

        verification_record = await create_verification_code(
            type_text=data.type.value,
            given_input=given_input,
            triggered_text=triggered_text,
            user_id=user_id,
            ip_address=ip_address,
        )

        if data.type == VerificationType.EMAIL:
            verification_code = verification_record.get("verification_code")
            send_verification_code_email(
                email=given_input,
                otp_code=verification_code,
                expiry_minutes=VERIFICATION_CODE_EXPIRY_MINUTES,
            )

        attempts_left_after = attempts_left - 1

        return success_response(
            request=request,
            message_key="verification_codes.success.verification_code_sent",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=SendVerificationCodeResponse(
                verification_id=verification_record["id"],
                expiryAt=verification_record["expiry_at"],
                attemptsLeft=attempts_left_after,
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
):
    """Verify verification code endpoint"""
    try:
        verification_record = await get_verification_code_by_id(data.verification_id)
        given_input = _validate_verification_record(verification_record, data)
        _check_verification_code_ownership(verification_record, current_user)

        await _verify_code_and_update_record(
            verification_record, data.verification_code, data.verification_id
        )

        triggered_text = verification_record.get("triggered_text", "")
        stored_user_id = verification_record.get("user_id")

        user_id = None
        if current_user:
            user_id = current_user.get("sub")
        elif stored_user_id:
            user_id = stored_user_id

        # Update email/phone if needed (only with token)
        if (
            current_user
            and user_id
            and triggered_text
            in [
                VerificationTrigger.EMAIL_UPDATE.value,
                VerificationTrigger.PHONE_NUMBER_UPDATE.value,
            ]
        ):
            # Get access token from request state (set by JWT middleware)
            access_token = getattr(request.state, "access_token", None)
            if not access_token:
                raise UnauthorizedException(
                    message_key="errors.unauthorized",
                    custom_code=CustomStatusCode.UNAUTHORIZED,
                )

            await _update_email_or_phone(user_id, given_input, triggered_text, access_token)

        return success_response(
            request=request,
            message_key="verification_codes.success.verification_code_verified",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data={
                "verified": True,
            },
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("Error verifying verification code: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e
