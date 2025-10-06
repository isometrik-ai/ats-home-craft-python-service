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


def send_email(email: str, subject: str, message: str, html: str = None) -> bool:
    """
    Send an email using Supabase Edge Function with Resend.

    Args:
        email (str): Recipient's email address
        subject (str): Email subject
        message (str): Email message content (plain text)
        html (str, optional): HTML version of the email

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
        print(f"Sending email to {email} with subject {subject} and message {message}")
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
        else:
            logger.error("Failed to send password reset confirmation email to %s", email)
            return False

    except Exception as error:
        logger.error("Error sending password reset confirmation email: %s", str(error))
        return False
