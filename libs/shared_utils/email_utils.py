"""
Email Utilities Module
This module provides shared email functionality for sending emails via Supabase Edge Functions.
"""

import os
import logging
from datetime import datetime

import httpx

# Configure logging
logger = logging.getLogger(__name__)

# Environment variables for email functionality
SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

COMMON_COMPANY_NAME = "House of App AI"
COMMON_SUPPORT_EMAIL = "support@houseofapp.ai"
COMMON_COMPANY_ADDRESS = "123 Main Street, City, State 12345"
COMMON_PRIVACY_POLICY_URL = "https://houseofapp.ai/privacy"
COMMON_TERMS_URL = "https://houseofapp.ai/terms"


def send_email(
    email: str,
    subject: str,
    message: str,
    html: str = None,
    from_name: str = None
) -> bool:
    """
    Send an email using Supabase Edge Function with Resend.

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
        payload = {
            "to": email,
            "subject": subject,
            "message": message
        }

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
    """
    Send a password reset confirmation email to the user.

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

We wanted to let you know that your password for your House of App AI account was successfully updated.

If you did not make this change, please reset your password immediately and contact our support team.

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
    expires_at: str
) -> bool:
    """
    Send an organization invitation email to a user.

    Args:
        email (str): Recipient's email address
        organization_name (str): Name of the organization
        inviter_name (str): Name of the person who sent the invitation
        invite_url (str): Invitation acceptance URL
        role_name (str): Role being offered (owner, admin, member)
        expires_at (str): Expiration date/time

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        subject = f"You're invited to join {organization_name}"

        # Plain text message
        message = f"""You're invited to join {organization_name}!

{inviter_name} has invited you to join {organization_name} as a {role_name}.

To accept this invitation, click the link below:
{invite_url}

This invitation will expire on {expires_at}.

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
            background-color: #4f46e5;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 6px;
            margin: 20px 0;
            font-weight: 500;
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

            <p><strong>{inviter_name}</strong> has invited you to join <strong>{organization_name}</strong> as a <strong>{role_name}</strong>.</p>

            <p>To accept this invitation, click the button below:</p>

            <a href="{invite_url}" class="button">Accept Invitation</a>

            <div class="highlight">
                <strong>Important:</strong> This invitation will expire on <strong>{expires_at}</strong>.
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
    terms_url: str = COMMON_TERMS_URL
) -> bool:
    """
    Send a welcome email to newly signed up users.

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

Thank you for signing up with us on {creation_date}. We are thrilled to have you join our community. Our platform is designed to help you manage your practice efficiently and effectively.

To get started, visit your dashboard: {dashboard_url}

If you have any questions or need assistance, feel free to reach out to our support team at {support_email}.

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
        body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; }}
        body {{ margin: 0; padding: 0; width: 100% !important; height: 100% !important; }}

        /* Mobile Responsive Styles */
        @media only screen and (max-width: 600px) {{
            .email-container {{ width: 100% !important; }}
            .mobile-padding {{ padding: 15px !important; }}
            .mobile-font-size {{ font-size: 16px !important; }}
            .button {{ width: 100% !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: Arial, Helvetica, sans-serif;">

    <!-- Preview Text (hidden but shows in email preview) -->
    <div style="display: none; max-height: 0px; overflow: hidden;">
        Welcome to {company_name}! We're excited to have you on board.
    </div>

    <!-- Email Container -->
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f9fafb;">
        <tr>
            <td style="padding: 40px 20px;">

                <!-- Main Email Table -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" class="email-container" style="margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

                    <!-- Header -->
                    <tr>
                        <td style="padding: 30px 40px; text-align: center; border-bottom: 1px solid #e5e7eb;">
                            <h1 style="margin: 0; color: #1e3a8a; font-size: 24px; font-weight: bold;">
                                Welcome to {company_name}
                            </h1>
                        </td>
                    </tr>

                    <!-- Body Content -->
                    <tr>
                        <td style="padding: 40px;" class="mobile-padding">

                            <!-- Greeting -->
                            <p style="margin: 0 0 20px 0; color: #1f2937; font-size: 16px; line-height: 1.6;">
                                Hello {first_name},
                            </p>

                            <!-- Main Message -->
                            <p style="margin: 0 0 20px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                                Thank you for signing up with us on {creation_date}. We are thrilled to have you join our community. Our platform is designed to help you manage your practice efficiently and effectively.
                            </p>

                            <!-- Call-to-Action Button -->
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 30px 0;">
                                <tr>
                                    <td style="text-align: center;">
                                        <a href="{dashboard_url}" style="display: inline-block; padding: 14px 40px; background-color: #3b82f6; color: #ffffff; text-decoration: none; border-radius: 6px; font-size: 16px; font-weight: 600; min-width: 200px;">
                                            Go to Dashboard
                                        </a>
                                    </td>
                                </tr>
                            </table>

                            <!-- Additional Information -->
                            <p style="margin: 20px 0 0 0; color: #6b7280; font-size: 14px; line-height: 1.6;">
                                If you have any questions or need assistance, feel free to reach out to our support team.
                            </p>

                        </td>
                    </tr>

                    <!-- Support Section -->
                    <tr>
                        <td style="padding: 30px 40px; background-color: #f9fafb; border-top: 1px solid #e5e7eb;">
                            <p style="margin: 0 0 10px 0; color: #6b7280; font-size: 14px; line-height: 1.5;">
                                Questions or need assistance?
                            </p>
                            <p style="margin: 0; color: #6b7280; font-size: 14px; line-height: 1.5;">
                                Contact our support team at
                                <a href="mailto:{support_email}" style="color: #3b82f6; text-decoration: none;">{support_email}</a>
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding: 30px 40px; text-align: center; border-top: 1px solid #e5e7eb;">
                            <p style="margin: 0 0 10px 0; color: #9ca3af; font-size: 13px; line-height: 1.5;">
                                © {current_year} {company_name}. All rights reserved.
                            </p>
                            <p style="margin: 0 0 15px 0; color: #9ca3af; font-size: 12px; line-height: 1.5;">
                                {company_address}
                            </p>
                            <p style="margin: 0; color: #9ca3af; font-size: 12px;">
                                <a href="{privacy_policy_url}" style="color: #9ca3af; text-decoration: underline;">Privacy Policy</a> |
                                <a href="{terms_url}" style="color: #9ca3af; text-decoration: underline;">Terms of Service</a>
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
        email_sent = send_email(email, subject, message, html_message, from_name="Ross.Ai")

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
    terms_url: str = COMMON_TERMS_URL
) -> bool:
    """
    Send a password change success email to the user.

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

This is a confirmation that your password for {company_name} has been successfully updated.

If you made this change, no further action is needed.

If you did not update your password, please contact us immediately for assistance.

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
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.04);">

    <div style="padding:30px; text-align:center; border-bottom:1px solid #eee;">
      <h2 style="margin:0; color:#1f3a93;">Password Updated Successfully</h2>
    </div>
    <div style="padding:30px; font-size:15px; color:#444; line-height:1.6;">
      <p>Hello {name},</p>
      <p>This is a confirmation that your password for <strong>{company_name}</strong> has been successfully updated.</p>
      <p>If you made this change, no further action is needed.</p>
      <p>If you did <strong>not</strong> update your password, please contact us immediately for assistance.</p>
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
        email_sent = send_email(email, subject, message, html_message, from_name="Ross.Ai")

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
    terms_url: str = COMMON_TERMS_URL
) -> bool:
    """
    Send a password reset success email to the user.

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

If you didn't request a password reset, your account may be at risk. Please reach out to us immediately.

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
  <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.04);">

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
        email_sent = send_email(email, subject, message, html_message, from_name="Ross.Ai")

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
    terms_url: str = COMMON_TERMS_URL
) -> bool:
    """
    Send a verification code (OTP) email to the user.

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
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fb; padding: 40px 0;">
      <tr>
        <td align="center">
          <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 8px rgba(0,0,0,0.05);">
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
                  Use the following One-Time Password (OTP) to verify your email or complete your signup:
                </p>
                <div style="margin: 30px 0;">
                  <div style="display: inline-block; background-color: #f3f4f6; padding: 16px 40px; border-radius: 8px; font-size: 28px; font-weight: bold; letter-spacing: 6px; color: #1d4ed8;">
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
              <td style="background-color: #f3f4f6; text-align: center; padding: 20px; font-size: 13px; color: #6b7280;">
                <p style="margin: 0;">&copy; {current_year} {company_name}. All rights reserved.</p>
                <p style="margin: 4px 0;">{company_address}</p>
                <p style="margin: 0;">
                  <a href="{privacy_policy_url}" style="color: #6b7280; text-decoration: none;">Privacy Policy</a> |
                  <a href="{terms_url}" style="color: #6b7280; text-decoration: none;">Terms of Service</a>
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
        email_sent = send_email(email, subject, message, html_message, from_name="Ross.Ai")

        if email_sent:
            logger.info("Verification code email sent successfully to %s", email)
            return True
        logger.error("Failed to send verification code email to %s", email)
        return False

    except Exception as error:
        logger.error("Error sending verification code email: %s", str(error))
        return False
