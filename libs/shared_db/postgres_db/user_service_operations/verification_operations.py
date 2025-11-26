"""
Verification Codes Database Operations Module

This module contains all verification code-related database operations.
All SQL queries for verification code management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- Create verification code
- Get verification code by ID
- Update verification code (verification attempts)
- Check verification code expiry
"""

import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_db.supabase_db.db import get_fresh_supabase_admin_client
from .exception_handling import handle_database_errors, create_error_messages, DatabaseOperationError

# Initialize logger
logger = get_logger("verification_operations")

# Environment variables
MAX_ATTEMPT_VERIFICATION = int(os.getenv("MAX_ATTEMPT_VERIFICATION", "5"))
# Legacy OTP settings (for backward compatibility)
OTP_ENABLED = os.getenv("OTP_ENABLED", "true").lower() == "true"
DEFAULT_OTP = os.getenv("DEFAULT_OTP", "1111")
# Type-specific OTP settings
EMAIL_OTP_ENABLED = os.getenv("EMAIL_OTP_ENABLED", "true").lower() == "true"
EMAIL_DEFAULT_OTP = os.getenv("EMAIL_DEFAULT_OTP", "1111")
PHONE_OTP_ENABLED = os.getenv("PHONE_OTP_ENABLED", "true").lower() == "true"
PHONE_DEFAULT_OTP = os.getenv("PHONE_DEFAULT_OTP", "1111")
VERIFICATION_CODE_EXPIRY_MINUTES = int(os.getenv("VERIFICATION_CODE_EXPIRY_MINUTES", "10"))
# Time window for counting attempts (in hours, 24 = per day, None = based on expiry only)
# Default: 24 hours (per day)
_VERIFICATION_ATTEMPT_WINDOW_HOURS = os.getenv("VERIFICATION_ATTEMPT_WINDOW_HOURS")
VERIFICATION_ATTEMPT_WINDOW_HOURS = int(_VERIFICATION_ATTEMPT_WINDOW_HOURS) if _VERIFICATION_ATTEMPT_WINDOW_HOURS else 24


# ============================================================================
# VERIFICATION CODE OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_verification_code",
    custom_messages=create_error_messages("create_verification_code", "creating"))
async def create_verification_code(
    type_text: str,
    given_input: str,
    triggered_text: str,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new verification code record.

    Args:
        type_text: Type of verification (EMAIL or PHONE_NUMBER)
        given_input: The input value (email or phone number)
        triggered_text: The triggered text (same as given_input for now)
        user_id: Optional user ID
        ip_address: Optional IP address

    Returns:
        Dict containing the created verification code record
    """
    supabase = await get_fresh_supabase_admin_client()

    # Generate verification code based on type
    # Use type-specific OTP settings (EMAIL_OTP_ENABLED/PHONE_OTP_ENABLED)
    # Fall back to legacy OTP_ENABLED if type-specific not set
    type_upper = type_text.upper()
    if type_upper == "EMAIL":
        otp_enabled = EMAIL_OTP_ENABLED
        default_otp = EMAIL_DEFAULT_OTP
    elif type_upper == "PHONE_NUMBER":
        otp_enabled = PHONE_OTP_ENABLED
        default_otp = PHONE_DEFAULT_OTP
    else:
        # Fallback to legacy settings for unknown types
        otp_enabled = OTP_ENABLED
        default_otp = DEFAULT_OTP
        logger.warning("Unknown verification type '%s', using legacy OTP settings", type_text)
    
    if otp_enabled:
        # Generate cryptographically secure random 4-digit code (1000-9999)
        # Using secrets.randbelow for secure random number generation
        verification_code = str(secrets.randbelow(9000) + 1000)
    else:
        # Use default OTP from config
        verification_code = default_otp

    # Calculate expiry time (current time + expiry minutes in milliseconds)
    current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    expiry_at = current_time_ms + (VERIFICATION_CODE_EXPIRY_MINUTES * 60 * 1000)

    # Prepare data for insertion
    verification_data = {
        "type_text": type_text,
        "given_input": given_input,
        "triggered_text": triggered_text,
        "verification_code": verification_code,
        "verified": False,
        "expiry_at": expiry_at,
        "attempts": []
    }

    if user_id:
        verification_data["user_id"] = user_id

    if ip_address:
        verification_data["ip_address"] = ip_address

    # Insert into database
    result = await supabase.table("verification_codes").insert(verification_data).execute()

    if not result.data or len(result.data) == 0:
        logger.error("Failed to create verification code")
        raise DatabaseOperationError(
            "Failed to create verification code",
            operation="create_verification_code"
        )

    logger.info("Verification code created: %s", result.data[0]['id'])
    return result.data[0]


@handle_database_errors(
    "get_verification_code_by_id",
    custom_messages=create_error_messages("get_verification_code_by_id", "getting"))
async def get_verification_code_by_id(verification_id: str) -> Optional[Dict[str, Any]]:
    """
    Get verification code by ID.

    Args:
        verification_id: The verification code record ID

    Returns:
        Dict containing the verification code record or None if not found
    """
    supabase = await get_fresh_supabase_admin_client()

    result = await supabase.table("verification_codes").select("*").eq("id", verification_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


@handle_database_errors(
    "get_recent_verification_codes",
    custom_messages=create_error_messages("get_recent_verification_codes", "getting"))
async def get_recent_verification_codes(
    type_text: str,
    given_input: str,
    limit: int = 5,
    window_hours: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Get recent verification codes for a given type and input.
    Used to check attempt counts.

    Args:
        type_text: Type of verification (EMAIL or PHONE_NUMBER)
        given_input: The input value (email or phone number)
        limit: Maximum number of records to return
        window_hours: Optional time window in hours (e.g., 24 for per-day limit)

    Returns:
        List of verification code records
    """
    supabase = await get_fresh_supabase_admin_client()

    query = (
        supabase.table("verification_codes")
        .select("*")
        .eq("type_text", type_text)
        .eq("given_input", given_input)
    )

    # If window_hours is specified, filter by time window
    if window_hours:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        query = query.gte("created_at", cutoff_time.isoformat())

    result = await (
        query
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    return result.data if result.data else []


@handle_database_errors(
    "update_verification_code",
    custom_messages=create_error_messages("update_verification_code", "updating"))
async def update_verification_code(
    verification_id: str,
    verified: bool,
    attempts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Update verification code record.

    Args:
        verification_id: The verification code record ID
        verified: Whether the code was verified
        attempts: Updated attempts array

    Returns:
        Dict containing the updated verification code record
    """
    supabase = await get_fresh_supabase_admin_client()

    update_data = {
        "verified": verified,
        "attempts": attempts
    }

    result = await (
        supabase.table("verification_codes")
        .update(update_data)
        .eq("id", verification_id)
        .execute()
    )

    if not result.data or len(result.data) == 0:
        logger.error("Failed to update verification code: %s", verification_id)
        raise DatabaseOperationError(
            f"Failed to update verification code: {verification_id}",
            operation="update_verification_code"
        )

    return result.data[0]
