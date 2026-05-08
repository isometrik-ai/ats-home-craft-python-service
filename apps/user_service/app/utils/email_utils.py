"""Email Utilities Module
This module provides shared email functionality for sending emails via Supabase Edge Functions.
"""

from datetime import datetime

import httpx

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

SUPABASE_URL = shared_settings.supabase.url
SERVICE_ROLE_KEY = shared_settings.supabase.service_key

COMMON_COMPANY_NAME = shared_settings.company_name
COMMON_SUPPORT_EMAIL = shared_settings.company_support_email
COMMON_COMPANY_ADDRESS = shared_settings.company_address
COMMON_PRIVACY_POLICY_URL = shared_settings.company_privacy_policy_url
COMMON_TERMS_URL = shared_settings.company_terms_url
ROSS_AI_FROM_NAME = shared_settings.app_name

logger = get_logger(__name__)


def send_email(
    email: str, subject: str, message: str, html: str = None, from_name: str = None
) -> bool:
    """Send an email using Supabase Edge Function with Resend.

    Args:
        email (str): Recipient's email address
        subject (str): Email subject
        message (str): Email message content (plain text)
        html (str, optional): HTML version of the email
        from_name (str, optional): Sender name to display in the email

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        payload = {"to": email, "subject": subject, "message": message}

        if html:
            payload["html"] = html

        if from_name:
            payload["from_name"] = from_name
        response = httpx.post(
            f"{SUPABASE_URL}/functions/v1/custom-email",
            headers={
                "apikey": SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        if response.status_code == 200:
            return True
        logger.error("Failed to send email: %s", response.text)
        return False
    except httpx.HTTPError as error:
        logger.error("Error sending email: %s", str(error))
        return False


def send_password_reset_confirmation_email(email: str, user_name: str = None) -> bool:
    """Send a password reset confirmation email to the user.

    Args:
        email (str): User's email address
        user_name (str, optional): User's name for personalization

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Create personalized greeting - use full name if available, otherwise use "User"
        greeting = user_name.strip() if user_name and user_name.strip() else "User"
        current_year = datetime.now().year

        subject = "Password Changed Successfully"

        # Plain text message
        message = f"""Hello {greeting},

We wanted to let you know that your password for your House of App AI account
was successfully updated.

If you did not make this change, please reset your password immediately and
contact our support team.

For security, we recommend you:
- Use a strong, unique password
- Avoid sharing your password with others
- Enable 2FA (if available)

If you have any questions, feel free to contact support.

Stay secure,
The House of App AI Team"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8" />
    <title>Password Changed Successfully</title>
    <style>
      body {{
        font-family: Arial, sans-serif;
        background-color: #f9fafb;
        margin: 0;
        padding: 0;
      }}
      .container {{
        max-width: 600px;
        margin: 20px auto;
        background: #ffffff;
        border-radius: 8px;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
        overflow: hidden;
      }}
      .header {{
        background: #4f46e5;
        color: #ffffff;
        padding: 16px 24px;
        text-align: center;
        font-size: 20px;
        font-weight: bold;
      }}
      .content {{
        padding: 24px;
        color: #111827;
        line-height: 1.6;
        font-size: 15px;
      }}
      .content p {{
        margin: 12px 0;
      }}
      .highlight {{
        background-color: #f3f4f6;
        padding: 12px;
        border-radius: 6px;
        font-size: 14px;
      }}
      .footer {{
        font-size: 12px;
        color: #6b7280;
        text-align: center;
        padding: 16px;
        border-top: 1px solid #e5e7eb;
      }}
      a {{
        color: #4f46e5;
        text-decoration: none;
        font-weight: 500;
      }}
    </style>
  </head>
  <body>
    <div class="container">
      <div class="header">Password Changed</div>
      <div class="content">
        <p>Hello {greeting},</p>
        <p>
          We wanted to let you know that your password for your
          <strong>House of App AI</strong> account was successfully updated.
        </p>
        <div class="highlight">
          If you did not make this change, please reset your password immediately
          and contact our support team.
        </div>
        <p>
          For security, we recommend you:
        </p>
        <ul>
          <li>Use a strong, unique password</li>
          <li>Avoid sharing your password with others</li>
          <li>Enable 2FA (if available)</li>
        </ul>
        <p>
          If you have any questions, feel free to
          <a href="mailto:support@yourdomain.com">contact support</a>.
        </p>
        <p>Stay secure,<br>
The House of App AI Team</p>
      </div>
      <div class="footer">
        © {current_year} House of App AI. All rights reserved.
      </div>
    </div>
  </body>
</html>"""

        # Send the email with HTML content
        email_sent = send_email(email, subject, message, html_message)

        if email_sent:
            logger.info("Password reset confirmation email sent successfully to %s", email)
            return True
        logger.error("Failed to send password reset confirmation email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending password reset confirmation email: %s", str(error))
        return False


def send_organization_invitation_email(
    email: str,
    organization_name: str,
    inviter_name: str,
    invitee_name: str,
    invite_url: str,
    role_name: str,
    expires_at: str,
) -> bool:
    """Send an organization invitation email to a user.

    Args:
        email (str): Recipient's email address
        organization_name (str): Name of the organization
        inviter_name (str): Name of the person who sent the invitation
        invitee_name (str): Name of the person being invited
        invite_url (str): Invitation acceptance URL
        role_name (str): Role being offered (owner, admin, member)
        expires_at (str): Expiration date/time

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        subject = f"You're invited to join {organization_name}"

        # Format expires_at to human-readable format
        try:
            # Handle datetime objects directly
            if isinstance(expires_at, datetime):
                expires_datetime = expires_at
            else:
                # Parse ISO format datetime string (handle both Z and +00:00 formats)
                expires_at_clean = str(expires_at).replace("Z", "+00:00")
                expires_datetime = datetime.fromisoformat(expires_at_clean)

            # Format as human-readable: "December 9, 2025 at 11:41 AM UTC"
            # If datetime has timezone info, format with UTC label
            # Otherwise, format without timezone label
            if expires_datetime.tzinfo:
                formatted_expires_at = expires_datetime.strftime("%B %d, %Y at %I:%M %p UTC")
            else:
                # No timezone info, format without timezone
                formatted_expires_at = expires_datetime.strftime("%B %d, %Y at %I:%M %p")
        except (ValueError, AttributeError) as date_error:
            # Fallback to original format if parsing fails
            logger.warning(
                "Failed to parse expires_at date: %s, using original format",
                str(date_error),
            )
            formatted_expires_at = expires_at

        # Plain text message
        message = f"""You're invited to join {organization_name}!

{inviter_name} has invited you to join {organization_name} as a {role_name}.

To accept this invitation, click the link below:
{invite_url}

This invitation will expire on {formatted_expires_at}.

If you don't want to join this organization, you can simply ignore this email.

Best regards,
The {organization_name} Team"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Organization Invitation</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f9fafb;
        }}
        .container {{
            background: #ffffff;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
            overflow: hidden;
        }}
        .header {{
            background: #4f46e5;
            color: #ffffff;
            padding: 20px;
            text-align: center;
            font-size: 20px;
            font-weight: bold;
        }}
        .content {{
            padding: 24px;
            color: #111827;
            line-height: 1.6;
            font-size: 15px;
        }}
        .content p {{
            margin: 12px 0;
        }}
        .button {{
            display: inline-block;
            background-color: #4f46e5 !important;
            color: #ffffff !important;
            padding: 12px 24px;
            text-decoration: none !important;
            border-radius: 6px;
            margin: 20px 0;
            font-weight: 500;
            border: none;
            text-align: center;
        }}
        .button:link, .button:visited, .button:hover, .button:active {{
            color: #ffffff !important;
            text-decoration: none !important;
        }}
        .highlight {{
            background-color: #f3f4f6;
            padding: 12px;
            border-radius: 6px;
            font-size: 14px;
            margin: 16px 0;
        }}
        .footer {{
            font-size: 12px;
            color: #6b7280;
            text-align: center;
            padding: 16px;
            border-top: 1px solid #e5e7eb;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">You're invited to join {organization_name}!</div>

        <div class="content">
            <p>Hello {invitee_name},</p>

            <p>
                <strong>{inviter_name}</strong> has invited you to join
                <strong>{organization_name}</strong> as a <strong>{role_name}</strong>.
            </p>

            <p>To accept this invitation, click the button below:</p>

            <div style="margin: 20px 0;">
                <a href="{invite_url}" class="button"
                   style="display: inline-block; background-color: #4f46e5;
                   color: #ffffff !important; padding: 12px 24px;
                   text-decoration: none !important; border-radius: 6px;
                   font-weight: 500; border: none;">Accept Invitation</a>
            </div>

            <div class="highlight">
                <strong>Important:</strong> This invitation will expire on
                <strong>{formatted_expires_at}</strong>.
            </div>

            <p>If you don't want to join this organization, you can simply ignore this email.</p>
        </div>

        <div class="footer">
            <p>Best regards,<br>The {organization_name} Team</p>
        </div>
    </div>
</body>
</html>"""

        # Send the email with HTML content
        email_sent = send_email(email, subject, message, html_message)

        if email_sent:
            logger.info("Organization invitation email sent successfully to %s", email)
            return True
        logger.error("Failed to send organization invitation email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending organization invitation email: %s", str(error))
        return False


def send_welcome_email(
    email: str,
    first_name: str,
    company_name: str = COMMON_COMPANY_NAME,
    dashboard_url: str = "https://house-of-apps-legal-ai-front-end.vercel.app/",
    support_email: str = COMMON_SUPPORT_EMAIL,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send a welcome email to newly signed up users.

    Args:
        email (str): User's email address
        first_name (str): User's first name
        company_name (str): Company name (default: "House of App AI")
        dashboard_url (str): Dashboard URL for the call-to-action button
        support_email (str): Support email address
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        current_year = datetime.now().year
        creation_date = datetime.now().strftime("%B %d, %Y")

        subject = f"Welcome to {company_name}!"

        # Plain text message
        message = f"""Hello {first_name},

Thank you for signing up with us on {creation_date}. We are thrilled to have
you join our community. Our platform is designed to help you manage your
practice efficiently and effectively.

To get started, visit your dashboard: {dashboard_url}

If you have any questions or need assistance, feel free to reach out to our
support team at {support_email}.

Best regards,
The {company_name} Team"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>Welcome to {company_name}</title>
    <style>
        /* Reset styles */
        body, table, td, a {{ -webkit-text-size-adjust: 100%;
        -ms-text-size-adjust: 100%; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; height: auto;
        line-height: 100%; outline: none; text-decoration: none; }}
        body {{ margin: 0; padding: 0; width: 100% !important;
        height: 100% !important; }}

        /* Mobile Responsive Styles */
        @media only screen and (max-width: 600px) {{
            .email-container {{ width: 100% !important; }}
            .mobile-padding {{ padding: 15px !important; }}
            .mobile-font-size {{ font-size: 16px !important; }}
            .button {{ width: 100% !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f9fafb;
font-family: Arial, Helvetica, sans-serif;">

    <!-- Preview Text (hidden but shows in email preview) -->
    <div style="display: none; max-height: 0px; overflow: hidden;">
        Welcome to {company_name}! We're excited to have you on board.
    </div>

    <!-- Email Container -->
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"
        style="background-color: #f9fafb;">
        <tr>
            <td style="padding: 40px 20px;">

                <!-- Main Email Table -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600"
                    class="email-container"
                    style="margin: 0 auto; background-color: #ffffff; border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

                    <!-- Header -->
                    <tr>
                        <td style="padding: 30px 40px; text-align: center;
                            border-bottom: 1px solid #e5e7eb;">
                            <h1 style="margin: 0; color: #1e3a8a; font-size: 24px;
                                font-weight: bold;">
                                Welcome to {company_name}
                            </h1>
                        </td>
                    </tr>

                    <!-- Body Content -->
                    <tr>
                        <td style="padding: 40px;" class="mobile-padding">

                            <!-- Greeting -->
                            <p style="margin: 0 0 20px 0; color: #1f2937; font-size: 16px;
                                line-height: 1.6;">
                                Hello {first_name},
                            </p>

                            <!-- Main Message -->
                            <p style="margin: 0 0 20px 0; color: #4b5563;
                            font-size: 15px; line-height: 1.6;">
                                Thank you for signing up with us on {creation_date}.
                                We are thrilled to have you join our community.
                                Our platform is designed to help you manage your
                                practice efficiently and effectively.
                            </p>

                            <!-- Call-to-Action Button -->
                            <table role="presentation" cellspacing="0" cellpadding="0"
                            border="0" width="100%" style="margin: 30px 0;">
                                <tr>
                                    <td style="text-align: center;">
                                        <a href="{dashboard_url}"
                                        style="display: inline-block; padding: 14px 40px;
                                        background-color: #3b82f6; color: #ffffff;
                                        text-decoration: none; border-radius: 6px;
                                        font-size: 16px; font-weight: 600;
                                        min-width: 200px;">
                                            Go to Dashboard
                                        </a>
                                    </td>
                                </tr>
                            </table>

                            <!-- Additional Information -->
                            <p style="margin: 20px 0 0 0; color: #6b7280; font-size: 14px;
                                line-height: 1.6;">
                                If you have any questions or need assistance, feel free to
                                reach out to our support team.
                            </p>

                        </td>
                    </tr>

                    <!-- Support Section -->
                    <tr>
                        <td style="padding: 30px 40px; background-color: #f9fafb;
                            border-top: 1px solid #e5e7eb;">
                            <p style="margin: 0 0 10px 0; color: #6b7280; font-size: 14px;
                                line-height: 1.5;">
                                Questions or need assistance?
                            </p>
                            <p style="margin: 0; color: #6b7280; font-size: 14px;
                                line-height: 1.5;">
                                Contact our support team at
                                <a href="mailto:{support_email}"
                                style="color: #3b82f6; text-decoration: none;">
                                {support_email}</a>
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding: 30px 40px; text-align: center;
                            border-top: 1px solid #e5e7eb;">
                            <p style="margin: 0 0 10px 0; color: #9ca3af; font-size: 13px;
                                line-height: 1.5;">
                                © {current_year} {company_name}. All rights reserved.
                            </p>
                            <p style="margin: 0 0 15px 0; color: #9ca3af; font-size: 12px;
                                line-height: 1.5;">
                                {company_address}
                            </p>
                            <p style="margin: 0; color: #9ca3af; font-size: 12px;">
                                <a href="{privacy_policy_url}"
                                style="color: #9ca3af; text-decoration: underline;">
                                Privacy Policy</a> |
                                <a href="{terms_url}"
                                style="color: #9ca3af; text-decoration: underline;">
                                Terms of Service</a>
                            </p>
                        </td>
                    </tr>

                </table>

            </td>
        </tr>
    </table>

</body>
</html>"""

        # Send the email with HTML content and sender name "Ross.Ai"
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Welcome email sent successfully to %s", email)
            return True
        logger.error("Failed to send welcome email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending welcome email: %s", str(error))
        return False


def send_password_change_success_email(
    email: str,
    user_name: str = None,
    company_name: str = COMMON_COMPANY_NAME,
    support_email: str = COMMON_SUPPORT_EMAIL,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send a password change success email to the user.

    Args:
        email (str): User's email address
        user_name (str, optional): User's name for personalization
        company_name (str): Company name (default: "House of App AI")
        support_email (str): Support email address
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Create personalized greeting - use name if available, otherwise use "User"
        name = user_name.strip() if user_name and user_name.strip() else "User"
        current_year = datetime.now().year

        subject = "Password Changed Successfully"

        # Plain text message
        message = f"""Hello {name},

This is a confirmation that your password for {company_name} has been
successfully updated.

If you made this change, no further action is needed.

If you did not update your password, please contact us immediately
for assistance.

Contact Support: {support_email}

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        # HTML message using the provided template
        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Password Updated</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04);">

    <div style="padding:30px; text-align:center; border-bottom:1px solid #eee;">
      <h2 style="margin:0; color:#1f3a93;">Password Updated Successfully</h2>
    </div>
    <div style="padding:30px; font-size:15px; color:#444; line-height:1.6;">
      <p>Hello {name},</p>
      <p>This is a confirmation that your password for
      <strong>{company_name}</strong> has been successfully updated.</p>
      <p>If you made this change, no further action is needed.</p>
      <p>If you did <strong>not</strong> update your password, please contact us
      immediately for assistance.</p>
      <p style="margin-top:25px;">
        📩 <strong>Contact Support:</strong><br />
        <a href="mailto:{support_email}" style="color:#2f76ff;">{support_email}</a>
      </p>
    </div>
  </div>
  <div style="max-width:600px; margin:20px auto; text-align:center; font-size:13px; color:#777;">
    <p style="color:#aaa;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}
    </p>
    <p>
      <a href="{privacy_policy_url}" style="color:#6c7ae0;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6c7ae0;">Terms of Service</a>
    </p>
  </div>
</body>
</html>"""

        # Send the email with HTML content and sender name "Ross.Ai"
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Password change success email sent successfully to %s", email)
            return True
        logger.error("Failed to send password change success email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending password change success email: %s", str(error))
        return False


def send_password_reset_success_email(
    email: str,
    user_name: str = None,
    company_name: str = COMMON_COMPANY_NAME,
    support_email: str = COMMON_SUPPORT_EMAIL,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send a password reset success email to the user.

    Args:
        email (str): User's email address
        user_name (str, optional): User's name for personalization
        company_name (str): Company name (default: "House of App AI")
        support_email (str): Support email address
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Create personalized greeting - use name if available, otherwise use "User"
        name = user_name.strip() if user_name and user_name.strip() else "User"
        current_year = datetime.now().year

        subject = "Password Reset Successful"

        # Plain text message
        message = f"""Hello {name},

Your password for {company_name} has been reset successfully.

If you requested this reset, you're all set.

If you didn't request a password reset, your account may be at risk.
Please reach out to us immediately.

Contact Support: {support_email}

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        # HTML message using the provided template
        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Password Reset Completed</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04);">

    <div style="padding:30px; text-align:center; border-bottom:1px solid #eee;">
      <h2 style="margin:0; color:#1f3a93;">Your Password Has Been Reset</h2>
    </div>

    <div style="padding:30px; font-size:15px; color:#444; line-height:1.6;">
      <p>Hello {name},</p>

      <p>Your password for <strong>{company_name}</strong> has been reset successfully.</p>

      <p>If you requested this reset, you're all set.</p>

      <p>If you didn't request a password reset, your account may be at risk.
      Please reach out to us immediately.</p>

      <p style="margin-top:25px;">
        📩 <strong>Contact Support:</strong><br />
        <a href="mailto:{support_email}" style="color:#2f76ff;">{support_email}</a>
      </p>
    </div>

  </div>

  <div style="max-width:600px; margin:20px auto; text-align:center; font-size:13px; color:#777;">
    <p style="color:#aaa;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}
    </p>
    <p>
      <a href="{privacy_policy_url}" style="color:#6c7ae0;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6c7ae0;">Terms of Service</a>
    </p>
  </div>

</body>
</html>"""

        # Send the email with HTML content and sender name "Ross.Ai"
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Password reset success email sent successfully to %s", email)
            return True
        logger.error("Failed to send password reset success email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending password reset success email: %s", str(error))
        return False


def send_verification_code_email(
    email: str,
    otp_code: str,
    expiry_minutes: int = 10,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send a verification code (OTP) email to the user.

    Args:
        email (str): User's email address
        otp_code (str): The OTP verification code to send
        expiry_minutes (int): Expiry time in minutes (default: 10)
        company_name (str): Company name (default: "House of App AI")
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        current_year = datetime.now().year

        subject = "Your Verification Code"

        # Plain text message
        message = f"""Hello 👋,

Use the following One-Time Password (OTP) to verify your email or complete your signup:

{otp_code}

This code will expire in {expiry_minutes} minutes.

If you didn't request this, please ignore this email.

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        # HTML message using the provided template
        html_message = f"""<!DOCTYPE html>
<html lang="en" style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Your Verification Code</title>
  </head>
  <body style="background-color: #f8f9fb; padding: 0; margin: 0;">
    <table width="100%" cellpadding="0" cellspacing="0"
        style="background-color: #f8f9fb; padding: 40px 0;">
      <tr>
        <td align="center">
          <table width="600" cellpadding="0" cellspacing="0"
              style="background-color: #ffffff; border-radius: 12px; overflow: hidden;
              box-shadow: 0 4px 8px rgba(0,0,0,0.05);">
            <!-- Header -->
            <tr>
              <td style="background-color: #1d4ed8; padding: 30px 40px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 22px;">Verify Your Identity</h1>
              </td>
            </tr>
            <!-- Body -->
            <tr>
              <td style="padding: 40px; text-align: center;">
                <p style="color: #111827; font-size: 16px; line-height: 1.6; margin-bottom: 20px;">
                  Hello 👋,
                </p>
                <p style="color: #4b5563; font-size: 16px; line-height: 1.6;">
                  Use the following One-Time Password (OTP) to verify your email or
                  complete your signup:
                </p>
                <div style="margin: 30px 0;">
                  <div style="display: inline-block; background-color: #f3f4f6;
                      padding: 16px 40px; border-radius: 8px; font-size: 28px;
                      font-weight: bold; letter-spacing: 6px; color: #1d4ed8;">
                    {otp_code}
                  </div>
                </div>
                <p style="color: #6b7280; font-size: 14px; line-height: 1.5;">
                  This code will expire in <strong>{expiry_minutes} minutes</strong>.
                  If you didn't request this, please ignore this email.
                </p>
              </td>
            </tr>
            <!-- Footer -->
            <tr>
              <td style="background-color: #f3f4f6; text-align: center; padding: 20px;
                  font-size: 13px; color: #6b7280;">
                <p style="margin: 0;">&copy; {current_year} {company_name}.
                All rights reserved.</p>
                <p style="margin: 4px 0;">{company_address}</p>
                <p style="margin: 0;">
                  <a href="{privacy_policy_url}"
                  style="color: #6b7280; text-decoration: none;">Privacy Policy</a> |
                  <a href="{terms_url}"
                  style="color: #6b7280; text-decoration: none;">Terms of Service</a>
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

        # Send the email with HTML content
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Verification code email sent successfully to %s", email)
            return True
        logger.error("Failed to send verification code email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending verification code email: %s", str(error))
        return False


def send_organization_delete_request_email(
    email: str,
    organization_name: str,
    requester_email: str,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send an organization delete request notification email to super admin users.

    Args:
        email (str): Super admin's email address
        organization_name (str): Name of the organization requested for deletion
        requester_email (str): Email of the user who requested the deletion
        company_name (str): Company name (default: "House of App AI")
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        current_year = datetime.now().year

        subject = f"Organization Deletion Request: {organization_name}"

        # Plain text message
        message = f"""Hello,

A deletion request has been submitted for the organization "{organization_name}".

Requested by: {requester_email}

Please review and take appropriate action.

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Organization Deletion Request</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04);">

    <div style="padding:30px; text-align:center; border-bottom:1px solid #eee;
        background-color:#dc2626;">
      <h2 style="margin:0; color:#ffffff;">Organization Deletion Request</h2>
    </div>
    <div style="padding:30px; font-size:15px; color:#444; line-height:1.6;">
      <p>Hello,</p>
      <p>A deletion request has been submitted for the organization:</p>
      <div style="background-color:#f3f4f6; padding:16px; border-radius:6px; margin:20px 0;">
        <p style="margin:0; font-size:18px; font-weight:bold; color:#1f2937;">
          {organization_name}
        </p>
      </div>
      <p><strong>Requested by:</strong> {requester_email}</p>
      <p style="margin-top:25px; padding:12px; background-color:#fef2f2;
          border-left:4px solid #dc2626; border-radius:4px;">
        Please review and take appropriate action.
      </p>
    </div>
  </div>
  <div style="max-width:600px; margin:20px auto; text-align:center; font-size:13px; color:#777;">
    <p style="color:#aaa;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}
    </p>
    <p>
      <a href="{privacy_policy_url}" style="color:#6c7ae0;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6c7ae0;">Terms of Service</a>
    </p>
  </div>
</body>
</html>"""

        # Send the email with HTML content and sender name "Ross.Ai"
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Organization delete request email sent successfully to %s", email)
            return True
        logger.error("Failed to send organization delete request email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending organization delete request email: %s", str(error))
        return False


def send_organization_deletion_approved_email(
    email: str,
    organization_name: str,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send an organization deletion approved notification email to organization members.

    Args:
        email (str): Organization member's email address
        organization_name (str): Name of the organization that was deleted
        company_name (str): Company name (default: "House of App AI")
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        current_year = datetime.now().year

        subject = f"Organization Deletion Confirmed: {organization_name}"

        # Plain text message
        message = f"""Hello,

Your organization "{organization_name}" has been permanently deleted from our system.

All associated data including user accounts, roles, permissions, and teams have been removed.

If you have any questions or concerns, please contact our support team.

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Organization Deletion Confirmed</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04);">

    <div style="padding:30px; text-align:center; border-bottom:1px solid #eee;
        background-color:#dc2626;">
      <h2 style="margin:0; color:#ffffff;">Organization Deletion Confirmed</h2>
    </div>
    <div style="padding:30px; font-size:15px; color:#444; line-height:1.6;">
      <p>Hello,</p>
      <p>Your organization has been permanently deleted from our system:</p>
      <div style="background-color:#f3f4f6; padding:16px; border-radius:6px; margin:20px 0;">
        <p style="margin:0; font-size:18px; font-weight:bold; color:#1f2937;">
          {organization_name}
        </p>
      </div>
      <p style="margin-top:25px; padding:12px; background-color:#fef2f2;
          border-left:4px solid #dc2626; border-radius:4px;">
        <strong>Important:</strong> All associated data including user accounts, roles,
        permissions, and teams have been permanently removed.
      </p>
      <p>If you have any questions or concerns, please contact our support team.</p>
    </div>
  </div>
  <div style="max-width:600px; margin:20px auto; text-align:center; font-size:13px; color:#777;">
    <p style="color:#aaa;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}
    </p>
    <p>
      <a href="{privacy_policy_url}" style="color:#6c7ae0;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6c7ae0;">Terms of Service</a>
    </p>
  </div>
</body>
</html>"""

        # Send the email with HTML content
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Organization deletion approved email sent successfully to %s", email)
            return True
        logger.error("Failed to send organization deletion approved email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending organization deletion approved email: %s", str(error))
        return False


def send_organization_deletion_rejected_email(
    email: str,
    organization_name: str,
    rejection_reason: str,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send an organization deletion rejection notification email to the requester.

    Args:
        email (str): Requester's email address
        organization_name (str): Name of the organization
        rejection_reason (str): Reason for rejection
        company_name (str): Company name (default: "House of App AI")
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        current_year = datetime.now().year

        subject = f"Organization Deletion Request Rejected: {organization_name}"

        # Plain text message
        message = f"""Hello,

Your request to delete the organization "{organization_name}" has been rejected.

Reason: {rejection_reason}

Your organization remains active. If you have any questions, please contact our support team.

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Organization Deletion Request Rejected</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04);">

    <div style="padding:30px; text-align:center; border-bottom:1px solid #eee;
        background-color:#f59e0b;">
      <h2 style="margin:0; color:#ffffff;">Deletion Request Rejected</h2>
    </div>
    <div style="padding:30px; font-size:15px; color:#444; line-height:1.6;">
      <p>Hello,</p>
      <p>Your request to delete the following organization has been rejected:</p>
      <div style="background-color:#f3f4f6; padding:16px; border-radius:6px; margin:20px 0;">
        <p style="margin:0; font-size:18px; font-weight:bold; color:#1f2937;">
          {organization_name}
        </p>
      </div>
      <div style="margin-top:25px; padding:12px; background-color:#fef3c7;
          border-left:4px solid #f59e0b; border-radius:4px;">
        <p style="margin:0 0 8px 0;"><strong>Reason:</strong></p>
        <p style="margin:0; color:#78350f;">{rejection_reason}</p>
      </div>
      <p style="margin-top:25px;">Your organization remains active. If you have any
      questions, please contact our support team.</p>
    </div>
  </div>
  <div style="max-width:600px; margin:20px auto; text-align:center; font-size:13px; color:#777;">
    <p style="color:#aaa;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}
    </p>
    <p>
      <a href="{privacy_policy_url}" style="color:#6c7ae0;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6c7ae0;">Terms of Service</a>
    </p>
  </div>
</body>
</html>"""

        # Send the email with HTML content
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Organization deletion rejected email sent successfully to %s", email)
            return True
        logger.error("Failed to send organization deletion rejected email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending organization deletion rejected email: %s", str(error))
        return False


def send_client_creation_email(
    email: str,
    organization_name: str,
    password: str | None = None,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Send a client creation email to the user.

    Args:
        email (str): User's email address
        organization_name (str): Name of the organization
        password (str, optional): User's password to include in the email
        company_name (str): Company name (default: "House of App AI")
        company_address (str): Company address for footer
        privacy_policy_url (str): Privacy policy URL
        terms_url (str): Terms of service URL

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        current_year = datetime.now().year

        subject = f"Welcome to {organization_name} - Client Account Created"

        # Build password section for plain text
        password_section = ""
        if password:
            password_section = f"""

Your login credentials:
Email: {email}
Password: {password}

Please change your password after your first login for security purposes.
"""

        # Plain text message
        message = f"""Hello,

Your client account has been successfully created with {organization_name}.

Organization: {organization_name}
{password_section}
You can now access your client portal and manage your account.

If you have any questions, please contact the organization support team.

Best regards,
{organization_name} Team"""

        # HTML message
        html_message = f"""<!DOCTYPE html>
<html lang="en" style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Client Account Created</title>
  </head>
  <body style="background-color: #f8f9fb; padding: 0; margin: 0;">
    <table width="100%" cellpadding="0" cellspacing="0"
        style="background-color: #f8f9fb; padding: 40px 0;">
      <tr>
        <td align="center">
          <table width="600" cellpadding="0" cellspacing="0"
              style="background-color: #ffffff; border-radius: 12px; overflow: hidden;
              box-shadow: 0 4px 8px rgba(0,0,0,0.05);">
            <!-- Header -->
            <tr>
              <td style="background-color: #1d4ed8; padding: 30px 40px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 22px;">Client Account Created</h1>
              </td>
            </tr>
            <!-- Body -->
            <tr>
              <td style="padding: 40px;">
                <p style="color: #111827; font-size: 16px; line-height: 1.6; margin-bottom: 20px;">
                  Hello,
                </p>
                <p style="color: #4b5563; font-size: 16px; line-height: 1.6; margin-bottom: 20px;">
                  Your client account has been successfully created with
                  <strong>{organization_name}</strong>.
                </p>
                <div style="background-color: #f3f4f6; padding: 20px; border-radius: 8px; margin: 20px 0;">
                  <p style="margin: 5px 0; color: #111827;">
                  <strong>Organization:</strong> {organization_name}</p>
                </div>
                {
            f'''
                <div style="background-color: #eff6ff; border-left: 4px solid #1d4ed8; padding: 20px; border-radius: 8px; margin: 20px 0;">
                  <p style="margin: 0 0 10px 0; color: #111827; font-weight: 600; font-size: 16px;">
                    Your Login Credentials:
                  </p>
                  <p style="margin: 5px 0; color: #111827;">
                    <strong>Email:</strong> {email}
                  </p>
                  <p style="margin: 5px 0; color: #111827;">
                    <strong>Password:</strong>
                    <code style="background-color: #ffffff; padding: 4px 8px; border-radius: 4px; font-family: monospace; font-size: 14px;">
                    {password}
                    </code>
                  </p>
                  <p style="margin: 10px 0 0 0; color: #dc2626; font-size: 14px;">
                    ⚠️ Please change your password after your first login for security purposes.
                  </p>
                </div>
                '''
            if password
            else ""
        }
                <p style="color: #4b5563; font-size: 16px; line-height: 1.6;">
                  You can now access your client portal and manage your account.
                </p>
                <p style="color: #4b5563; font-size: 16px; line-height: 1.6;">
                  If you have any questions, please contact the organization support team.
                </p>
                <p style="color: #111827; font-size: 16px; margin-top: 30px;">
                  Best regards,<br>
                  {organization_name} Team
                </p>
              </td>
            </tr>
            <!-- Footer -->
            <tr>
              <td style="background-color: #f3f4f6; text-align: center; padding: 20px;
                  font-size: 13px; color: #6b7280;">
                <p style="margin: 0;">This is an automated message from {organization_name}.</p>
                <p style="margin: 4px 0;">
                &copy; {current_year} {company_name}. All rights reserved.
                </p>
                <p style="margin: 4px 0;">{company_address}</p>
                <p style="margin: 0;">
                  <a href="{privacy_policy_url}"
                  style="color: #6b7280; text-decoration: none;">Privacy Policy</a> |
                  <a href="{terms_url}"
                  style="color: #6b7280; text-decoration: none;">Terms of Service</a>
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

        # Send the email with HTML content and sender name "Ross.Ai"
        email_sent = send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)

        if email_sent:
            logger.info("Client creation email sent successfully to %s", email)
            return True
        logger.error("Failed to send client creation email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending client creation email: %s", str(error))
        return False


def send_org_member_banned_email(
    *,
    email: str,
    organization_name: str,
    banned_by_email: str,
    support_email: str = COMMON_SUPPORT_EMAIL,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Notify a user that they were banned/suspended from an organization."""
    try:
        current_year = datetime.now().year
        subject = f"Access removed from {organization_name}"

        message = f"""Hello,

Your access to the organization "{organization_name}" has been removed by an
administrator ({banned_by_email}).

If you believe this is a mistake, please contact your organization admin or reach
out to support at {support_email}.

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Access removed</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04); overflow:hidden;">
    <div style="padding:20px 28px; background:#dc2626; color:#fff; font-weight:bold; text-align:center;">
      Access Removed
    </div>
    <div style="padding:28px; font-size:15px; color:#444; line-height:1.6;">
      <p style="margin:0 0 14px 0;">Hello,</p>
      <p style="margin:0 0 14px 0;">
        Your access to <strong>{organization_name}</strong> has been removed by an administrator
        (<strong>{banned_by_email}</strong>).
      </p>
      <div style="background:#f3f4f6; padding:12px 14px; border-radius:6px; margin:18px 0;">
        If you believe this is a mistake, please contact your organization admin or email
        <a href="mailto:{support_email}" style="color:#2563eb; text-decoration:none;"
          >{support_email}</a>.
      </div>
      <p style="margin:18px 0 0 0;">Thanks,<br />{company_name} Team</p>
    </div>
    <div style="padding:16px 28px; background:#f9fafb; color:#6b7280; font-size:12px; text-align:center;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}<br />
      <a href="{privacy_policy_url}" style="color:#6b7280;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6b7280;">Terms of Service</a>
    </div>
  </div>
</body>
</html>"""

        return send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)
    except Exception as error:
        logger.error("Error sending org member banned email: %s", str(error))
        return False


def send_org_member_unbanned_email(
    *,
    email: str,
    organization_name: str,
    unbanned_by_email: str,
    support_email: str = COMMON_SUPPORT_EMAIL,
    company_name: str = COMMON_COMPANY_NAME,
    company_address: str = COMMON_COMPANY_ADDRESS,
    privacy_policy_url: str = COMMON_PRIVACY_POLICY_URL,
    terms_url: str = COMMON_TERMS_URL,
) -> bool:
    """Notify a user that they were unbanned/unsuspended for an organization."""
    try:
        current_year = datetime.now().year
        subject = f"Access restored for {organization_name}"

        message = f"""Hello,

Your access to the organization "{organization_name}" has been restored by an
administrator ({unbanned_by_email}).

You can now sign in and select the organization again.

If you have trouble accessing the organization, contact support at {support_email}.

© {current_year} {company_name}. All rights reserved.
{company_address}"""

        html_message = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Access restored</title>
</head>
<body style="margin:0; padding:40px 0; background:#f7f8fa; font-family:Arial, sans-serif;">
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px;
      box-shadow:0 2px 6px rgba(0,0,0,0.04); overflow:hidden;">
    <div style="padding:20px 28px; background:#16a34a; color:#fff; font-weight:bold; text-align:center;">
      Access Restored
    </div>
    <div style="padding:28px; font-size:15px; color:#444; line-height:1.6;">
      <p style="margin:0 0 14px 0;">Hello,</p>
      <p style="margin:0 0 14px 0;">
        Your access to <strong>{organization_name}</strong> has been restored by an administrator
        (<strong>{unbanned_by_email}</strong>).
      </p>
      <div style="background:#f3f4f6; padding:12px 14px; border-radius:6px; margin:18px 0;">
        You can now sign in and select the organization again.
      </div>
      <p style="margin:18px 0 0 0;">Thanks,<br />{company_name} Team</p>
    </div>
    <div style="padding:16px 28px; background:#f9fafb; color:#6b7280; font-size:12px; text-align:center;">
      © {current_year} {company_name}. All rights reserved.<br />
      {company_address}<br />
      <a href="{privacy_policy_url}" style="color:#6b7280;">Privacy Policy</a> |
      <a href="{terms_url}" style="color:#6b7280;">Terms of Service</a>
    </div>
  </div>
</body>
</html>"""

        return send_email(email, subject, message, html_message, from_name=ROSS_AI_FROM_NAME)
    except Exception as error:
        logger.error("Error sending org member unbanned email: %s", str(error))
        return False
