"""
User Admin Operations Module

This module contains all user-related admin operations.
All Supabase Auth admin API operations for user management should be centralized here.
"""

import traceback
from postgrest import APIError
from httpx import HTTPError, HTTPStatusError, RequestError, TimeoutException
from fastapi import HTTPException
from supabase_auth.errors import AuthApiError

from libs.shared_utils.common_query import log_exception
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("user_admin_operations")

# ============================================================================
# USER BAN OPERATIONS
# ============================================================================


async def ban_the_user(user_id: str) -> bool:
    """Ban a user in the organization."""
    supabase = await get_supabase_admin_client()
    try:
        result = supabase.auth.admin.update_user_by_id(user_id,{"ban_duration": "365d"})
        return result.user is not None

    except APIError as e:
        logger.error("Supabase API error unbanning user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error unbanning user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error unbanning user: %s", e, exc_info=True)
        raise


async def unban_the_user(user_id: str) -> bool:
    """Unban a user in the organization."""
    supabase = await get_supabase_admin_client()
    try:
        result = supabase.auth.admin.update_user_by_id(user_id,{"ban_duration": "none"})
        return result.user is not None

    except APIError as e:
        logger.error("Supabase API error unbanning user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error unbanning user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error unbanning user: %s", e, exc_info=True)
        raise

async def delete_auth_user(user_id: str) -> bool:
    """Delete user from auth.users table."""
    supabase = await get_supabase_admin_client()
    try:
        await supabase.auth.admin.delete_user(id=user_id)
    except HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"No user found with ID {user_id}")
    except AuthApiError as e:
        log_exception()
        traceback.print_exc()
        logger.error("Supabase API error deleting auth user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        traceback.print_exc()
        logger.error("Network error deleting auth user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        traceback.print_exc()
        logger.error("Data validation error deleting auth user: %s", e, exc_info=True)
        raise


async def update_email_of_user(user_id: str, email: str) -> bool:
    """Update email of user in auth.users table."""
    supabase = await get_supabase_admin_client()
    try:
        result = supabase.auth.admin.update_user_by_id(user_id,{"email": email})
        return result.user is not None
    except APIError as e:
        logger.error("Supabase API error updating email of user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating email of user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating email of user: %s", e, exc_info=True)
        raise


async def update_metadata_of_user(user_id: str, metadata: dict) -> bool:
    """Update metadata of user in auth.users table."""
    supabase = await get_supabase_admin_client()
    try:
        result = supabase.auth.admin.update_user_by_id(user_id,{"user_metadata": metadata})
        return result is not None
    except APIError as e:
        logger.error("Supabase API error updating metadata of user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating metadata of user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating metadata of user: %s", e, exc_info=True)
        raise



async def update_password_with_link_identity(user_id: str, password: str) -> bool:
    """Add email/password identity to existing OAuth user using updateUser."""
    try:
        supabase_admin = await get_supabase_admin_client()


        # First, get the user to check their current providers and email
        user_data = await supabase_admin.auth.admin.get_user_by_id(user_id)
        current_providers = user_data.user.app_metadata.get("providers", [])

        # Check if user already has email provider
        if "email" in current_providers:
            result = await supabase_admin.auth.admin.update_user_by_id(
                user_id,
                {
                    "password": password
                }
            )
            return result.user is not None

        # For OAuth-only users, add email/password identity using updateUser
        # This creates a new identity for the existing user without creating a new user

        # Use updateUser to add email/password authentication
        # This will create an email identity linked to the existing user
        result = await supabase_admin.auth.admin.update_user_by_id(
            user_id,
            {
                "password": password,
                "app_metadata": {
                    **user_data.user.app_metadata,
                    "providers": current_providers + ["email"]
                },
                "user_metadata": {
                    **user_data.user.user_metadata,
                }
            }
        )

        return result is not None

    except AuthApiError as e:
        logger.error("Supabase Auth API error updating password of user: %s", e, exc_info=True)
        raise
    except APIError as e:
        logger.error("Supabase API error updating password of user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating password of user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating password of user: %s", e, exc_info=True)
        raise

async def get_user_by_id(user_id: str) -> dict:
    """Get user by id from auth.users table."""
    try:
        supabase = await get_supabase_admin_client()
        return await supabase.auth.admin.get_user_by_id(user_id)
    except AuthApiError as e:
        logger.error("Supabase API error getting user by id: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting user by id: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting user by id: %s", e, exc_info=True)
        raise
