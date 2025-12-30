"""Auth repository helpers using the Supabase Python client.

Use with the service client for admin operations on auth users.
"""

from __future__ import annotations

from typing import Any

from supabase import AsyncClient

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.auth import SignupRequest
from libs.shared_utils.http_exceptions import BadRequestException
from libs.shared_utils.response_factory import CustomStatusCode

# Initialize logger
logger = get_logger("user-utils")


async def create_user(
    sb_client: AsyncClient,
    email: str,
    password: str,
    user_metadata: dict[str, Any] | None = None,
    phone: str | None = None,
    email_confirm: bool = False,
) -> dict[str, Any]:
    """Create a Supabase Auth user (service client required)."""
    resp = await sb_client.auth.admin.create_user(
        {
            "email": email,
            "password": password,
            "user_metadata": user_metadata or {},
            "phone": phone,
            "email_confirm": email_confirm,
        }
    )
    return resp.user.model_dump() if resp and resp.user else {}


async def get_user_by_id(sb_client: AsyncClient, user_id: str) -> dict[str, Any]:
    """Fetch an auth user by ID."""
    resp = await sb_client.auth.admin.get_user_by_id(user_id)
    return resp.user.model_dump() if resp and resp.user else {}


async def delete_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Delete an auth user by ID."""
    await sb_client.auth.admin.delete_user(user_id)
    return True


async def list_users(
    sb_client: AsyncClient, page: int = 1, per_page: int = 50
) -> list[dict[str, Any]]:
    """List auth users with pagination."""
    resp = await sb_client.auth.admin.list_users(page=page, per_page=per_page)
    if resp and resp.users:
        return [user.model_dump() for user in resp.users]
    return []


# ---------------------------------------------------------------------------
# Higher-level auth user admin helpers (migrated from admin_operations.user)
# These are still “repository-level” but encapsulate common admin patterns.
# ---------------------------------------------------------------------------


async def ban_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Ban a user in the organization for 365 days (8760 hours)."""
    result = await sb_client.auth.admin.update_user_by_id(
        user_id,
        {"ban_duration": "8760h"},
    )
    return result.user is not None


async def unban_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Unban a user in the organization."""
    result = await sb_client.auth.admin.update_user_by_id(
        user_id,
        {"ban_duration": "none"},
    )
    return result.user is not None


async def delete_auth_user(sb_client: AsyncClient, user_id: str) -> bool:
    """Delete user from auth.users table."""
    await sb_client.auth.admin.delete_user(id=user_id)
    return True


async def update_email(
    sb_client: AsyncClient,
    user_id: str,
    email: str,
) -> bool:
    """Update email of user in auth.users table."""
    result = await sb_client.auth.admin.update_user_by_id(user_id, {"email": email})
    return result.user is not None


async def update_metadata(
    sb_client: AsyncClient,
    user_id: str,
    metadata: dict[str, Any],
) -> bool:
    """Update metadata of user in auth.users table."""
    result = await sb_client.auth.admin.update_user_by_id(
        user_id,
        {"user_metadata": metadata},
    )
    return result.user is not None


async def update_phone(
    client: AsyncClient,
    user_id: str,
    existing_metadata: dict[str, Any],
    phone: str,
) -> bool:
    """Update phone number of user in auth.users table user_metadata."""
    updated_metadata = {**(existing_metadata or {}), "phone": phone}
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
    """Add or update email/password identity for an existing auth user."""
    print(getattr(client, "_key", "No key attribute"))
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
    response = sb_client.auth.admin.generate_link(
        {
            "type": "magiclink",
            "email": email,
        }
    )

    if response and hasattr(response, "properties") and response.properties:
        magic_link = response.properties.action_link
        if magic_link:
            return magic_link

    logger.error("Magic link not found in Supabase client response")
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
                    "phone": body.phone,
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


async def update_password_with_token(
    token: str, new_password: str, sb_admin_client: AsyncClient
) -> dict:
    """Update password with token using Supabase Auth Admin API
    Args:
        token: User's token
        new_password: New password
        sb_admin_client: Supabase admin client
    Returns:
        dict: Supabase auth response containing user and session information
    """
    return await sb_admin_client.auth.admin.update_user_by_id(token, {"password": new_password})


async def refresh_session(refresh_token: str, supabase_client: AsyncClient) -> dict:
    """Refresh user session using Supabase Auth Admin API
    Args:
        refresh_token: User's refresh token
        supabase_client: Supabase client
    Returns:
        dict: Supabase auth response containing user and session information
    """
    return await supabase_client.auth.refresh_session(refresh_token)
