"""User Admin Operations Module
This module contains all user-related admin operations.
All Supabase Auth admin API operations for user management should be centralized here.
"""

from httpx import HTTPError, RequestError, TimeoutException
from postgrest import APIError

from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_db.supabase_db.db import (
    get_fresh_supabase_admin_client,
    get_supabase_admin_client,
)

logger = get_logger("user_admin_operations")


async def ban_the_user(user_id: str) -> bool:
    """Ban a user in the organization
    Args:
        user_id: User ID
    Returns:
        bool: True if user was banned successfully, False otherwise
    Raises:
        Exception: If the user is not found or the ban fails
    """
    supabase = await get_supabase_admin_client()
    try:
        # Convert 365 days to hours (365 * 24 = 8760 hours)
        # Supabase uses Go's time format which supports: ns, us, ms, s, m, h (not "d")
        result = await supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "8760h"})
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
    """Unban a user in the organization
    Args:
        user_id: User ID
    Returns:
        bool: True if user was unbanned successfully, False otherwise
    Raises:
        Exception: If the user is not found or the unban fails
    """
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "none"})
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
    """Delete user from auth.users table
    Args:
        user_id: User ID
    Returns:
        bool: True if user was deleted successfully, False otherwise
    Raises:
        Exception: If the user is not found or the deletion fails
    """
    supabase = await get_supabase_admin_client()
    try:
        await supabase.auth.admin.delete_user(id=user_id)
        return True
    except Exception as e:
        logger.error("Error deleting auth user: %s", e, exc_info=True)
        raise


async def update_email_of_user(user_id: str, email: str) -> bool:
    """Update email of user in auth.users table
    Args:
        user_id: User ID
        email: New email address
    Returns:
        bool: True if email was updated successfully, False otherwise
    Raises:
        Exception: If the email is invalid or the update fails
    """
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.auth.admin.update_user_by_id(user_id, {"email": email})
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
    """Update metadata of user in auth.users table
    Args:
        user_id: User ID
        metadata: New metadata
    Returns:
        bool: True if metadata was updated successfully, False otherwise
    Raises:
        Exception: If the metadata is invalid or the update fails
    """
    supabase = await get_fresh_supabase_admin_client()
    try:
        result = await supabase.auth.admin.update_user_by_id(user_id, {"user_metadata": metadata})
        return result.user is not None
    except APIError as e:
        logger.error("Supabase API error updating metadata of user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating metadata of user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating metadata of user: %s", e, exc_info=True)
        raise


async def update_phone_of_user(user_id: str, phone: str) -> bool:
    """Update phone number of user in auth.users table user_metadata
    Args:
        user_id: User ID
        phone: New phone number
    Returns:
        bool: True if phone number was updated successfully, False otherwise
    Raises:
        Exception: If the phone number is invalid or the update fails
    """
    supabase = await get_supabase_admin_client()
    try:
        # First get current user to preserve existing metadata
        user_data = await get_user_by_id(user_id)
        if not user_data:
            logger.error("User not found: %s", user_id)
            raise ValueError(f"User not found: {user_id}")

        # Get existing user_metadata or create empty dict
        # get_user_by_id returns a UserResponse object with .user attribute
        existing_metadata = (
            user_data.user.user_metadata if hasattr(user_data, "user") and user_data.user else {}
        )
        if not existing_metadata:
            existing_metadata = {}

        # Update phone in metadata
        updated_metadata = {**existing_metadata, "phone": phone}

        # Update user with new metadata
        result = await supabase.auth.admin.update_user_by_id(
            user_id, {"user_metadata": updated_metadata}
        )
        return result.user is not None
    except APIError as e:
        logger.error("Supabase API error updating phone of user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating phone of user: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating phone of user: %s", e, exc_info=True)
        raise


async def update_password_with_link_identity(user_id: str, password: str) -> bool:
    """Add email/password identity to existing OAuth user using updateUser
    Args:
        user_id: User ID
        password: New password
    Returns:
        bool: True if password was updated successfully, False otherwise
    Raises:
        Exception: If the password is invalid or the update fails
    """
    try:
        supabase_admin = await get_fresh_supabase_admin_client()

        # First, get the user to check their current providers and email
        user_data = await supabase_admin.auth.admin.get_user_by_id(user_id)
        current_providers = user_data.user.app_metadata.get("providers", [])

        # Check if user already has email provider
        if "email" in current_providers:
            result = await supabase_admin.auth.admin.update_user_by_id(
                user_id, {"password": password}
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
                    "providers": current_providers + ["email"],
                },
                "user_metadata": {
                    **user_data.user.user_metadata,
                },
            },
        )

        return result is not None

    except Exception as e:
        logger.error(
            "Error adding email/password identity to existing OAuth user: %s",
            e,
            exc_info=True,
        )
        raise


async def get_user_by_id(user_id: str) -> dict:
    """Get user by id from auth.users table
    Args:
        user_id: User ID
    Returns:
        dict: User information
    Raises:
        Exception: If the user is not found or the retrieval fails
    """
    supabase = await get_supabase_admin_client()
    return await supabase.auth.admin.get_user_by_id(user_id)


async def update_user(user_id: str, update_data: dict) -> bool:
    """Update user in auth.users table
    Args:
        user_id: User ID
        update_data: New data to update
    Returns:
        bool: True if user was updated successfully, False otherwise
    Raises:
        Exception: If the user is not found or the update fails
    """
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.auth.admin.update_user_by_id(user_id, update_data)
        return result.user is not None
    except APIError as e:
        logger.error("Supabase API error updating user: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating user: %s", e, exc_info=True)
        raise
