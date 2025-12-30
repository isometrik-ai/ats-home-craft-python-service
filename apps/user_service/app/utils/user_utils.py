"""User Utilities Module."""

from fastapi import HTTPException, status
from supabase import AsyncClient

from apps.user_service.app.db.repositories.organisation_member_repository import (
    OrganisationMemberRepository,
)
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.utils.email_utils import send_email
from libs.shared_db.supabase_db.auth_repository import generate_magic_link, update_email
from libs.shared_utils.common_query import USER_NOT_FOUND_MESSAGE

# Initialize logger
logger = get_logger("user-utils")


def build_full_name(*parts: str) -> str:
    """Build a full name from parts.

    Args:
        *parts: Parts of the full name

    Returns:
        str: Full name
    """
    return " ".join(filter(None, parts))


async def update_supabase_user_email(
    user_id: str,
    organization_id: str,
    email: str,
    organisation_member_repository: OrganisationMemberRepository,
    sb_client: AsyncClient,
):
    """Update user email and send magic link notification
    Args:
        user_id: User ID
        organization_id: Organization ID
        email: New email address
        organisation_member_repository: Repository instance for database operations
        sb_client: Supabase client
    """
    try:
        user_info = await organisation_member_repository.get_user_profile_by_id(
            user_id, organization_id
        )

        if user_info is None:
            raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

        # Update user email in Supabase Auth
        response = await update_email(sb_client, user_id, email)
        # Check if the update was successful
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user email",
            )

        # Update email in organization_members table
        result = await organisation_member_repository.update_user_email(
            user_id, organization_id, email
        )
        if not result:
            raise HTTPException(status_code=404, detail="Member not found")

        # Prepare user data for email notification
        user_data = {
            "id": user_id,
            "full_name": user_info.get("full_name", ""),
            "email": email,  # Use the new email
        }

        # Send magic link email notification
        email_sent = await send_admin_update_email(sb_client, user_data)

        if not email_sent:
            logger.warning("Failed to send magic link email to %s", email)
            # Note: We don't fail the entire operation if email fails
            # The email update was successful, only the notification failed

    except HTTPException:  # ⬅️ re-raise FastAPI errorfs untouched
        raise
    except Exception as e:  # ⬅️ handle every other failure
        logger.error("Error updating Supabase user email: %s", str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error") from e


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


async def send_admin_update_email(sb_client: AsyncClient, user: dict) -> bool:
    """Send admin update notification email with magic link
    Args:
        user: User information containing id, full_name, email
        sb_client: Supabase client
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Generate magic link using Supabase Auth Admin API
        magic_link = await generate_magic_link(sb_client, user.get("email"))

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
