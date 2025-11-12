"""
User Utility Admin Operations Module
This module contains all user-related admin operations.
All Supabase Auth admin API operations for user management should be centralized here.
"""

import urllib.parse
from typing import Optional, Tuple, get_args

from fastapi import HTTPException, status
from supabase_auth.errors import AuthApiError
from supabase_auth.types import Provider as AUTH_PROVIDER

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.users import CreateUserRequest
from apps.user_service.app.dependencies.common_utils import UserContext
from apps.user_service.app.schemas.auth import CODE_VERIFIER, CODE_CHALLENGE

from libs.shared_utils.email_utils import send_email
from libs.shared_utils.common_query import log_exception
from libs.shared_utils.common_query import USER_NOT_FOUND_MESSAGE
from libs.shared_db.supabase_db.db import get_supabase_admin_client, get_fresh_supabase_admin_client
from libs.shared_db.supabase_db.admin_operations.user import update_email_of_user
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
    update_user_email,
)

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


async def send_admin_update_email(user: dict) -> bool:
    """
    Send admin update notification email with magic link.

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
            detail="Failed to send admin update email"
        ) from error

# async def create_supabase_user(body, organization_id):
#     """
#     Create user in Supabase Auth with organization metadata using admin.createUser.
#     This is for admin-initiated user creation (like in create_organisation API).

#     Args:
#         body: Request body with user data
#         organization_id: Organization ID to associate with user

#     Returns:
#         str: Created user ID

#     Raises:
#         HTTPException: For duplicate email or Supabase errors
#     """
#     try:
#         supabase = await get_supabase_admin_client()
#         supabase_response = supabase.auth.admin.create_user(
#             {
#                 "email": body.email,
#                 "password": body.password,
#                 "email_confirm": True,  # Auto-confirm email for admin user
#                 "user_metadata": {
#                     "organization_id": organization_id,
#                     "full_name": body.full_name,
#                     "phone": body.phone,
#                     "is_super_admin": True,
#                     "type": "",
#                 },
#             }
#         )
#         return supabase_response.user.id

#     except (ConnectionError, TimeoutError, ValueError) as supabase_error:
#         if (
#             "already_exists" in str(supabase_error).lower()
#             or "duplicate" in str(supabase_error).lower()
#         ):
#             raise HTTPException(
#                 status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
#             ) from supabase_error
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to create user account",
#         ) from supabase_error


async def sign_up_supabase_user(body):
    """
    Create user in Supabase Auth using auth.signUp for user-initiated registration.
    This is for user signup (like in signup API) and requires email confirmation.

    Args:
        body: Request body with user data
organization_id: Organization ID to associate with user

    Returns:
        dict: Supabase auth response containing user and session information

    Raises:
        HTTPException: For duplicate email or Supabase errors
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
                    }
                }
            }
        )
        if not supabase_response.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to create user account"
            )

        return supabase_response

    except HTTPException:
        # Re-raise HTTPExceptions as-is (e.g., from the user=None check above)
        raise
    except Exception as supabase_error:
        # Check for duplicate email/user errors FIRST, regardless of exception type
        error_message = str(supabase_error).lower()
        error_type = type(supabase_error).__name__

        # Log for debugging
        logger.warning("Signup error - Type: %s, Message: %s", error_type, error_message)

        # Check error message and also check error attributes if available
        error_str_lower = error_message
        if hasattr(supabase_error, 'message'):
            error_str_lower += " " + str(supabase_error.message).lower()
        if hasattr(supabase_error, 'detail'):
            error_str_lower += " " + str(supabase_error.detail).lower()
        if hasattr(supabase_error, 'args') and supabase_error.args:
            error_str_lower += " " + " ".join(str(arg).lower() for arg in supabase_error.args)

        if (
            "already_exists" in error_str_lower
            or "duplicate" in error_str_lower
            or "already registered" in error_str_lower
            or "user already registered" in error_str_lower
            or "email already" in error_str_lower
            or "user already exists" in error_str_lower
            or "already been registered" in error_str_lower
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            ) from supabase_error

        # Handle specific exception types for better error messages
        if isinstance(supabase_error, (ConnectionError, TimeoutError)):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily unavailable. Please try again later.",
            ) from supabase_error

        if isinstance(supabase_error, (ValueError, AuthApiError)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request: {str(supabase_error)}",
            ) from supabase_error

        # Default to 500 for unexpected errors
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
    except AuthApiError as error:
        if error.status == 400 and error.message == "Email not confirmed":
            raise HTTPException(status_code=403, detail="Email not confirmed. Please check your email Inbox for the confirmation link.") from error
    except Exception as error:
        log_exception()
        logger.error(error)
        raise


async def invite_user_with_email(body: CreateUserRequest, user_context: UserContext) -> str:
    """
    Invite user with email using Supabase Auth Admin API.

    Args:
        body (CreateUserRequest): Request body with user data
        user_context (UserContext): User context containing organization ID

    Returns:
        str: Created user ID

    Raises:
        HTTPException: For duplicate email or Supabase errors
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

        logger.error("Email: %s, Error: %s",body.email,str(e))
        raise HTTPException(
            status_code=409,
            detail=str(e),
        ) from e

async def reset_the_password_email(email: str):
    """
    Reset password email using Supabase Auth Admin API.
    """
    try:
        supabase = await get_fresh_supabase_admin_client()
        return await supabase.auth.reset_password_email(email)

    except (AttributeError, TypeError) as e:
        logger.error("AttributeError while resetting password email: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error while sending password reset email.",
        ) from e
    except ValueError as e:
        logger.error("ValueError while resetting password email: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address provided.",
        ) from e
    except AuthApiError as e:
        logger.error("Supabase Auth API error while resetting password email: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send password reset email due to authentication service error.",
        ) from e
    except Exception as e:
        logger.error("Unexpected error while resetting password email: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while sending password reset email.",
        ) from e

async def update_password_with_token(token: str, new_password: str) -> dict:
    """
    Update password with token using Supabase Auth Admin API.
    """
    try:
        supabase = await get_supabase_admin_client()
        return await supabase.auth.admin.update_user_by_id(token, {"password": new_password})
    except Exception as e:
        logger.error("Unexpected error while updating password with token: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while updating password with token.",
        ) from e

async def supabase_user_oauth(provider: str) -> dict:
    """
    Link user identity using Supabase Auth Admin API.
    """
    try:
        supabase = await get_supabase_admin_client()
        _provider_validity_check(provider)

        # Get the Supabase URL dynamically from the client
        supabase_url = supabase.supabase_url
        base_url = f"{supabase_url}/auth/v1/authorize"

        params = {
            "provider": provider,
            "redirect_to": "http://localhost:5000/v1/admin/auth/oauth-callback",
            "code_challenge": CODE_CHALLENGE,
            "code_challenge_method": "S256"
        }

        # Construct the URL manually with our custom PKCE parameters
        query_string = urllib.parse.urlencode(params)
        oauth_url = f"{base_url}?{query_string}"

        return {
            "provider": provider,
            "url": oauth_url,
            "code_verifier": CODE_VERIFIER,  # Include this for reference
            "code_challenge": CODE_CHALLENGE
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error while creating OAuth URL: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while creating OAuth URL.",
        ) from e


async def get_oauth_link_url(user_id: str, user_email: str, provider: str) -> dict:
    """
    Generate Google OAuth URL for linking to existing email/password user.
    """
    try:
        supabase = await get_supabase_admin_client()
        _provider_validity_check(provider)

        # Generate OAuth URL with special parameters for linking
        base_url = f"{supabase.supabase_url}/auth/v1/authorize"

        params = {
            "provider": provider,
            "redirect_to": "http://localhost:5000/v1/admin/auth/oauth-callback",
            "user_id": user_id,
            "code_challenge": CODE_CHALLENGE,
            "code_challenge_method": "S256"
        }

        query_string = urllib.parse.urlencode(params)
        oauth_url = f"{base_url}?{query_string}"

        return {
            "success": True,
            "oauth_url": oauth_url,
            "user_email": user_email,
            "message": f"Redirect user to this URL to link {provider} account"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error generating %s OAuth URL: %s", provider, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate {provider} OAuth URL"
        )

async def link_google_identity_to_existing_user(user_id: str, google_user) -> bool:
    """
    Link Google identity to existing email/password user.
    """
    try:
        supabase_admin = await get_supabase_admin_client()

        # Update user to include Google provider
        result = await supabase_admin.auth.admin.update_user_by_id(
            user_id,
            {
                "app_metadata": {
                    "provider": "email",
                    "providers": ["email", "google"],
                    "google_identity": {
                        "google_id": google_user.id,
                        "avatar_url": google_user.user_metadata.get("avatar_url"),
                        "name": google_user.user_metadata.get("full_name"),
                        "verified_email": google_user.email_confirmed_at is not None
                    }
                }
            }
        )

        return result.user is not None

    except Exception as e:
        logger.error("Error linking Google identity: %s", str(e))
        return False

def _provider_validity_check(provider: str):
    """
    Check if provider is valid.
    """
    if provider not in get_args(AUTH_PROVIDER):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid provider"
        )

async def refresh_session(refresh_token: str) -> dict:
    """
    Refresh user session using Supabase Auth Admin API.
    """
    try:
        # supabase = await get_supabase_client()
        supabase = await get_supabase_admin_client()
        return await supabase.auth.refresh_session(refresh_token)
    except AuthApiError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Unexpected error while refreshing session: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while refreshing session.",
        ) from e