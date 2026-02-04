"""Auth repository helpers using the Supabase Python client.

Use with the service client for admin operations on auth users.
"""

from typing import Any

from supabase import AsyncClient
from supabase_auth.types import VerifyOtpParams

from apps.user_service.app.schemas.auth import SignupRequest
from libs.shared_utils.http_exceptions import BadRequestException
from libs.shared_utils.response_factory import CustomStatusCode


async def create_user(
    sb_client: AsyncClient,
    email: str,
    user_metadata: dict[str, Any] | None = None,
    phone: str | None = None,
    email_confirm: bool = False,
) -> dict[str, Any]:
    """Create a Supabase Auth user (service client required).
    Args:
        sb_client: Supabase AsyncClient
        email: User's email address
        user_metadata: User's metadata
        phone: User's phone number
        email_confirm: Whether to confirm email
    Returns:
        dict[str, Any]: User data
    """
    resp = await sb_client.auth.admin.create_user(
        {
            "email": email,
            "user_metadata": user_metadata or {},
            "phone": phone,
            "email_confirm": email_confirm,
        }
    )
    return resp.user.model_dump() if resp and resp.user else {}


async def get_user_by_id(sb_client: AsyncClient, user_id: str) -> dict[str, Any]:
    """Fetch an auth user by ID.
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
    Returns:
        dict[str, Any]: User data
    """
    resp = await sb_client.auth.admin.get_user_by_id(user_id)
    return resp.user.model_dump() if resp and resp.user else {}


async def delete_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Delete an auth user by ID.
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
    Returns:
        bool: True if user deleted successfully, False otherwise
    """
    await sb_client.auth.admin.delete_user(user_id)
    return True


async def list_users(
    sb_client: AsyncClient, page: int = 1, per_page: int = 50
) -> list[dict[str, Any]]:
    """List auth users with pagination.
    Args:
        sb_client: Supabase AsyncClient
        page: Page number
        per_page: Number of users per page
    Returns:
        list[dict[str, Any]]: List of user data
    """
    resp = await sb_client.auth.admin.list_users(page=page, per_page=per_page)
    if resp and resp.users:
        return [user.model_dump() for user in resp.users]
    return []


# ---------------------------------------------------------------------------
# Higher-level auth user admin helpers (migrated from admin_operations.user)
# These are still “repository-level” but encapsulate common admin patterns.
# ---------------------------------------------------------------------------


async def ban_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Ban a user in the organization for 365 days (8760 hours).
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
    Returns:
        bool: True if user banned successfully, False otherwise
    """
    result = await sb_client.auth.admin.update_user_by_id(
        user_id,
        {"ban_duration": "8760h"},
    )
    return result.user is not None


async def unban_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Unban a user in the organization.
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
    Returns:
        bool: True if user unbanned successfully, False otherwise
    """
    result = await sb_client.auth.admin.update_user_by_id(
        user_id,
        {"ban_duration": "none"},
    )
    return result.user is not None


async def delete_auth_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Delete user from auth.users table.
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
    Returns:
        bool: True if user deleted successfully, False otherwise
    """
    await sb_client.auth.admin.delete_user(id=user_id)
    return True


async def update_email(
    sb_client: AsyncClient,
    user_id: str,
    email: str,
) -> bool:
    """Update email of user in auth.users table.
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
        email: User's email address
    Returns:
        bool: True if email updated successfully, False otherwise
    """
    result = await sb_client.auth.admin.update_user_by_id(user_id, {"email": email})
    return result.user is not None


async def update_metadata(
    sb_client: AsyncClient,
    user_id: str,
    metadata: dict[str, Any],
) -> bool:
    """Update metadata of user in auth.users table.
    Args:
        sb_client: Supabase AsyncClient
        user_id: User's ID
        metadata: User's metadata
    Returns:
        bool: True if metadata updated successfully, False otherwise
    """
    result = await sb_client.auth.admin.update_user_by_id(
        user_id,
        {"user_metadata": metadata},
    )
    return result.user is not None


async def update_phone(
    client: AsyncClient,
    user_id: str,
    existing_metadata: dict[str, Any],
    phone_number: str,
    phone_isd_code: str,
) -> bool:
    """Update phone number of user in auth.users table user_metadata.
    Args:
        client: Supabase client
        user_id: User's ID
        existing_metadata: User's existing metadata
        phone_number: Phone number (without ISD code)
        phone_isd_code: Phone ISD code (e.g., '+91')
    Returns:
        bool: True if phone updated successfully, False otherwise
    """
    updated_metadata = {
        **(existing_metadata or {}),
        "phone_number": phone_number,
        "phone_isd_code": phone_isd_code,
    }
    result = await client.auth.admin.update_user_by_id(
        user_id,
        {"user_metadata": updated_metadata},
    )
    return result.user is not None


async def update_password_with_link_identity(
    client: AsyncClient,
    user_id: str,
    password: str,
) -> bool:
    """Add or update email/password identity for an existing auth user.
    Args:
        client: Supabase client
        user_id: User's ID
        password: User's password
    Returns:
        bool: True if password updated successfully, False otherwise
    """
    user_data = await client.auth.admin.get_user_by_id(uid=user_id)
    current_providers = user_data.user.app_metadata.get("providers", [])

    # If user already has email provider, just update password
    if "email" in current_providers:
        result = await client.auth.admin.update_user_by_id(
            user_id,
            {"password": password},
        )
        return result.user is not None

    # For OAuth-only users, add email/password identity using update_user_by_id
    result = await client.auth.admin.update_user_by_id(
        user_id,
        {
            "password": password,
            "app_metadata": {
                **user_data.user.app_metadata,
                "providers": current_providers + ["email"],
            },
            "user_metadata": {
                **user_data.user.user_metadata,
            },
        },
    )

    return result is not None


async def generate_magic_link(sb_client: AsyncClient, email: str) -> str | None:
    """Generate a magic link using Supabase Auth Admin API generateLink
    Args:
        sb_client: Supabase client
        email: User's email address
    Returns:
        Generated magic link URL or None if failed
    """
    response = await sb_client.auth.admin.generate_link(
        {
            "type": "magiclink",
            "email": email,
        }
    )

    if response and hasattr(response, "properties") and response.properties:
        magic_link = response.properties.action_link
        if magic_link:
            return magic_link

    return None


async def sign_up_supabase_user(body: SignupRequest, sb_client: AsyncClient):
    """Create user in Supabase Auth using auth.signUp for user-initiated registration
    Args:
        body: Request body with user data
        sb_client: Supabase client
    Returns:
        dict: Supabase auth response containing user and session information
    """
    supabase_response = await sb_client.auth.sign_up(
        {
            "email": body.email,
            "password": body.password,
            "options": {
                "data": {
                    "first_name": body.first_name,
                    "last_name": body.last_name,
                    "phone_number": body.phone_number,
                    "phone_isd_code": body.phone_isd_code,
                    "timezone": body.timezone,
                    "salutation": body.salutation,
                }
            },
        }
    )
    if not supabase_response.user:
        raise BadRequestException(
            message_key="errors.bad_request",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    return supabase_response


async def login_user(
    email: str,
    password: str,
    sb_client: AsyncClient,
) -> dict:
    """Attempts to log in a user with the provided email and password.
    Returns the result from Supabase or raises an exception on failure.

    Args:
        email: User's email address
        password: User's password
        sb_client: Supabase client
    Returns:
        dict: Supabase authentication result
    """
    result = await sb_client.auth.sign_in_with_password({"email": email, "password": password})
    return result


async def send_password_reset_email(email: str, sb_client: AsyncClient):
    """Send password reset email using Supabase Auth Admin API
    Args:
        email: User's email address
        sb_client: Supabase anon client
    Returns:
        dict: Supabase auth response containing user and session information
    """
    return await sb_client.auth.reset_password_email(email)


async def update_password_with_token(token: str, new_password: str, sb_client: AsyncClient) -> dict:
    """Update password with recovery token using standard Supabase flow.

    This follows the standard Supabase password reset flow:
    1. Verify the recovery token using verify_otp
    2. Update password using update_user (which uses the verified session)

    Args:
        token: Recovery token from password reset email URL (use the token parameter,
               not the access_token from the URL hash)
        new_password: New password
        sb_client: Supabase anon client (not admin client)
    Returns:
        dict: Supabase auth response containing user and session information
    """
    # Step 1: Verify the recovery token - this establishes a session
    verify_params: VerifyOtpParams = {"token_hash": token, "type": "recovery"}
    verify_response = await sb_client.auth.verify_otp(verify_params)

    if not verify_response.session:
        raise ValueError("Token verification failed - no session established")

    # Step 2: Update password using the verified session
    update_response = await sb_client.auth.update_user({"password": new_password})

    return update_response


async def refresh_session(refresh_token: str, supabase_client: AsyncClient) -> dict:
    """Refresh user session using Supabase Auth Admin API
    Args:
        refresh_token: User's refresh token
        supabase_client: Supabase client
    Returns:
        dict: Supabase auth response containing user and session information
    """
    return await supabase_client.auth.refresh_session(refresh_token)
