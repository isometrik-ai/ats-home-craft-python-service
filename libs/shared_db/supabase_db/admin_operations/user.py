"""
User Admin Operations Module

This module contains all user-related admin operations.
All Supabase Auth admin API operations for user management should be centralized here.
"""

from postgrest import APIError
from httpx import HTTPError, RequestError, TimeoutException
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
        # result = await supabase.table("organization_members").update({
        #     "status": "active",
        #     "ban_reason": None,
        #     "updated_at": "now()"
        # }).eq("user_id", user_id).eq("organization_id", organization_id).execute()
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
        # result = await supabase.table("organization_members").update({
        #     "status": "active",
        #     "ban_reason": None,
        #     "updated_at": "now()"
        # }).eq("user_id", user_id).eq("organization_id", organization_id).execute()
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
        result = supabase.auth.admin.delete_user(user_id)
        return result.user is not None
    except APIError as e:
        logger.error("Supabase API error deleting auth user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error deleting auth user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
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
