"""User Utilities Module."""

from fastapi import HTTPException, status
from supabase import AsyncClient

from apps.user_service.app.db.repositories import (
    OrganizationMemberRepository,
    OrganizationRepository,
)
from apps.user_service.app.schemas.auth import IsometrikDetails
from apps.user_service.app.utils.common_utils import parse_json_field
from apps.user_service.app.utils.email_utils import send_email
from libs.shared_db.supabase_db.auth_repository import generate_magic_link, update_email
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.isometrik_service import login_to_isometrik
from libs.shared_utils.logger import get_logger  # Logger import
from libs.shared_utils.status_codes import CustomStatusCode

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
    organization_member_repository: OrganizationMemberRepository,
    sb_client: AsyncClient,
):
    """Update user email and send magic link notification
    Args:
        user_id: User ID
        organization_id: Organization ID
        email: New email address
        organization_member_repository: Repository instance for database operations
        sb_client: Supabase client
    """
    try:
        user_info = await organization_member_repository.get_user_profile_by_id(
            user_id, organization_id
        )

        if user_info is None:
            raise HTTPException(status_code=404, detail="User not found in organization")

        # Update user email in Supabase Auth
        response = await update_email(sb_client, user_id, email)
        # Check if the update was successful
        if not response:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user email",
            )

        # Update email in organization_members table
        result = await organization_member_repository.update_user_email(
            user_id, organization_id, email
        )
        if not result:
            raise HTTPException(status_code=404, detail="User not found in organization")

        # Prepare user data for email notification
        user_data = {
            "id": user_id,
            "full_name": user_info.get("full_name", ""),
            "email": email,  # Use the new email
        }

        # Send magic link email notification
        await send_admin_update_email(sb_client, user_data)

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

        if not magic_link:
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


async def get_isometrik_details(
    user_id: str, organization_id: str, organization_repository: OrganizationRepository
) -> IsometrikDetails | None:
    """Get Isometrik details for a user.

    Args:
        user_id: User ID
        organization_id: Organization ID
        organization_repository: Organization repository
    Returns:
        dict | None: Isometrik details
    """
    organization = await organization_repository.get_organization_by_id(organization_id)
    if not organization:
        return None
    if organization and organization.get("status") != "active":
        raise ForbiddenException(
            message_key="organizations.errors.organization_not_active",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    org_settings = parse_json_field(organization.get("settings"))
    isometrik_credentials = org_settings.get("isometrik_application_details", {})
    isometrik_login_response = await login_to_isometrik(
        user_id=user_id,
        isometrik_credentials=isometrik_credentials,
    )
    isometrik_details = None
    if isometrik_login_response:
        isometrik_details = IsometrikDetails(
            user_id=isometrik_login_response.get("userId"),
            token=isometrik_login_response.get("userToken"),
            license_key=isometrik_credentials.get("licenseKey"),
            user_secret=isometrik_credentials.get("userSecret"),
            app_secret=isometrik_credentials.get("appSecret"),
        )
    return isometrik_details
