"""
User Utility Admin Operations Module
This module contains all user-related admin operations.
All Supabase Auth admin API operations for user management should be centralized here.
"""

import sys
import os
from typing import Optional, Tuple, Any
from fastapi import HTTPException, status
from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_db.supabase_db.admin_operations.user import update_email_of_user
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
    update_user_email,
)
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_utils.email_utils import send_email

logger = get_logger("user_utility_admin")


async def update_supabase_user_email(
    user_id: str, organization_id: str, email: str
):
    """
    Update user email and send magic link notification
    """
    try:
        # Get user information before updating email for email notification
        user_info = await get_user_profile_by_id(user_id, organization_id)

        if user_info is None:
            raise HTTPException(status_code=404, detail="User not found in organization")

        # Update user email in Supabase Auth
        response = await update_email_of_user(user_id, email)
        # Check if the update was successful
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user email",
            )

        # Update email in organization_members table
        result = await update_user_email(user_id, organization_id, email)
        if result is None:
            raise HTTPException(status_code=404, detail="Member not found")

        # Prepare user data for email notification
        user_data = {
            "id": user_id,
            "full_name": user_info.get("full_name", ""),
            "email": email,  # Use the new email
        }

        # Send magic link email notification
        logger.info("Sending magic link email notification to user %s", user_id)
        email_sent = send_admin_update_email(user_data)

        if email_sent:
            logger.info("Magic link email sent successfully to %s", email)
        else:
            logger.warning("Failed to send magic link email to %s", email)
            # Note: We don't fail the entire operation if email fails
            # The email update was successful, only the notification failed

    except HTTPException:  # ⬅️ re-raise FastAPI errors untouched
        raise
    except Exception as e:  # ⬅️ handle every other failure
        logger.error("Error updating Supabase user email: %s", str(e))
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error"
        ) from e

async def generate_magic_link(email: str) -> Optional[str]:
    """
    Generate a magic link using Supabase Auth Admin API generateLink.

    Args:
        email (str): User's email address

    Returns:
        Optional[str]: Generated magic link URL or None if failed
    """
    try:
        supabase_client = await get_supabase_admin_client()
        response = supabase_client.auth.admin.generate_link(
            {
                "type": "magiclink",
                "email": email,
            }
        )

        logger.debug("Magic link generation response: %s", response)

        if response and hasattr(response, "properties") and response.properties:
            magic_link = response.properties.action_link
            logger.debug("Generated magic link: %s", magic_link)
            if magic_link:
                logger.info(
                    "Magic link generated successfully using Supabase client for %s",
                    email,
                )
                return magic_link

        logger.error("Magic link not found in Supabase client response")
        return None

    except (ValueError, AttributeError) as error:
        logger.error("Error generating magic link with Supabase client: %s", str(error))
        return None
    except Exception as error:
        logger.error("Unexpected error generating magic link: %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate magic link"
        ) from error


def create_admin_update_email_content(user: dict, magic_link: str) -> Tuple[str, str]:
    """
    Create email subject and content for admin update notification with magic link.

    Args:
        user (dict): User information containing full_name, email
        magic_link (str): Generated magic link for authentication

    Returns:
        Tuple[str, str]: Email subject and HTML message content
    """
    full_name = user.get("full_name", "")

    subject = "Your Email Has Been Updated - XQtiv"

    html_message = f"""
    <div style="font-family: Arial, sans-serif !important; font-size: 14px !important; color: #333333 !important; line-height: 1.6 !important; max-width: 600px !important;">
        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Hello {full_name},
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Your email id has been updated by the admin.
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Login using the link below:
        </p>

        <div style="text-align: center !important; margin: 24px 0 !important;">
            <a href="{magic_link}"
               style="background-color: #3498db !important; color: white !important; padding: 12px 24px !important; text-decoration: none !important; border-radius: 6px !important; display: inline-block !important; font-weight: bold !important;">
                Magic Link
            </a>
        </div>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            If the button doesn't work, you can copy and paste this link into your browser:
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #3498db !important; word-break: break-all !important;">
            {magic_link}
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Best regards,<br>
            Team XQtiv
        </p>

        <hr style="border: none !important; border-top: 1px solid #dee2e6 !important; margin: 24px 0 !important;">

        <p style="font-size: 11px !important; color: #868e96 !important; margin: 0 !important;">
            This is an automated notification from XQtiv. Please do not reply to this email.
        </p>
    </div>
    """

    return subject, html_message.strip()


def send_admin_update_email(user: dict) -> bool:
    """
    Send admin update notification email with magic link.

    Args:
        user: User information containing id, full_name, email

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Generate magic link using Supabase Auth Admin API
        magic_link = generate_magic_link(user.get("email"))
        logger.debug("Generated magic link for email update: %s", magic_link)

        if magic_link is None:
            logger.error("Failed to generate magic link for %s", user.get("email"))
            return False

        # Create email content with the actual magic link
        subject, html_message = create_admin_update_email_content(user, magic_link)

        # Send email with HTML content
        email_sent = send_email(
            user.get("email"),
            subject,
            "Please check the HTML version of this email.",
            html_message,
        )

        if email_sent:
            logger.info("Admin update email sent successfully to %s", user.get("email"))
            return True

        logger.error("Failed to send admin update email to %s", user.get("email"))
        return False

    except (ValueError, AttributeError) as error:
        logger.error("Error sending admin update email - validation error: %s", str(error))
        return False
    except Exception as error:
        logger.error("Unexpected error sending admin update email: %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send admin update email"
        ) from error

async def create_supabase_user(body, organization_id):
    """
    Create user in Supabase Auth with organization metadata using admin.createUser.
    This is for admin-initiated user creation (like in create_organisation API).

    Args:
        body: Request body with user data
        organization_id: Organization ID to associate with user

    Returns:
        str: Created user ID

    Raises:
        HTTPException: For duplicate email or Supabase errors
    """
    try:
        supabase = await get_supabase_admin_client()
        supabase_response = supabase.auth.admin.create_user(
            {
                "email": body.email,
                "password": body.password,
                "email_confirm": True,  # Auto-confirm email for admin user
                "user_metadata": {
                    "organization_id": organization_id,
                    "full_name": body.full_name,
                    "phone": body.phone,
                    "is_super_admin": True,
                    "type": "",
                },
            }
        )
        return supabase_response.user.id

    except (ConnectionError, TimeoutError, ValueError) as supabase_error:
        print(f"Supabase user creation failed: {supabase_error}")
        if (
            "already_exists" in str(supabase_error).lower()
            or "duplicate" in str(supabase_error).lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
            ) from supabase_error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        ) from supabase_error


async def sign_up_supabase_user(body, organization_id):
    """
    Create user in Supabase Auth using auth.signUp for user-initiated registration.
    This is for user signup (like in signup API) and requires email confirmation.

    Args:
        body: Request body with user data
        organization_id: Organization ID to associate with user

    Returns:
        str: Created user ID

    Raises:
        HTTPException: For duplicate email or Supabase errors
    """
    try:
        supabase = await get_supabase_admin_client()
        supabase_response = supabase.auth.sign_up(
            {
                "email": body.email,
                "password": body.password,
                "options": {
                    "data": {
                        "organization_id": organization_id,
                        "full_name": body.full_name,
                        "phone": body.phone,
                        "is_super_admin": True,
                        "type": "organization_member",
                    }
                }
            }
        )

        if not supabase_response.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to create user account"
            )

        return supabase_response.user.id

    except (ConnectionError, TimeoutError, ValueError) as supabase_error:
        print(f"Supabase user signup failed: {supabase_error}")
        if (
            "already_exists" in str(supabase_error).lower()
            or "duplicate" in str(supabase_error).lower()
            or "already registered" in str(supabase_error).lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            ) from supabase_error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        ) from supabase_error

# ============================================================================
# AUTHENTICATION FUNCTIONS
# ============================================================================


async def login_user(email: str, password: str) -> dict:
    """
    Attempts to log in a user with the provided email and password.
    Returns the result from Supabase or raises an exception on failure.

    Args:
        email (str): User's email address
        password (str): User's password

    Returns:
        Any: Supabase authentication result

    Raises:
        Exception: If authentication fails
    """
    try:
        # Get Supabase client from shared db module
        supabase = await get_supabase_admin_client()
        result = await supabase.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
        return result
    except Exception as error:
        log_exception()
        logger.error(error)
        raise

def log_exception():
    """Log exception details"""
    exc_type, _, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    logger.error("Error: %s, File: %s, Line: %s", exc_type, fname, exc_tb.tb_lineno)
