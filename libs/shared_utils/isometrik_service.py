"""
Isometrik Service Module

This module provides integration with Isometrik API for application creation.
Handles creation of Isometrik applications.

Author: AI Assistant
Date: 2024-12-24
Last Updated: 2024-12-24

Operations Covered:
- Create Isometrik application
"""

import os
import json
from typing import Dict, Any, Optional
import httpx
from fastapi import HTTPException

from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("isometrik_service")

class IsometrikAPIError(Exception):
    """Exception raised for Isometrik API errors (4xx/5xx status codes)."""
    
    def __init__(self, message: str, status_code: int = None, response_text: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class IsometrikConnectionError(Exception):
    """Exception raised for Isometrik API connection/network errors."""
    
    def __init__(self, message: str, original_error: Exception = None):
        super().__init__(message)
        self.original_error = original_error

# Environment variables
ISOMETRIK_ENABLED = os.getenv("ISOMETRIK_ENABLED", "false").lower() in ("true", "1", "yes")
ISOMETRIK_ADMIN_API_URL = os.getenv("ISOMETRIK_ADMIN_API_URL", "https://admin-apis.isometrik.io")
ISOMETRIK_API_URL = os.getenv("ISOMETRIK_API_URL", "https://api.isometrik.ai")
ISOMETRIK_CLIENT_NAME = os.getenv("ISOMETRIK_CLIENT_NAME", "691ad27c348f70f518ee0053")
ISOMETRIK_REGION_ID = os.getenv("ISOMETRIK_REGION_ID", "507f1f77bcf86cd799439011")
ISOMETRIK_AUTH_TOKEN = os.getenv("ISOMETRIK_AUTH_TOKEN", "aXNvbWV0cmlrOjFZVXBDYlJEblU4MzBISA==")


def is_isometrik_enabled() -> bool:
    """
    Check if Isometrik integration is enabled via environment variable.
    
    Returns:
        bool: True if Isometrik is enabled, False otherwise
    """
    return ISOMETRIK_ENABLED


def get_isometrik_data_from_settings(organization_settings: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Extract Isometrik application data from organization settings.
    
    Args:
        organization_settings (Optional[Dict[str, Any]]): Organization settings dictionary
        
    Returns:
        Optional[Dict[str, Any]]: Isometrik application data if found, None otherwise
    """
    if not organization_settings:
        return None
    
    # Get application details from new structure (now contains data directly)
    application_details = organization_settings.get("isometrik_application_details")
    if application_details and isinstance(application_details, dict):
        # Return the data directly (it's already the data portion)
        return application_details
    
    # Fallback to old structure for backward compatibility
    return organization_settings.get("isometrik")


async def create_isometrik_application(
    organization_name: str,
    product_types: Optional[list] = None,
    plan: str = "basic"
) -> Dict[str, Any]:
    """
    Create a new Isometrik application for an organization.

    Args:
        organization_name (str): Name of the organization
        product_types (Optional[list]): List of product types (default: ["chat", "video"])
        plan (str): Plan type (default: "basic")

    Returns:
        Dict[str, Any]: Response from Isometrik API containing application details

    Raises:
        IsometrikAPIError: If API call returns 4xx/5xx status code
        IsometrikConnectionError: If network/connection error occurs
        Exception: For unexpected errors
    """
    try:
        # Default product types
        if product_types is None:
            product_types = ["chat", "video"]

        # Prepare request payload
        payload = {
            "clientName": ISOMETRIK_CLIENT_NAME,
            "name": organization_name,
            "productType": product_types,
            "regionId": ISOMETRIK_REGION_ID,
            "plan": plan
        }

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {ISOMETRIK_AUTH_TOKEN}"
        }

        # Make API call
        url = f"{ISOMETRIK_ADMIN_API_URL}/v1/intr/application"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            return result

    except httpx.HTTPStatusError as e:
        raise IsometrikAPIError(
            f"Isometrik API error: {e.response.status_code} - {e.response.text}",
            status_code=e.response.status_code,
            response_text=e.response.text
        ) from e
    except httpx.RequestError as e:
        raise IsometrikConnectionError(
            f"Failed to connect to Isometrik API: {str(e)}",
            original_error=e
        ) from e


async def create_isometrik_user(
    user_id: str,
    first_name: Optional[str],
    last_name: Optional[str],
    email: str,
    isometrik_credentials: Dict[str, Any],
    organization_id: str,
    role: str,
    avatar_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new Isometrik chat user for an organization.

    Args:
        user_id (str): User ID
        first_name (Optional[str]): User's first name
        last_name (Optional[str]): User's last name
        email (str): User's email address
        isometrik_credentials (Dict[str, Any]): Isometrik credentials from settings
            Should contain: userSecret, licenseKey, appSecret
        organization_id (str): Organization ID
        role (str): Role of the user
        avatar_url (Optional[str]): URL to user's avatar
    Returns:
        Dict[str, Any]: Response from Isometrik API containing user details

    Raises:
        Exception: If API call fails
    """
    try:
        # Build user name from first and last name
        user_name_parts = []
        if first_name:
            user_name_parts.append(first_name)
        if last_name:
            user_name_parts.append(last_name)
        user_name = " ".join(user_name_parts) if user_name_parts else email.split("@")[0]

        # Generate password: first 12 chars of user_id (without hyphens) + 'Ai$'
        # user_id format is UUID like "88fe7112-60f3-4381-b1ca-aecc139893cf"
        # Remove hyphens and take first 12 chars: "88fe711260f3"
        password = user_id.replace("-", "")[:12] + "Ai$"

        # Prepare request payload
        payload = {
            "userName": user_name,
            "userIdentifier": user_id,
            "userProfileImageUrl": avatar_url if avatar_url else "https://example.com/default-avatar.jpg",
            "password": password,
            "metaData": {
                "user_id": user_id,
                "role": role,
                "organization_id": organization_id,
            },
            "messageNotificationEmail": email,
            "emailNotifications": True,
            "clubEmailNotifications": False
        }

        # Prepare headers - use credentials from stored Isometrik data
        headers = {
            "Content-Type": "application/json",
            "userSecret": isometrik_credentials.get("userSecret", ""),
            "licenseKey": isometrik_credentials.get("licenseKey", ""),
            "appSecret": isometrik_credentials.get("appSecret", "")
        }

        # Make API call
        url = f"{ISOMETRIK_API_URL}/chat/user"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()  # Raises for 4xx/5xx status codes
            
            result = response.json()
            return result

    except httpx.HTTPStatusError as e:
        # Try to extract actual error message from API response
        error_detail = e.response.text
        try:
            # Try to parse JSON response to extract meaningful error message
            error_json = e.response.json()
            if isinstance(error_json, dict):
                # Look for common error message fields
                error_detail = (
                    error_json.get("message") or
                    error_json.get("error") or
                    error_json.get("detail") or
                    error_json.get("errorMessage") or
                    str(error_json)
                )
        except (json.JSONDecodeError, ValueError, AttributeError):
            # If response is not JSON, use the text as-is
            pass
        
        # Convert to HTTPException to ensure error message is returned in API response
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Isometrik API error: {error_detail}"
        ) from e
    except httpx.RequestError as e:
        # Convert connection errors to HTTPException with actual error message
        error_message = str(e)
        logger.error("Failed to connect to Isometrik API: %s", error_message)
        # Include the actual error message in the response
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to Isometrik API: {error_message}"
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error creating Isometrik chat user: {str(e)}"
        ) from e