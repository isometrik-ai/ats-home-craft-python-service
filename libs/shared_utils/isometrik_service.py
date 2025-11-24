"""
Isometrik Service Module

This module provides integration with Isometrik API for application management.
Handles CRUD operations for Isometrik applications.

Author: AI Assistant
Date: 2024-12-24
Last Updated: 2024-12-24

Operations Covered:
- Create Isometrik application
- Update Isometrik application (future)
- Delete Isometrik application (future)
- Get Isometrik application (future)
"""

import os
from typing import Dict, Any, Optional
import httpx
from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("isometrik_service")

# Environment variables
ISOMETRIK_API_URL = os.getenv("ISOMETRIK_API_URL", "https://admin-apis.isometrik.io")
ISOMETRIK_CLIENT_NAME = os.getenv("ISOMETRIK_CLIENT_NAME", "691ad27c348f70f518ee0053")
ISOMETRIK_REGION_ID = os.getenv("ISOMETRIK_REGION_ID", "507f1f77bcf86cd799439011")
ISOMETRIK_AUTH_TOKEN = os.getenv("ISOMETRIK_AUTH_TOKEN", "aXNvbWV0cmlrOjFZVXBDYlJEblU4MzBISA==")


def get_isometrik_data_from_settings(organization_settings: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Extract Isometrik data from organization settings.
    
    Args:
        organization_settings (Optional[Dict[str, Any]]): Organization settings dictionary
        
    Returns:
        Optional[Dict[str, Any]]: Isometrik data if found, None otherwise
    """
    if not organization_settings:
        return None
    
    return organization_settings.get("isometrik")


async def create_isometrik_application(
    organization_name: str,
    organization_id: str,
    product_types: Optional[list] = None,
    plan: str = "basic"
) -> Dict[str, Any]:
    """
    Create a new Isometrik application for an organization.

    Args:
        organization_name (str): Name of the organization
        organization_id (str): Organization ID (used as clientName)
        product_types (Optional[list]): List of product types (default: ["chat", "video"])
        plan (str): Plan type (default: "basic")

    Returns:
        Dict[str, Any]: Response from Isometrik API containing application details

    Raises:
        Exception: If API call fails
    """
    try:
        # Use organization_id as clientName, or fallback to env variable
        client_name = organization_id or ISOMETRIK_CLIENT_NAME
        
        # Default product types
        if product_types is None:
            product_types = ["chat", "video"]

        # Prepare request payload
        payload = {
            "clientName": client_name,
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
                "Successfully created Isometrik application for organization: %s (clientName: %s)",
                organization_name,
                client_name
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


async def update_isometrik_application(
    organization_id: str,
    organization_name: Optional[str] = None,
    application_id: Optional[str] = None,
    product_types: Optional[list] = None,
    plan: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update an existing Isometrik application.

    Args:
        organization_id (str): Organization ID (used as accountId)
        organization_name (Optional[str]): Updated organization name (only updates name if provided)
        application_id (Optional[str]): Isometrik application ID (if not provided, uses organization_id)
        product_types (Optional[list]): Updated product types
        plan (Optional[str]): Updated plan type

    Returns:
        Dict[str, Any]: Response from Isometrik API

    Raises:
        Exception: If API call fails
    """
    try:
        # Use organization_id as application_id if not provided
        app_id = application_id or organization_id
        account_id = organization_id

        # Build payload with only provided fields
        payload = {
            "accountId": account_id,
            "applicationId": app_id
        }

        # Only add fields that are provided
        if organization_name is not None:
            payload["name"] = organization_name
        
        if product_types is not None:
            payload["productType"] = product_types
        
        if plan is not None:
            payload["plan"] = plan
        
        # Always include regionId (required by API)
        payload["regionId"] = ISOMETRIK_REGION_ID

        # Prepare headers - using Basic auth (same as create)
        # Note: If Isometrik requires Bearer token for update, set ISOMETRIK_UPDATE_AUTH_TOKEN env var
        auth_token = os.getenv("ISOMETRIK_UPDATE_AUTH_TOKEN", ISOMETRIK_AUTH_TOKEN)
        auth_type = "Bearer" if os.getenv("ISOMETRIK_UPDATE_AUTH_TOKEN") else "Basic"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"{auth_type} {auth_token}"
        }

        # Make API call - PATCH request
        url = f"{ISOMETRIK_API_URL}/v1/application"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            logger.info(
                "Successfully updated Isometrik application for organization: %s (applicationId: %s)",
                organization_id,
                app_id
            )
            return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "Isometrik API error updating application - Organization: %s, Status: %s, Response: %s",
            organization_id,
            e.response.status_code,
            e.response.text
        )
        raise Exception(f"Isometrik API error: {e.response.status_code} - {e.response.text}") from e
    except httpx.RequestError as e:
        logger.error(
            "Network error calling Isometrik API - Organization: %s, Error: %s",
            organization_id,
            str(e)
        )
        raise Exception(f"Failed to connect to Isometrik API: {str(e)}") from e
    except Exception as e:
        logger.error(
            "Unexpected error updating Isometrik application - Organization: %s, Error: %s",
            organization_id,
            str(e),
            exc_info=True
        )
        raise


async def delete_isometrik_application(application_id: str) -> bool:
    """
    Delete an Isometrik application.

    Args:
        application_id (str): Isometrik application ID

    Returns:
        bool: True if deletion was successful

    Raises:
        Exception: If API call fails
    """
    # TODO: Implement delete functionality
    raise NotImplementedError("Delete Isometrik application not yet implemented")


async def get_isometrik_application(application_id: str) -> Dict[str, Any]:
    """
    Get Isometrik application details.

    Args:
        application_id (str): Isometrik application ID

    Returns:
        Dict[str, Any]: Application details from Isometrik API

    Raises:
        Exception: If API call fails
    """
    # TODO: Implement get functionality
    raise NotImplementedError("Get Isometrik application not yet implemented")


async def create_isometrik_chat_user(
    organization_id: str,
    first_name: Optional[str],
    last_name: Optional[str],
    email: str,
    isometrik_credentials: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create a new Isometrik chat user for an organization.

    Args:
        organization_id (str): Organization ID
        first_name (Optional[str]): User's first name
        last_name (Optional[str]): User's last name
        email (str): User's email address
        isometrik_credentials (Dict[str, Any]): Isometrik credentials from settings
            Should contain: userSecret, licenseKey, appSecret

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

        # Generate password: first 13 chars of org_id + 'Ai$'
        # org_id format is UUID like "88fe7112-60f3-4381-b1ca-aecc139893cf"
        # Take first 13 chars: "88fe7112-60f3"
        org_id_prefix = organization_id[:13] if len(organization_id) >= 13 else organization_id
        password = f"{org_id_prefix}Ai$"

        # Prepare request payload
        payload = {
            "userName": user_name,
            "userIdentifier": organization_id,
            "password": password,
            "metaData": {
                "user_type": "ADMIN",
                "org_id": organization_id
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
            logger.info(
                "Successfully created Isometrik chat user for organization: %s (userId: %s)",
                organization_id,
                result.get("data", {}).get("userId") if isinstance(result, dict) and result.get("data") else None
            )
            return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "Isometrik API error creating chat user - Organization: %s, Status: %s, Response: %s",
            organization_id,
            e.response.status_code,
            e.response.text
        )
        raise Exception(f"Isometrik API error: {e.response.status_code} - {e.response.text}") from e
    except httpx.RequestError as e:
        logger.error(
            "Network error calling Isometrik API - Organization: %s, Error: %s",
            organization_id,
            str(e)
        )
        raise Exception(f"Failed to connect to Isometrik API: {str(e)}") from e
    except Exception as e:
        logger.error(
            "Unexpected error creating Isometrik chat user - Organization: %s, Error: %s",
            organization_id,
            str(e),
            exc_info=True
        )
        raise

