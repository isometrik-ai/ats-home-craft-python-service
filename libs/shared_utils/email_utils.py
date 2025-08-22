"""
Email Utilities Module
This module provides shared email functionality for sending emails via Supabase Edge Functions.
"""

import os
import requests
import logging

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
            
        response = requests.post(
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
            logger.info("Email sent successfully to %s", email)
            return True
        logger.error("Failed to send email: %s", response.text)
        return False
    except requests.RequestException as error:
        logger.error("Error sending email: %s", str(error))
        return False 