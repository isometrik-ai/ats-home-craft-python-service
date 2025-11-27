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
from typing import Dict, Any, Optional
import httpx
from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("isometrik_service")

# Environment variables
ISOMETRIK_ENABLED = os.getenv("ISOMETRIK_ENABLED", "false").lower() in ("true", "1", "yes")
ISOMETRIK_API_URL = os.getenv("ISOMETRIK_API_URL", "https://admin-apis.isometrik.io")
ISOMETRIK_CHAT_API_URL = os.getenv("ISOMETRIK_CHAT_API_URL", "https://apis.isometrik.io")
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
        Exception: If API call fails
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
        url = f"{ISOMETRIK_API_URL}/v1/intr/application"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            logger.info(
                "Successfully created Isometrik application for organization: %s",
                organization_name
            )
            return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "Isometrik API error creating application - Organization: %s, Status: %s, Response: %s",
            organization_name,
            e.response.status_code,
            e.response.text
        )
        raise Exception(f"Isometrik API error: {e.response.status_code} - {e.response.text}") from e
    except httpx.RequestError as e:
        logger.error(
            "Network error calling Isometrik API - Organization: %s, Error: %s",
            organization_name,
            str(e)
        )
        raise Exception(f"Failed to connect to Isometrik API: {str(e)}") from e
    except Exception as e:
        logger.error(
            "Unexpected error creating Isometrik application - Organization: %s, Error: %s",
            organization_name,
            str(e),
            exc_info=True
        )
        raise

