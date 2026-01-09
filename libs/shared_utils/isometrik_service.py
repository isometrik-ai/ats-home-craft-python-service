"""Isometrik service module
This module provides integration with Isometrik API for application creation.
Handles creation of Isometrik applications.
"""

from typing import Any

import httpx

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    InternalServerErrorException,
    RateLimitExceededException,
    ServiceUnavailableException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("isometrik_service")


def get_isometrik_data_from_settings(
    organization_settings: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract Isometrik application data from organization settings."""
    if not organization_settings:
        return None

    application_details = organization_settings.get("isometrik_application_details")
    if application_details and isinstance(application_details, dict):
        return application_details

    return organization_settings.get("isometrik")


async def create_isometrik_application(
    organization_name: str, product_types: list[str] | None = None, plan: str = "basic"
) -> dict[str, Any]:
    """Create a new Isometrik application for an organization.

    Args:
        organization_name (str): Name of the organization
        product_types (Optional[list]): List of product types (default: ["chat", "video"])
        plan (str): Plan type (default: "basic")

    Returns:
        dict[str, Any]: Response from Isometrik API containing application details

    Raises:
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        # Default product types
        if product_types is None:
            product_types = ["chat", "video"]

        # Prepare request payload
        payload = {
            "clientName": shared_settings.isometrik.client_name,
            "name": organization_name,
            "productType": product_types,
            "regionId": shared_settings.isometrik.region_id,
            "plan": plan,
        }

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {shared_settings.isometrik.auth_token}",
        }

        # Make API call
        url = f"{shared_settings.isometrik.admin_api_url}/v1/intr/application"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            try:
                return response.json()
            except ValueError as e:
                logger.error("Invalid JSON in Isometrik API response: %s", response.text)
                raise ServiceUnavailableException(
                    message_key="errors.external_service_unavailable",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                ) from e

    except httpx.HTTPStatusError as e:
        logger.error("Isometrik API status code error: %s", str(e))
        status_code = e.response.status_code

        if 400 <= status_code < 500:
            raise BadRequestException(
                message_key="errors.external_api_bad_request",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_BAD_REQUEST,
            ) from e

        if status_code == 429:
            raise RateLimitExceededException(
                message_key="errors.external_api_rate_limited",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_RATE_LIMIT,
            ) from e

        # 5xx errors
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e
    except httpx.RequestError as e:
        logger.error("Isometrik API connection error: %s", str(e))
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e
    except Exception as e:
        logger.error("Unexpected error creating Isometrik application: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e


async def create_isometrik_user(
    user_id: str,
    first_name: str | None,
    last_name: str | None,
    email: str,
    isometrik_credentials: dict[str, Any],
    organization_id: str,
    role: str,
    avatar_url: str | None = "https://example.com/default-avatar.jpg",
) -> dict[str, Any]:
    """Create a new Isometrik user for an organization.

    Args:
        user_id (str): User ID
        first_name (str | None): User's first name
        last_name (str | None): User's last name
        email (str): User's email address
        isometrik_credentials (dict[str, Any]): Isometrik credentials from settings
            Should contain: userSecret, licenseKey, appSecret
        organization_id (str): Organization ID
        role (str): Role of the user
        avatar_url (str | None): URL to user's avatar
    Returns:
        dict[str, Any]: Response from Isometrik API containing user details

    Raises:
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        user_name = " ".join(filter(None, [first_name, last_name])) or email.split("@")[0]

        password = user_id.replace("-", "")[:12] + "Ai$"

        payload = {
            "userName": user_name,
            "userIdentifier": str(user_id),
            "userProfileImageUrl": avatar_url,
            "password": password,
            "metaData": {
                "user_id": str(user_id),
                "role": role,
                "organization_id": str(organization_id),
            },
            "messageNotificationEmail": email,
            "emailNotifications": True,
            "clubEmailNotifications": False,
        }

        headers = {
            "Content-Type": "application/json",
            "userSecret": isometrik_credentials.get("userSecret", ""),
            "licenseKey": isometrik_credentials.get("licenseKey", ""),
            "appSecret": isometrik_credentials.get("appSecret", ""),
        }

        url = f"{shared_settings.isometrik.api_url}/chat/user"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            try:
                return response.json()
            except ValueError as e:
                logger.error("Invalid JSON in Isometrik API response: %s", response.text)
                raise ServiceUnavailableException(
                    message_key="errors.external_service_unavailable",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                ) from e

    except httpx.HTTPStatusError as e:
        logger.error("Isometrik API status code error: %s", str(e))
        status_code = e.response.status_code
        if 400 <= status_code < 500:
            raise BadRequestException(
                message_key="errors.external_api_bad_request",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_BAD_REQUEST,
            ) from e

        if status_code == 429:
            raise RateLimitExceededException(
                message_key="errors.external_api_rate_limited",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_RATE_LIMIT,
            ) from e

        # 5xx errors
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e
    except httpx.RequestError as e:
        logger.error("Isometrik API connection error: %s", str(e))
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e
    except Exception as e:
        logger.error("Unexpected error creating Isometrik chat user: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e


async def login_to_isometrik(
    user_id: str,
    isometrik_credentials: dict[str, Any],
) -> dict[str, Any]:
    """Login to Isometrik.

    Args:
        user_id (str): User ID
        isometrik_credentials (dict[str, Any]): Isometrik credentials from settings
            Should contain: userSecret, licenseKey, appSecret
    Returns:
        dict[str, Any]: Response from Isometrik API containing login details

    Raises:
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        password = user_id.replace("-", "")[:12] + "Ai$"

        payload = {
            "userIdentifier": str(user_id),
            "password": password,
        }

        headers = {
            "Content-Type": "application/json",
            "userSecret": isometrik_credentials.get("userSecret", ""),
            "licenseKey": isometrik_credentials.get("licenseKey", ""),
            "appSecret": isometrik_credentials.get("appSecret", ""),
        }

        url = f"{shared_settings.isometrik.api_url}/chat/user/authenticate"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            try:
                return response.json()
            except ValueError as e:
                logger.error("Invalid JSON in Isometrik API response: %s", response.text)
                raise ServiceUnavailableException(
                    message_key="errors.external_service_unavailable",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                ) from e

    except httpx.HTTPStatusError as e:
        logger.error("Isometrik API status code error: %s", str(e))
        status_code = e.response.status_code
        if 400 <= status_code < 500:
            raise BadRequestException(
                message_key="errors.external_api_bad_request",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_BAD_REQUEST,
            ) from e

        if status_code == 429:
            raise RateLimitExceededException(
                message_key="errors.external_api_rate_limited",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_RATE_LIMIT,
            ) from e

        # 5xx errors
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e
    except httpx.RequestError as e:
        logger.error("Isometrik API connection error: %s", str(e))
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e
    except Exception as e:
        logger.error("Unexpected error creating Isometrik chat user: %s", str(e))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from e
