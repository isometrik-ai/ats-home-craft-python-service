"""User Utility Admin Operations Module
This module contains all user-related admin operations.
All Supabase Auth admin API operations for user management should be centralized here.
"""

from fastapi import HTTPException, status

from apps.user_service.app.schemas.auth import SignupRequest
from apps.user_service.app.schemas.users import CreateUserRequest
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
    update_user_email,
)
from libs.shared_db.supabase_db.admin_operations.user import update_email_of_user
from libs.shared_db.supabase_db.db import (
    get_fresh_supabase_admin_client,
    get_supabase_admin_client,
)
from libs.shared_utils.common_query import USER_NOT_FOUND_MESSAGE
from libs.shared_utils.email_utils import send_email
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    InternalServerErrorException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("user_utility_admin")


async def update_supabase_user_email(user_id: str, organization_id: str, email: str):
    """Update user email and send magic link notification
    Args:
        user_id: User ID
        organization_id: Organization ID
        email: New email address
    """
    try:
        user_info = await get_user_profile_by_id(user_id, organization_id)

        if user_info is None:
            raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

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
        email_sent = await send_admin_update_email(user_data)

        if not email_sent:
            logger.warning("Failed to send magic link email to %s", email)
            # Note: We don't fail the entire operation if email fails
            # The email update was successful, only the notification failed

    except HTTPException:  # ⬅️ re-raise FastAPI errors untouched
        raise
    except Exception as e:  # ⬅️ handle every other failure
        logger.error("Error updating Supabase user email: %s", str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error") from e


async def generate_magic_link(email: str) -> str | None:
    """Generate a magic link using Supabase Auth Admin API generateLink
    Args:
        email: User's email address
    Returns:
        Generated magic link URL or None if failed
    """
    try:
        supabase_client = await get_supabase_admin_client()
        response = supabase_client.auth.admin.generate_link(
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

    except (ValueError, AttributeError) as error:
        logger.error("Error generating magic link with Supabase client: %s", str(error))
        return None
    except Exception as error:
        logger.error("Unexpected error generating magic link: %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate magic link",
        ) from error


def create_admin_update_email_content(user: dict, magic_link: str) -> tuple[str, str]:
    """Create email subject and content for admin update notification with magic link
    Args:
        user: User information containing full_name, email
        magic_link: Generated magic link for authentication
    Returns:
        tuple[str, str]: Email subject and HTML message content

    Args:
        user: User information containing full_name, email
        magic_link: Generated magic link for authentication
    Returns:
        tuple[str, str]: Email subject and HTML message content
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


async def send_admin_update_email(user: dict) -> bool:
    """Send admin update notification email with magic link
    Args:
        user: User information containing id, full_name, email
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Generate magic link using Supabase Auth Admin API
        magic_link = await generate_magic_link(user.get("email"))

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
            detail="Failed to send admin update email",
        ) from error


async def sign_up_supabase_user(body: SignupRequest):
    """Create user in Supabase Auth using auth.signUp for user-initiated registration
    Args:
        body: Request body with user data
    Returns:
        dict: Supabase auth response containing user and session information
    """
    try:
        supabase = await get_supabase_admin_client()
        supabase_response = await supabase.auth.sign_up(
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
    except Exception as e:
        logger.error("Unexpected error signing up user: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e


# ============================================================================
# AUTHENTICATION FUNCTIONS
# ============================================================================


async def login_user(
    email: str,
    password: str,
    user_agent: str | None = None,
    device_signature: str | None = None,
) -> dict:
    """Attempts to log in a user with the provided email and password.
    Returns the result from Supabase or raises an exception on failure.

    Args:
        email: User's email address
        password: User's password
        user_agent: User-Agent header value
        device_signature: X-Device-Signature header value

    Returns:
        dict: Supabase authentication result

    Raises:
        Exception: If authentication fails
    """
    try:
        custom_headers = {}
        if device_signature:
            custom_headers["X-Device-Signature"] = device_signature

        supabase = await get_supabase_admin_client(
            user_agent=user_agent,
            custom_headers=custom_headers if custom_headers else None,
        )

        result = await supabase.auth.sign_in_with_password({"email": email, "password": password})
        return result
    except Exception as error:
        logger.error("Unexpected error while logging in user: %s", str(error))
        raise


async def invite_user_with_email(body: CreateUserRequest, user_context: UserContext) -> str:
    """Invite user with email using Supabase Auth Admin API
    Args:
        body: Request body with user data
        user_context: User context containing organization ID
    Returns:
        str: Created user ID
    Raises:
        HTTPException: If the user already exists or the email is invalid
    """
    try:
        supabase = await get_supabase_admin_client()
        response = await supabase.auth.admin.invite_user_by_email(
            email=body.email,
            options={
                "data": {
                    "organization_id": str(user_context.organization_id),
                    "role_id": str(body.role_id),
                    "full_name": body.full_name,
                    "type": "organization_member",
                }
            },
        )
        return response.user.id

    except Exception as e:
        error_message = str(e).lower()
        if (
            "already exists" in error_message
            or "already registered" in error_message
            or "user already exists" in error_message
        ):
            raise HTTPException(
                status_code=409,
                detail="User with this email already exists in the organization",
            ) from e

        logger.error("Email: %s, Error: %s", body.email, str(e))
        raise HTTPException(
            status_code=409,
            detail=str(e),
        ) from e


async def send_password_reset_email(email: str):
    """Send password reset email using Supabase Auth Admin API
    Args:
        email: User's email address
    Returns:
        dict: Supabase auth response containing user and session information
    Raises:
        Exception: If the email is invalid or the password reset email fails
    """
    try:
        supabase = await get_fresh_supabase_admin_client()
        return await supabase.auth.reset_password_email(email)

    except Exception as e:
        logger.error("Unexpected error while resetting password email: %s", str(e))
        raise e


async def update_password_with_token(token: str, new_password: str) -> dict:
    """Update password with token using Supabase Auth Admin API
    Args:
        token: User's token
        new_password: New password
    Returns:
        dict: Supabase auth response containing user and session information
    Raises:
        Exception: If the password update fails
    """
    try:
        supabase = await get_supabase_admin_client()
        return await supabase.auth.admin.update_user_by_id(token, {"password": new_password})
    except Exception as e:
        logger.error("Unexpected error while updating password with token: %s", str(e))
        raise e


async def refresh_session(refresh_token: str) -> dict:
    """Refresh user session using Supabase Auth Admin API
    Args:
        refresh_token: User's refresh token
    Returns:
        dict: Supabase auth response containing user and session information
    Raises:
        Exception: If the refresh token is invalid
    """
    try:
        supabase = await get_supabase_admin_client()
        return await supabase.auth.refresh_session(refresh_token)
    except Exception as e:
        logger.error("Unexpected error while refreshing session: %s", str(e))
        raise e
