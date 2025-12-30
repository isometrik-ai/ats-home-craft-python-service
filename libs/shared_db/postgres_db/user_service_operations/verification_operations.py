"""Verification Codes Database Operations Module
This module contains all verification code-related database operations.
All SQL queries for verification code management should be centralized here.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.user_service.app.config.app_settings import app_settings
from libs.shared_db.supabase_db.db import get_fresh_supabase_admin_client
from libs.shared_utils.http_exceptions import InternalServerErrorException
from libs.shared_utils.status_codes import CustomStatusCode


async def create_verification_code(
    type_text: str,
    given_input: str,
    triggered_text: str,
    user_id: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Create a new verification code record
    Args:
        type_text: Type of verification (EMAIL or PHONE_NUMBER)
        given_input: The input value (email or phone number)
        triggered_text: The triggered text (same as given_input for now)
        user_id: Optional user ID
        ip_address: Optional IP address
    Returns:
        dict containing the created verification code record

    """
    supabase = await get_fresh_supabase_admin_client()

    type_upper = type_text.upper()
    if type_upper == "EMAIL":
        otp_enabled = app_settings.two_fa_settings.email_otp_enabled
        default_otp = app_settings.two_fa_settings.email_default_otp
    else:
        otp_enabled = app_settings.two_fa_settings.phone_otp_enabled
        default_otp = app_settings.two_fa_settings.phone_default_otp

    if otp_enabled:
        verification_code = str(secrets.randbelow(9000) + 1000)
    else:
        verification_code = default_otp

    current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    expiry_at = current_time_ms + (
        app_settings.two_fa_settings.verification_code_expiry_minutes * 60 * 1000
    )

    verification_data = {
        "type_text": type_text,
        "given_input": given_input,
        "triggered_text": triggered_text,
        "verification_code": verification_code,
        "verified": False,
        "expiry_at": expiry_at,
        "attempts": [],
    }

    if user_id:
        verification_data["user_id"] = user_id

    if ip_address:
        verification_data["ip_address"] = ip_address

    result = await supabase.table("verification_codes").insert(verification_data).execute()

    if not result.data or len(result.data) == 0:
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    return result.data[0]


async def get_verification_code_by_id(verification_id: str) -> dict[str, Any] | None:
    """Get verification code by ID
    Args:
        verification_id: The verification code record ID
    Returns:
        dict containing the verification code record or None if not found
    """
    supabase = await get_fresh_supabase_admin_client()

    result = (
        await supabase.table("verification_codes").select("*").eq("id", verification_id).execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


async def get_recent_verification_codes(
    type_text: str, given_input: str, limit: int = 5, window_hours: int | None = None
) -> list[dict[str, Any]]:
    """Get recent verification codes for a given type and input
    Args:
        type_text: Type of verification (EMAIL or PHONE_NUMBER)
        given_input: The input value (email or phone number)
        limit: Maximum number of records to return
        window_hours: Optional time window in hours (e.g., 24 for per-day limit)
    Returns:
        list of verification code records
    """
    supabase = await get_fresh_supabase_admin_client()

    query = (
        supabase.table("verification_codes")
        .select("*")
        .eq("type_text", type_text)
        .eq("given_input", given_input)
    )

    if window_hours:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        query = query.gte("created_at", cutoff_time.isoformat())

    result = await query.order("created_at", desc=True).limit(limit).execute()

    return result.data if result.data else []


async def update_verification_code(
    verification_id: str, verified: bool, attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Update verification code record
    Args:
        verification_id: The verification code record ID
        verified: Whether the code was verified
        attempts: Updated attempts array
    Returns:
        dict containing the updated verification code record
    """
    supabase = await get_fresh_supabase_admin_client()

    update_data = {"verified": verified, "attempts": attempts}

    result = await (
        supabase.table("verification_codes").update(update_data).eq("id", verification_id).execute()
    )

    if not result.data or len(result.data) == 0:
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    return result.data[0]
