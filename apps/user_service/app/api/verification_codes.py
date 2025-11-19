"""
Verification Codes API Module

This module provides API endpoints for verification code operations.
Includes send and verify functionality with proper error handling.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

import os
import ipaddress
import jwt
import time
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
    get_user_by_id,
)
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_auth_user_by_email,
)
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from supabase import create_async_client
import os

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


def _sanitize_ip(candidate: Optional[str]) -> Optional[str]:
    """
    Validate and sanitize IP address strings.
    Returns a valid IP string or None if invalid.
    """
    if not candidate:
        return None
    candidate = candidate.split(",")[0].strip()
    try:
        # Validate IPv4/IPv6
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        logger.debug("Invalid IP address detected: %s", candidate)
        return None


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address from request.

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

    logger.debug("Unable to determine client IP address; returning 'unknown'")
    return "unknown"


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


def _normalize_phone(phone: str) -> str:
    """
    Normalize phone number by removing '+' sign for comparison.
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
    """
    Check if phone number already exists for another user.
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
            if hasattr(user, 'phone') and user.phone:
                user_phone = user.phone
            
            # Normalize both phones for comparison
            if user_phone:
                normalized_user_phone = _normalize_phone(user_phone)
                if normalized_user_phone == normalized_input_phone:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="This phone number is already registered with another account. Please use a different phone number."
                    )


async def _check_auth_user_exists_by_phone(phone: str) -> bool:
    """
    Check if phone number already exists in auth.users.
    Checks the actual phone field in auth.users, not user_metadata.
    Normalizes phone numbers (removes '+') for comparison.

    Args:
        phone: Phone number to check

    Returns:
        True if phone exists in auth.users, False otherwise
    """
    try:
        # Normalize the input phone for comparison
        normalized_input_phone = _normalize_phone(phone)
        
        supabase = await get_supabase_admin_client()
        users_list = await supabase.auth.admin.list_users(per_page=1000)

        for user in users_list:
            # Check the actual phone field, not user_metadata
            user_phone = None
            if hasattr(user, 'phone') and user.phone:
                user_phone = user.phone
            
            # Normalize both phones for comparison
            if user_phone:
                normalized_user_phone = _normalize_phone(user_phone)
                if normalized_user_phone == normalized_input_phone:
                    return True
        return False
    except Exception as e:
        logger.warning("Error checking phone existence in auth.users: %s", str(e))
        return False


async def _validate_phone_for_update(phone: str, user_id: str) -> None:
    """
    Validate phone number for authenticated user update.
    Normalizes phone numbers (removes '+') for comparison.

    Args:
        phone: Phone number to validate
        user_id: Current user ID

    Raises:
        HTTPException: If phone is same as current or already registered
    """
    try:
        user_data = await get_user_by_id(user_id)
        if user_data and hasattr(user_data, 'user') and user_data.user:
            # Check the actual phone field, not user_metadata
            current_user_phone = None
            if hasattr(user_data.user, 'phone') and user_data.user.phone:
                current_user_phone = user_data.user.phone

            # Normalize both phones for comparison (remove '+' if present)
            normalized_input_phone = _normalize_phone(phone)
            normalized_current_phone = _normalize_phone(current_user_phone) if current_user_phone else None

            # Check if entered phone is same as current phone (after normalization)
            if normalized_current_phone and normalized_input_phone == normalized_current_phone:
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

    # If not matched, reject with simple error message (no attempt limit)
    if not code_matched:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code. Please try again."
        )

    return code_matched


async def _get_supabase_client_with_token(access_token: str):
    """
    Create a Supabase client with user's access token.
    
    Args:
        access_token: User's JWT access token
        
    Returns:
        Supabase AsyncClient configured with user's token
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
    
    if not supabase_url or not supabase_anon_key:
        raise RuntimeError("Missing Supabase configuration. Ensure SUPABASE_URL and SUPABASE_ANON_KEY are set.")
    
    # Create client with user's access token in headers
    from supabase.lib.client_options import AsyncClientOptions
    
    options = AsyncClientOptions(
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    )
    
    client = await create_async_client(supabase_url, supabase_anon_key, options)
    return client


async def _update_email_or_phone(
    user_id: str,
    given_input: str,
    triggered_text: str,
    access_token: str
) -> tuple[bool, bool]:
    """
    Update email or phone number using Supabase auth.update_user() with user's token.
    
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
    email_updated = False
    phone_updated = False

    try:
        # Create Supabase client with user's access token
        supabase = await _get_supabase_client_with_token(access_token)
        
        # Get user first to validate token and get user info
        user_response = await supabase.auth.get_user(access_token)
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token or user not found"
            )
        
        # Manually create and save session to storage
        # This is required for update_user() to work
        # We need to create a Session object and save it to the client's storage
        try:
            # Decode JWT to get expiration time
            SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
            if not SUPABASE_JWT_SECRET:
                raise RuntimeError("SUPABASE_JWT_SECRET not found in environment")
            
            decoded = jwt.decode(
                access_token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
                options={"verify_exp": False}
            )
            exp = decoded.get("exp", 0)
            current_time = int(time.time())
            
            if exp > 0 and exp <= current_time:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access token has expired. Please refresh your token."
                )
            
            # Calculate expires_in
            expires_in = max(exp - current_time, 3600) if exp > 0 else 3600
            expires_at = exp if exp > 0 else current_time + expires_in
            
            # Create Session object manually
            from supabase_auth.types import Session, User
            from supabase_auth.helpers import model_dump_json, model_dump
            
            # Convert user to the format Session expects
            # Try to get user as dict first, then create User object if needed
            try:
                # Try to convert to dict
                if hasattr(user_response.user, 'model_dump'):
                    user_data = user_response.user.model_dump()
                elif hasattr(user_response.user, 'dict'):
                    user_data = user_response.user.dict()
                else:
                    user_data = dict(user_response.user)
                
                # Create User object from dict to ensure type compatibility
                user_obj = User(**user_data)
            except Exception as user_error:
                logger.warning("Could not convert user object: %s, trying direct assignment", str(user_error))
                # Fallback: try using the user object directly
                user_obj = user_response.user
            
            # Create session with access token and a placeholder refresh token
            # The refresh token won't be used if the access token is still valid
            session = Session(
                access_token=access_token,
                refresh_token="placeholder_refresh_token",  # Placeholder - won't be validated if token is valid
                expires_in=expires_in,
                expires_at=expires_at,
                token_type="bearer",
                user=user_obj
            )
            
            # Save session directly to storage and set in-memory session
            # This mimics what _save_session() does internally
            storage_key = supabase.auth._storage_key
            session_json = model_dump_json(session)
            
            # Set in-memory session first
            supabase.auth._in_memory_session = session
            
            # Save to storage if persist_session is enabled
            if supabase.auth._persist_session:
                await supabase.auth._storage.set_item(storage_key, session_json)
            
            logger.info("Session manually created and saved for user %s", user_id)
            
        except HTTPException:
            raise
        except Exception as session_error:
            logger.error("Error creating session: %s", str(session_error), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create session: {str(session_error)}"
            )
        
        if triggered_text == VerificationTrigger.EMAIL_UPDATE.value:
            # Update email using Supabase admin API to update immediately without email confirmation
            # Since the email is already verified through our verification code system,
            # we use admin.update_user_by_id() which updates immediately
            # Reference: https://supabase.com/docs/reference/python/auth-updateuser
            try:
                from libs.shared_db.supabase_db.db import get_supabase_admin_client
                
                admin_supabase = await get_supabase_admin_client()
                # Use admin API to update email immediately
                # Since we've already verified the email via verification code, we update it directly
                # and set email_confirmed_at to make it active immediately (bypassing email confirmation)
                # Also update user_metadata (raw_user_meta_data) to ensure consistency
                current_time = datetime.now(timezone.utc).isoformat()
                
                # First, get current user metadata to preserve existing fields
                try:
                    user_data = await admin_supabase.auth.admin.get_user_by_id(user_id)
                    existing_metadata = {}
                    if user_data and user_data.user:
                        existing_metadata = user_data.user.user_metadata or {}
                except Exception as get_user_error:
                    logger.warning("Could not get user metadata, using empty dict: %s", str(get_user_error))
                    existing_metadata = {}
                
                # Update email in metadata as well (this updates raw_user_meta_data in database)
                # Make sure we're creating a new dict to avoid any reference issues
                updated_metadata = dict(existing_metadata)  # Create a copy
                updated_metadata["email"] = given_input  # Update email in the copy
                
                logger.info("Updating email for user %s", user_id)
                
                # Update email field first
                response = await admin_supabase.auth.admin.update_user_by_id(
                    user_id,
                    {
                        "email": given_input,
                        "email_confirmed_at": current_time
                    }
                )
                
                # Then update user_metadata separately to ensure raw_user_meta_data is updated
                # This is important because sometimes updating both together might not work
                try:
                    metadata_response = await admin_supabase.auth.admin.update_user_by_id(
                        user_id,
                        {
                            "user_metadata": updated_metadata  # This updates raw_user_meta_data
                        }
                    )
                except Exception as metadata_error:
                    logger.error("Failed to update user_metadata separately for email: %s", str(metadata_error))
                    # Continue anyway as email field was updated
                
                # Verify the update by reading the user back
                try:
                    verify_user = await admin_supabase.auth.admin.get_user_by_id(user_id)
                    if verify_user and verify_user.user:
                        verified_metadata = verify_user.user.user_metadata or {}
                        verified_email_in_metadata = verified_metadata.get("email")
                        if verified_email_in_metadata != given_input:
                            logger.warning("Email in user_metadata not updated correctly for user %s, retrying", user_id)
                            await admin_supabase.auth.admin.update_user_by_id(
                                user_id,
                                {"user_metadata": updated_metadata}
                            )
                except Exception as verify_error:
                    logger.warning("Could not verify email update: %s", str(verify_error))
                
                if response and response.user:
                    email_updated = True
                    logger.info("Email updated and confirmed in auth for user %s: %s", user_id, given_input)
                    
                    # Double-check: if email change is still pending, update again to force confirmation
                    try:
                        updated_user = await admin_supabase.auth.admin.get_user_by_id(user_id)
                        if updated_user and updated_user.user:
                            # If email hasn't changed yet (still showing old email), update again
                            if updated_user.user.email != given_input:
                                logger.warning("Email not updated on first attempt, retrying for user %s", user_id)
                                # Update again to force the change
                                retry_response = await admin_supabase.auth.admin.update_user_by_id(
                                    user_id,
                                    {
                                        "email": given_input,
                                        "email_confirmed_at": current_time,
                                        "user_metadata": updated_metadata  # Also update raw_user_meta_data on retry
                                    }
                                )
                                if retry_response and retry_response.user:
                                    logger.info("Email updated on retry for user %s", user_id)
                    except Exception as retry_error:
                        logger.warning("Could not retry email update: %s", str(retry_error))
                        # Continue anyway
                    
                    # Also update organization_members table using Supabase table update
                    # This ensures the profile endpoint shows the updated email
                    try:
                        # Update all organization_members records for this user
                        update_result = await admin_supabase.table("organization_members").update({
                            "email": given_input,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }).eq("user_id", user_id).execute()
                        
                        if update_result.data:
                            logger.info("Email updated in organization_members for user %s: %d records updated", 
                                      user_id, len(update_result.data))
                        else:
                            logger.warning("No organization_members records found to update for user %s", user_id)
                    except Exception as org_update_error:
                        # Log but don't fail - auth update was successful
                        logger.warning("Could not update organization_members email: %s", str(org_update_error))
                else:
                    logger.warning("Email update returned no user for user %s: %s", user_id, given_input)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to update email"
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error("Error updating email for user %s: %s", user_id, str(e), exc_info=True)
                email_updated = False
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to update email: {str(e)}"
                )
                
        elif triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value:
            # Update phone number using admin API to bypass SMS verification
            # Since we've already verified the phone via verification code, we update it directly
            try:
                from libs.shared_db.supabase_db.db import get_supabase_admin_client
                
                admin_supabase = await get_supabase_admin_client()
                # Use admin API to update phone immediately
                # Update both the phone field and phone_confirmed_at to make it active
                # Also update user_metadata (raw_user_meta_data) to ensure consistency
                current_time = datetime.now(timezone.utc).isoformat()
                
                # First, get current user metadata to preserve existing fields
                try:
                    user_data = await admin_supabase.auth.admin.get_user_by_id(user_id)
                    existing_metadata = {}
                    if user_data and user_data.user:
                        existing_metadata = user_data.user.user_metadata or {}
                except Exception as get_user_error:
                    logger.warning("Could not get user metadata, using empty dict: %s", str(get_user_error))
                    existing_metadata = {}
                
                # Update phone in metadata as well (this updates raw_user_meta_data in database)
                # Make sure we're creating a new dict to avoid any reference issues
                updated_metadata = dict(existing_metadata)  # Create a copy
                updated_metadata["phone"] = given_input  # Update phone in the copy
                
                logger.info("Updating phone for user %s", user_id)
                
                # Update phone field first - ensure we pass the phone exactly as received
                # Supabase might normalize phone numbers, but we'll pass it as-is
                phone_to_update = given_input  # Use the phone exactly as provided
                response = await admin_supabase.auth.admin.update_user_by_id(
                    user_id,
                    {
                        "phone": phone_to_update,
                        "phone_confirmed_at": current_time
                    }
                )
                
                # Then update user_metadata separately to ensure raw_user_meta_data is updated
                # This is important because sometimes updating both together might not work
                try:
                    metadata_response = await admin_supabase.auth.admin.update_user_by_id(
                        user_id,
                        {
                            "user_metadata": updated_metadata  # This updates raw_user_meta_data
                        }
                    )
                except Exception as metadata_error:
                    logger.error("Failed to update user_metadata separately: %s", str(metadata_error))
                    # Continue anyway as phone field was updated
                
                # Verify the update by reading the user back
                try:
                    verify_user = await admin_supabase.auth.admin.get_user_by_id(user_id)
                    if verify_user and verify_user.user:
                        verified_metadata = verify_user.user.user_metadata or {}
                        verified_phone_in_metadata = verified_metadata.get("phone")
                        if verified_phone_in_metadata != given_input:
                            logger.warning("Phone in user_metadata not updated correctly for user %s, retrying", user_id)
                            await admin_supabase.auth.admin.update_user_by_id(
                                user_id,
                                {"user_metadata": updated_metadata}
                            )
                except Exception as verify_error:
                    logger.warning("Could not verify phone update: %s", str(verify_error))
                
                if response and response.user:
                    phone_updated = True
                    # Verify the phone was actually updated in both places
                    updated_phone = response.user.phone if hasattr(response.user, 'phone') else None
                    updated_metadata_phone = None
                    if hasattr(response.user, 'user_metadata') and response.user.user_metadata:
                        updated_metadata_phone = response.user.user_metadata.get("phone")
                    
                    # Check if "+" was stripped by Supabase
                    if given_input.startswith("+") and updated_phone and not updated_phone.startswith("+"):
                        logger.warning("Phone '+' sign was stripped by Supabase for user %s", user_id)
                    
                    # Verify both phone field and user_metadata were updated
                    if updated_phone != given_input or updated_metadata_phone != given_input:
                        logger.warning("Phone not fully updated on first attempt, retrying for user %s", user_id)
                        retry_response = await admin_supabase.auth.admin.update_user_by_id(
                            user_id,
                            {
                                "phone": given_input,
                                "phone_confirmed_at": current_time,
                                "user_metadata": updated_metadata  # Also update raw_user_meta_data on retry
                            }
                        )
                        if retry_response and retry_response.user:
                            retry_phone = retry_response.user.phone if hasattr(retry_response.user, 'phone') else None
                            logger.info("Phone updated on retry for user %s", user_id)
                    
                    # Also update organization_members table using Supabase table update
                    # This ensures the profile endpoint shows the updated phone
                    try:
                        # Update all organization_members records for this user
                        update_result = await admin_supabase.table("organization_members").update({
                            "phone": given_input,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }).eq("user_id", user_id).execute()
                        
                        if update_result.data:
                            logger.info("Phone updated in organization_members for user %s: %d records updated", 
                                      user_id, len(update_result.data))
                        else:
                            logger.warning("No organization_members records found to update for user %s", user_id)
                    except Exception as org_update_error:
                        # Log but don't fail - auth update was successful
                        logger.warning("Could not update organization_members phone: %s", str(org_update_error))
                else:
                    logger.warning("Phone update returned no user for user %s: %s", user_id, given_input)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to update phone number"
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error("Error updating phone for user %s: %s", user_id, str(e), exc_info=True)
                phone_updated = False
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to update phone number: {str(e)}"
                )
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating Supabase client or updating user %s: %s", user_id, str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user: {str(e)}"
        )

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
    
    # Get current email from Supabase auth (actual email field, not user_metadata)
    current_user_email = ""
    try:
        user_data = await get_user_by_id(user_id)
        if user_data and hasattr(user_data, 'user') and user_data.user:
            # Get the actual email field, not user_metadata
            if hasattr(user_data.user, 'email') and user_data.user.email:
                current_user_email = user_data.user.email.lower()
    except Exception as e:
        logger.warning("Could not get current email from auth, using JWT token email: %s", str(e))
        # Fallback to JWT token email if we can't get it from auth
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
    - Checks if email/phone already exists in auth.users database
    - If user is already registered, returns error asking to login instead
    - If user is not registered, sends verification code (OTP) for signup flow
    - Works for both EMAIL and PHONE_NUMBER types

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
        SendVerificationCodeResponse:
            - verificationId: ID of the created verification code
            - expiryAt: Expiry timestamp
            - message: Success message
            - attemptsLeft: Number of send OTP attempts remaining for today (per day limit)

    Raises:
        HTTPException:
            - 400:
                - Entered email/phone is same as current user's email/phone (when token provided)
                - Email/phone already registered in auth.users (when no token - user should login instead)
            - 409: Email/phone already registered with another account (when token provided)
            - 429: Maximum send OTP attempts reached for today (per day limit)
            - 500: Internal server error

    Note:
        - There is a per-day limit on sending OTP codes (default: 5 attempts per day)
        - The limit resets after 24 hours
        - The response includes remaining attempts for the day
    """
    try:
        # Determine the input value based on type
        given_input = data.email if data.type == VerificationType.EMAIL else data.phoneNumber

        # Validate and determine triggered_text based on authentication status
        if current_user:
            user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        else:
            # For unauthenticated requests (signup flow), check if user already exists
            if data.type == VerificationType.EMAIL:
                # Check if email already exists in auth.users
                existing_auth_user = await get_auth_user_by_email(data.email)
                if existing_auth_user:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="This email is already registered. Please login instead of signing up."
                    )
            else:  # PHONE_NUMBER
                # Check if phone already exists in auth.users
                phone_exists = await _check_auth_user_exists_by_phone(data.phoneNumber)
                if phone_exists:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="This phone number is already registered. Please login instead of signing up."
                    )

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

        # Calculate remaining attempts (before creating new code)
        attempts_left = MAX_ATTEMPT_VERIFICATION - unverified_count

        # Check if max attempts reached (per day limit)
        if unverified_count >= MAX_ATTEMPT_VERIFICATION:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Maximum send OTP attempts ({MAX_ATTEMPT_VERIFICATION}) reached for today. Please try again tomorrow."
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

        # Calculate remaining attempts after creating this code
        # Subtract 1 because we just created a new unverified code
        attempts_left_after = attempts_left - 1

        # Return response
        return SendVerificationCodeResponse(
            verificationId=verification_record["id"],
            expiryAt=verification_record["expiry_at"],
            message="Verification code sent successfully",
            attemptsLeft=attempts_left_after
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

    Note:
        - There is NO limit on verification attempts. Users can verify as many times as they want.
        - Only the send OTP endpoint has a per-day limit.
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

        # Update email/phone if needed (only with token)
        email_updated = False
        phone_updated = False
        if current_user and user_id and triggered_text in [VerificationTrigger.EMAIL_UPDATE.value, VerificationTrigger.PHONE_NUMBER_UPDATE.value]:
            # Get access token from request state (set by JWT middleware)
            access_token = getattr(request.state, "access_token", None)
            if not access_token:
                logger.error("Access token not found in request state for user %s", user_id)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access token required to update email/phone number"
                )
            
            logger.info("Attempting to update %s for user %s with triggered_text: %s", data.type.value, user_id, triggered_text)
            email_updated, phone_updated = await _update_email_or_phone(
                user_id,
                given_input,
                triggered_text,
                access_token
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
